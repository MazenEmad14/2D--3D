"""
app/parsers/mask_to_ir.py

Converts a raw segmentation mask (H×W uint8 array, values 0-3) into an
:class:`~app.schemas.ir_schema.IRFloorPlan`.

Pipeline
--------
1. **Letterbox un-mapping** — contour coordinates in the 512×512 mask space
   are converted back to the *original image's* pixel space.

2. **Per-class contour extraction** (mirrors the reference repo's
   ``extract_polygons.py``):
   a. Binary mask for the class.
   b. 3×3 morphological closing — seals hairline breaks without destroying
      ~5-pixel door openings.
   c. ``cv2.findContours(RETR_CCOMP)`` — top-level outer rings + one level
      of hole contours (a wall ring with a door cut-out).
   d. ``cv2.approxPolyDP(epsilon=APPROX_EPSILON_PX)`` — collapses pixel
      staircases on axis-aligned walls to 2-4 vertices per straight segment.
   e. Drop contours with area < ``MIN_POLYGON_AREA_PX`` (model speckle).

3. **Wall centerline via minAreaRect** — for each wall contour:
   ``cv2.minAreaRect`` returns the minimum-area rotated bounding box.
   - Thickness = shorter side of the rectangle (metres).
   - Centerline = the longer axis, from midpoint of one short side to
     midpoint of the opposite short side.
   *Rationale*: simpler and faster than skeletonization; handles both
   axis-aligned and diagonal walls; produces clean start/end/thickness
   suitable for ``Wall`` objects directly.

4. **Opening extraction** — door/window contours are associated with their
   nearest wall centre-line segment by perpendicular projection (same
   algorithm as the DXF parser).

5. **Floor polygon** — derived from the convex hull of the "floor" class
   mask's largest contour, remapped to original image space.  Falls back
   to the axis-aligned bounding box of all wall endpoints if the floor mask
   is empty.

6. **Scale estimation** (pixels → metres):
   - Default: ``DEFAULT_SCALE_M_PER_PX = 0.01 m/px`` (assumes a typical
     residential room is 5 m wide ≈ 500 px at 512×512).
     ``metadata.scale_confidence = "estimated"``.
   - Optional: if ``known_door_width_m`` is provided, the average detected
     door contour width (bounding-box long axis) is used to compute
     ``scale = known_door_width_m / avg_door_px_width``.
     ``metadata.scale_confidence = "high"``.

Known limitations
-----------------
- Walls thinner than ~3 px in the mask may be merged by morphological closing
  or dropped by the area filter.
- The scale estimate (0.01 m/px) is a heuristic — real accuracy requires a
  reference object or explicit user calibration.
- The minimum-area rectangle centerline is approximate for L-shaped or
  T-shaped wall regions; these should be refined in a future phase using
  proper skeleton extraction.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Literal

import cv2
import numpy as np
import skimage.morphology

from app.schemas.ir_schema import IRFloorPlan, Metadata, Opening, Point2D, Wall

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Class IDs (must match floorplan_segmenter.py and reference repo labels.py)
# ---------------------------------------------------------------------------

CLASS_FLOOR: int = 0
CLASS_WALL: int = 1
CLASS_DOOR: int = 2
CLASS_WINDOW: int = 3

# ---------------------------------------------------------------------------
# Polygon extraction constants (from reference repo's extract_polygons.py)
# ---------------------------------------------------------------------------

#: Closing kernel size (px) — seals 1-px gaps without merging thin openings.
CLOSING_KERNEL_PX: int = 3

#: Douglas-Peucker epsilon (px) — kills staircase artefacts on straight edges.
APPROX_EPSILON_PX: float = 1.5

#: Minimum contour area (px²) — drops model speckle smaller than a real door.
MIN_POLYGON_AREA_PX: float = 30.0

# ---------------------------------------------------------------------------
# Scale estimation
# ---------------------------------------------------------------------------

#: Default pixel → metre scale factor.
#: Assumption: a typical residential room is ~5 m wide; at 512 px input that
#: is ~500 px → 0.01 m/px.  This is a coarse estimate — use
#: ``known_door_width_m`` for better accuracy.
DEFAULT_SCALE_M_PER_PX: float = 0.01

#: Default wall height in metres (model produces no height information).
DEFAULT_WALL_HEIGHT_M: float = 3.0

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _mask_to_canvas_coords(
    mask_coords: np.ndarray,
    letterbox_info: dict,
) -> np.ndarray:
    """
    Map contour coordinates from the 512×512 mask space back to the original
    image's pixel space, **with a Y-axis flip** so that world Y increases
    upward (mathematical convention) rather than downward (image/OpenCV
    convention).

    OpenCV row 0 is the *top* of the image; in our world coordinate system we
    want row 0 to correspond to the *largest* Y value so the resulting floor-
    plan is not vertically mirrored in the 3D scene.

    Parameters
    ----------
    mask_coords:
        Array of shape (N, 2) with (col, row) pairs in 512×512 mask space.
    letterbox_info:
        Dict returned by :func:`~app.parsers.image_preprocessing.preprocess_image`.
        Must contain ``orig_h`` (original image height in pixels).

    Returns
    -------
    np.ndarray, shape (N, 2) — (x, y) coordinates where x = column pixels
    and y is **flipped** so that y increases upward in the original image
    space (y = orig_h - row_pixels).
    """
    pad_left = letterbox_info["pad_left"]
    pad_top  = letterbox_info["pad_top"]
    scale    = letterbox_info["scale"]
    orig_h   = letterbox_info["orig_h"]

    # Subtract padding, then un-scale  →  (col_orig, row_orig)
    orig = (mask_coords - np.array([pad_left, pad_top], dtype=np.float32)) / scale

    # Flip Y: image row 0 (top) → world Y = orig_h, image row orig_h (bottom) → world Y = 0.
    # This is the single Y-inversion in the full pipeline; builder.py uses IR coordinates
    # directly and applies no further flip.
    orig[:, 1] = orig_h - orig[:, 1]

    return orig


def _extract_contours_for_class(
    mask: np.ndarray,
    class_id: int,
) -> list[np.ndarray]:
    """
    Extract simplified contours for *class_id* from *mask*.

    Returns a list of contour arrays (each shape (N, 1, 2) int32), ordered
    by descending area, with speckle removed.
    """
    binary = (mask == class_id).astype(np.uint8)
    if binary.sum() == 0:
        return []

    kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (CLOSING_KERNEL_PX, CLOSING_KERNEL_PX)
    )
    closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

    contours, hierarchy = cv2.findContours(
        closed, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE
    )
    if not contours or hierarchy is None:
        return []

    hierarchy = hierarchy[0]  # (N, 4)
    result: list[np.ndarray] = []
    for i, (_, _, _, parent) in enumerate(hierarchy):
        if parent != -1:
            continue  # skip holes — we only want outer rings here
        if cv2.contourArea(contours[i]) < MIN_POLYGON_AREA_PX:
            continue
        simplified = cv2.approxPolyDP(contours[i], APPROX_EPSILON_PX, closed=True)

        # Proposal A: degenerate check — reference extract_polygons.py line 115.
        # A contour reduced to < 3 vertices by approxPolyDP cannot form a polygon.
        if len(simplified) < 3:
            logger.debug("Dropping degenerate contour (vertices=%d) for class %d", len(simplified), class_id)
            continue

        # Proposal B: post-simplification area re-check.
        # The reference (extract_polygons.py line 112) applies MIN_POLYGON_AREA_PX to the
        # raw contour. We add a secondary guard on the *simplified* result, but we use a
        # much lower floor (5 px²) rather than 30 px²: on noisy real-world images,
        # approxPolyDP can shrink a valid small opening below 30 px² even though the raw
        # contour passed. 5 px² catches only truly degenerate near-zero triangles.
        MIN_SIMPLIFIED_AREA_PX = 5.0
        if cv2.contourArea(simplified) < MIN_SIMPLIFIED_AREA_PX:
            logger.debug("Dropping post-simplification speckle (area=%.1f px²) for class %d",
                         cv2.contourArea(simplified), class_id)
            continue

        result.append(simplified)

    # Sort largest → smallest
    result.sort(key=cv2.contourArea, reverse=True)
    return result


def _trace_skeleton_graph(skeleton: np.ndarray, min_branch_length: int = 5) -> list[list[tuple[int, int]]]:
    """
    Extracts topological branches from a 1-pixel wide skeleton.
    Uses 8-connectivity to classify pixels into endpoints, paths, and junctions.
    """
    skel_u8 = skeleton.astype(np.uint8)
    kernel = np.array([[1, 1, 1],
                       [1, 0, 1],
                       [1, 1, 1]], dtype=np.uint8)
    
    neighbor_count = cv2.filter2D(skel_u8, -1, kernel, borderType=cv2.BORDER_CONSTANT)
    
    endpoints_mask = (skel_u8 > 0) & (neighbor_count == 1)
    paths_mask = (skel_u8 > 0) & (neighbor_count == 2)
    junctions_mask = (skel_u8 > 0) & (neighbor_count >= 3)
    
    # 1. Junction clustering
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
        junctions_mask.astype(np.uint8), connectivity=8
    )
    junction_nodes = {}  # label -> (x, y)
    junction_pixels_to_label = {}
    
    for label in range(1, num_labels):
        ys, xs = np.where(labels == label)
        cx, cy = centroids[label]
        best_dist = float('inf')
        best_pt = (xs[0], ys[0])
        for x, y in zip(xs, ys):
            d = (x - cx)**2 + (y - cy)**2
            if d < best_dist:
                best_dist = d
                best_pt = (x, y)
        junction_nodes[label] = best_pt
        for x, y in zip(xs, ys):
            junction_pixels_to_label[(x, y)] = label

    def get_neighbors(x, y, mask):
        ns = []
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0: continue
                nx, ny = x + dx, y + dy
                if 0 <= ny < mask.shape[0] and 0 <= nx < mask.shape[1]:
                    if mask[ny, nx]:
                        ns.append((nx, ny))
        return ns

    visited_paths = np.zeros_like(skel_u8, dtype=bool)
    branches = []

    def trace_branch(start_node_pt, start_node_label, first_path_pt):
        branch = [start_node_pt, first_path_pt]
        visited_paths[first_path_pt[1], first_path_pt[0]] = True
        curr_pt = first_path_pt
        end_node_label = None
        
        while True:
            # Look for junction neighbors
            j_neighbors = get_neighbors(curr_pt[0], curr_pt[1], junctions_mask)
            unvisited_j = []
            for jn in j_neighbors:
                lbl = junction_pixels_to_label[jn]
                # Avoid jumping back to the start node's cluster if we just started
                if len(branch) == 2 and start_node_label is not None and lbl == start_node_label:
                    continue
                unvisited_j.append(lbl)
                
            if unvisited_j:
                end_node_label = unvisited_j[0]
                break
                
            # Look for endpoint neighbors
            e_neighbors = get_neighbors(curr_pt[0], curr_pt[1], endpoints_mask)
            e_neighbors = [e for e in e_neighbors if e != start_node_pt]
            if e_neighbors:
                branch.append(e_neighbors[0])
                break
                
            # Move to next path pixel
            p_neighbors = get_neighbors(curr_pt[0], curr_pt[1], paths_mask)
            next_p = None
            for pn in p_neighbors:
                if not visited_paths[pn[1], pn[0]]:
                    next_p = pn
                    break
            
            if next_p:
                branch.append(next_p)
                visited_paths[next_p[1], next_p[0]] = True
                curr_pt = next_p
            else:
                break
                
        return branch, end_node_label

    # 2. Trace from endpoints
    ey, ex = np.where(endpoints_mask > 0)
    for x, y in zip(ex, ey):
        for px, py in get_neighbors(x, y, paths_mask):
            if not visited_paths[py, px]:
                branch_pts, end_j = trace_branch((x, y), None, (px, py))
                branches.append({
                    'points': branch_pts + ([junction_nodes[end_j]] if end_j is not None else []),
                    'start_j': None,
                    'end_j': end_j
                })
        # Check if endpoint connects directly to a junction
        for jx, jy in get_neighbors(x, y, junctions_mask):
            lbl = junction_pixels_to_label[(jx, jy)]
            branches.append({
                'points': [(x, y), junction_nodes[lbl]],
                'start_j': None,
                'end_j': lbl
            })

    # 3. Trace from junctions
    for label, node_pt in junction_nodes.items():
        cluster_ys, cluster_xs = np.where(labels == label)
        for cx, cy in zip(cluster_xs, cluster_ys):
            for px, py in get_neighbors(cx, cy, paths_mask):
                if not visited_paths[py, px]:
                    branch_pts, end_j = trace_branch(node_pt, label, (px, py))
                    branches.append({
                        'points': branch_pts + ([junction_nodes[end_j]] if end_j is not None else []),
                        'start_j': label,
                        'end_j': end_j
                    })
            # Check if this junction connects directly to another junction cluster
            for jx, jy in get_neighbors(cx, cy, junctions_mask):
                other_lbl = junction_pixels_to_label[(jx, jy)]
                if other_lbl > label: # Only add edge once
                    branches.append({
                        'points': [node_pt, junction_nodes[other_lbl]],
                        'start_j': label,
                        'end_j': other_lbl
                    })

    # 4. Find closed loops (no junctions or endpoints)
    py, px = np.where(paths_mask > 0)
    for x, y in zip(px, py):
        if not visited_paths[y, x]:
            curr = (x, y)
            branch = [curr]
            visited_paths[y, x] = True
            
            while True:
                next_p = None
                for nx, ny in get_neighbors(curr[0], curr[1], paths_mask):
                    if not visited_paths[ny, nx]:
                        next_p = (nx, ny)
                        break
                if next_p:
                    branch.append(next_p)
                    visited_paths[next_p[1], next_p[0]] = True
                    curr = next_p
                else:
                    break
            
            branch.append((x, y)) # Close loop
            branches.append({
                'points': branch,
                'start_j': None,
                'end_j': None
            })

    # 5. Short-branch merging (junction-collapse) & length filtering
    # ---------------------------------------------------------------
    # Two-pass approach:
    #   Pass A: build a Union-Find merge_map for junctions separated by a
    #           branch shorter than min_branch_length (these pairs are merged
    #           into a single logical node).
    #   Pass B: remove any branch whose both endpoints collapsed to the same
    #           root, and any stub shorter than min_branch_length.
    # After that, run a topological healing pass so that junctions reduced to
    # degree-2 by the stub removal dissolve — merging the two surviving
    # branches into one continuous branch. This prevents a straight wall from
    # being emitted as many collinear fragments.

    # --- Pass A: Union-Find junction merge for inter-junction micro-segments ---
    merge_map: dict[int, int] = {}

    def _find_root(j: int) -> int:
        while j in merge_map:
            j = merge_map[j]
        return j

    for b in branches:
        if b['start_j'] is not None and b['end_j'] is not None and b['start_j'] != b['end_j']:
            pts = b['points']
            length = sum(
                math.hypot(pts[i+1][0] - pts[i][0], pts[i+1][1] - pts[i][1])
                for i in range(len(pts) - 1)
            )
            if length < min_branch_length:
                sl = _find_root(b['start_j'])
                el = _find_root(b['end_j'])
                if sl != el:
                    merge_map[el] = sl

    # --- Pass B: filter branches (collapsed rings and stubs) ---
    # Produce a normalised list of surviving branch dicts. Each entry keeps
    # its raw pixel path and the *root* junction IDs for both endpoints so the
    # healing pass can reason about the graph topology.
    surviving: list[dict] = []
    for b in branches:
        pts = b['points']
        length = sum(
            math.hypot(pts[i+1][0] - pts[i][0], pts[i+1][1] - pts[i][1])
            for i in range(len(pts) - 1)
        )

        sj = _find_root(b['start_j']) if b['start_j'] is not None else None
        ej = _find_root(b['end_j'])   if b['end_j']   is not None else None

        # Drop branches whose endpoints collapsed into the same node
        if sj is not None and ej is not None and sj == ej:
            continue

        # Drop stubs shorter than threshold
        if length < min_branch_length:
            continue

        # Snap pixel-path endpoints to canonical junction coordinates
        if sj is not None:
            pts[0] = junction_nodes[sj]
        if ej is not None:
            pts[-1] = junction_nodes[ej]

        surviving.append({'points': pts, 'start_j': sj, 'end_j': ej})

    # --- Pass C: topological healing ---
    # Build a per-junction adjacency list: junction_id -> list of indices into
    # `surviving` where that junction appears as an endpoint.
    # A junction reduced to exactly 2 connections by stub removal is no longer
    # a real topological node — dissolve it by concatenating its two branches.
    # Repeat until no more degree-2 junctions exist.

    def _build_adjacency(branch_list: list[dict]) -> dict[int, list[int]]:
        """Return {junction_root: [branch_indices]} for all junction endpoints."""
        adj: dict[int, list[int]] = {}
        for idx, b in enumerate(branch_list):
            for jid in (b['start_j'], b['end_j']):
                if jid is not None:
                    adj.setdefault(jid, []).append(idx)
        return adj

    def _concat_branches(b1: dict, b2: dict, shared_j: int) -> dict:
        """
        Concatenate two branches that share junction *shared_j* into one.
        The shared junction node (degree-2) is dropped from the interior of
        the merged path — it becomes a plain path point, not a junction.
        """
        pts1 = b1['points']
        pts2 = b2['points']

        # Orient pts1 so that shared_j is at its tail (last element).
        if b1['start_j'] == shared_j:
            pts1 = list(reversed(pts1))
            new_start_j = b1['end_j']
        else:
            new_start_j = b1['start_j']

        # Orient pts2 so that shared_j is at its head (first element).
        if b2['end_j'] == shared_j:
            pts2 = list(reversed(pts2))
            new_end_j = b2['start_j']
        else:
            new_end_j = b2['end_j']

        # Drop the duplicated shared-junction pixel at the join point.
        merged_pts = pts1 + pts2[1:]
        return {'points': merged_pts, 'start_j': new_start_j, 'end_j': new_end_j}

    # Iterate until stable: each pass scans for degree-2 junctions and merges.
    changed = True
    while changed:
        changed = False
        # Strip any None sentinels left by previous iteration before rebuilding
        surviving = [b for b in surviving if b is not None]
        adj = _build_adjacency(surviving)
        for jid, branch_indices in adj.items():
            # Only dissolve if exactly 2 branches meet here AND neither branch
            # is a closed loop (where start_j == end_j, meaning it uses the
            # same junction on both ends — dissolving would break the loop).
            if len(branch_indices) != 2:
                continue
            i1, i2 = branch_indices
            b1, b2 = surviving[i1], surviving[i2]

            # Safety: don't dissolve if one branch is a closed loop
            if b1['start_j'] == b1['end_j'] or b2['start_j'] == b2['end_j']:
                continue

            merged = _concat_branches(b1, b2, jid)
            # Replace b1 in-place; mark b2 as removed via sentinel
            surviving[i1] = merged
            surviving[i2] = None  # type: ignore[assignment]
            changed = True
            break  # restart scan with fresh adjacency after each merge

    # Final sentinel strip
    surviving = [b for b in surviving if b is not None]

    # --- Pass D: simplify and emit final branches ---
    final_branches = []
    for b in surviving:
        pts = b['points']
        is_closed_loop = (pts[0] == pts[-1])

        pts_array = np.array(pts, dtype=np.float32)
        # TODO: enforce orthogonal corners
        simplified = cv2.approxPolyDP(pts_array, epsilon=1.5, closed=is_closed_loop)
        simp_pts = [(int(p[0][0]), int(p[0][1])) for p in simplified]

        # Deduplicate sequential identical points that approxPolyDP might leave
        deduped = [simp_pts[0]]
        for pt in simp_pts[1:]:
            if pt != deduped[-1]:
                deduped.append(pt)

        if is_closed_loop and deduped[-1] != deduped[0]:
            deduped.append(deduped[0])

        if len(deduped) > 1:
            final_branches.append(deduped)

    return final_branches


def _adaptive_erode_wall_mask(
    closed: np.ndarray,
    dist_transform: np.ndarray,
    keep_fraction: float = 0.45,
    local_radius: int | None = None,
) -> np.ndarray:
    """
    Pixel-wise adaptive thinning of the wall mask for single-strand skeletonisation.

    Rather than morphological erosion with a global or per-component radius
    (which fails when all walls are one connected blob), we keep only pixels
    where the local distance-transform value is a large enough fraction of the
    *locally-maximum* distance-transform value:

        keep pixel (x,y)  iff  DT[y,x] >= keep_fraction * local_max_DT[y,x]

    ``local_max_DT`` is computed by dilating the distance transform with a
    square window of size ``2*local_radius+1``.  This makes the threshold
    adapt to local wall width: thin walls (small DT peak) keep their full
    medial region, while thick walls (large DT peak) are trimmed to a thin
    inner strip — exactly what is needed to collapse double-strand skeletons.

    Guard: pixels with DT == 0 (off-mask) are always excluded.

    Parameters
    ----------
    closed:
        Morphologically-closed binary wall mask, shape (H, W) uint8.
    dist_transform:
        Distance transform of *closed*, shape (H, W) float32.
        Must be pre-computed from the **original** *closed* mask.
    keep_fraction:
        Fraction of the local DT peak to use as the keep threshold.
        Lower values keep a wider strip (less thinning); higher values keep
        a narrower strip (more aggressive thinning).  ``0.45`` is calibrated
        to produce a 1-3 px wide strip for wall half-thicknesses >= 3 px.
    local_radius:
        Half-size of the square window used to compute the local DT maximum.
        If ``None`` (default), the radius is auto-calibrated from the 95th-
        percentile of the distance-transform values in the mask:
        ``max(12, int(p95_dt * 1.2))``.  This ensures the window always covers
        the widest wall in the image regardless of resolution or scale.
        Pass an explicit integer to override for tests or fine-tuning.

    Returns
    -------
    np.ndarray, shape (H, W) uint8
        Thinned mask.  Always a subset of *closed*; suitable for passing to
        :func:`skimage.morphology.skeletonize`.
    """
    # Auto-calibrate local_radius from the image's own DT distribution so that
    # the max-pooling window is always larger than the widest wall half-thickness.
    # 
    # KNOWN LIMITATION: The global 95th percentile (p95_dt) works perfectly for 
    # uniform images, but under-estimates the required erosion radius for a very 
    # thick wall if it shares a connected component with many thin walls (e.g. an
    # exterior load-bearing wall merged with interior dividers). In such cases, 
    # the thick wall may still exhibit double-strand fragmentation.
    # A true per-region (not per-component) multi-scale adaptive erosion would be 
    # needed to fully resolve this, but naive multi-scale dilation was found to 
    # catastrophically fragment thin walls by allowing thick-junction DT values to 
    # bleed over into thin-wall regions.
    if local_radius is None:
        dt_vals_mask = dist_transform[closed > 0]
        if dt_vals_mask.size > 0:
            p95_dt = float(np.percentile(dt_vals_mask, 95))
            local_radius = max(12, int(p95_dt * 1.2))
        else:
            local_radius = 12

    # Local maximum of the distance transform — gives the DT peak in the
    # neighbourhood of each pixel (i.e. the local wall half-thickness).
    win = 2 * local_radius + 1
    local_max_dt = cv2.dilate(dist_transform,
                               cv2.getStructuringElement(cv2.MORPH_RECT, (win, win)))

    # Avoid division by zero; where local_max_dt == 0 the mask is already 0.
    with np.errstate(invalid='ignore', divide='ignore'):
        ratio = np.where(local_max_dt > 0, dist_transform / local_max_dt, 0.0)

    # Keep only pixels that lie in the inner keep_fraction of the wall
    # cross-section.  Near-boundary pixels (small DT -> small ratio) are
    # excluded, which collapses double-strand skeletons to a single medial strip.
    inner_band = (ratio >= keep_fraction) & (closed > 0)

    return inner_band.astype(np.uint8)



def _extract_walls_via_skeleton(
    mask: np.ndarray,
    scale_m_per_px: float,
    letterbox_info: dict,
) -> list[Wall]:
    """
    Extract discrete 1D wall centerlines from the wall mask using graph-based 
    Skeleton tracing, avoiding HoughLinesP fragmentation.
    """
    binary = (mask == CLASS_WALL).astype(np.uint8)
    if binary.sum() == 0:
        return []

    # Close small gaps
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (CLOSING_KERNEL_PX, CLOSING_KERNEL_PX))
    closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
    
    # Distance transform for thickness recovery.
    # IMPORTANT: this must be computed from `closed`, NEVER from an eroded
    # variant. The erosion below is only a skeletonisation aid.
    dist_transform = cv2.distanceTransform(closed, cv2.DIST_L2, 5)

    # Per-component adaptive erosion — collapses thick double-strand walls to
    # a thin strip that skeletonises into a single centerline strand.
    # Thickness sampling later still uses the original `dist_transform`.
    eroded_for_skel = _adaptive_erode_wall_mask(closed, dist_transform)

    # Skeletonize the *eroded* mask so thick walls produce single-strand paths.
    skeleton_bool = skimage.morphology.skeletonize(eroded_for_skel > 0)
    
    branches = _trace_skeleton_graph(skeleton_bool, min_branch_length=5)
    
    walls = []
    H, W = dist_transform.shape
    lb_scale = letterbox_info["scale"]

    idx = 0
    for branch_pts in branches:
        for i in range(len(branch_pts) - 1):
            x1, y1 = branch_pts[i]
            x2, y2 = branch_pts[i+1]
            
            # Sample thickness along the mask line
            length = math.hypot(x2 - x1, y2 - y1)
            steps = int(max(length, 5))
            dt_vals = []
            for j in range(steps + 1):
                t = j / steps
                px = int(round(x1 + t*(x2 - x1)))
                py = int(round(y1 + t*(y2 - y1)))
                if 0 <= px < W and 0 <= py < H:
                    dt_vals.append(dist_transform[py, px])
                    
            median_dt = float(np.median(dt_vals)) if dt_vals else 1.0
            thickness_mask_px = max(median_dt * 2.0, 1.0)
            
            # Convert to original image coordinates
            pts_mask = np.array([[x1, y1], [x2, y2]], dtype=np.float32)
            pts_orig = _mask_to_canvas_coords(pts_mask, letterbox_info)
            
            start = Point2D(
                x=float(pts_orig[0][0]) * scale_m_per_px,
                y=float(pts_orig[0][1]) * scale_m_per_px,
            )
            end = Point2D(
                x=float(pts_orig[1][0]) * scale_m_per_px,
                y=float(pts_orig[1][1]) * scale_m_per_px,
            )
            
            thickness_orig_px = thickness_mask_px / lb_scale
            thickness_m = max(thickness_orig_px * scale_m_per_px, 0.05)
            
            length_m = math.hypot(end.x - start.x, end.y - start.y)
            if length_m < 0.05:
                continue
                
            walls.append(Wall(
                id=f"wall_{idx}",
                start=start,
                end=end,
                thickness=thickness_m,
                height=DEFAULT_WALL_HEIGHT_M,
            ))
            idx += 1
            
    return walls


def _find_nearest_wall(
    point_px: tuple[float, float],
    walls: list[Wall],
    scale_m_per_px: float,
) -> tuple[Wall | None, float]:
    """
    Project a mask-space point onto each wall centre-line.

    Returns ``(nearest_wall, position_on_wall_m)`` where
    ``position_on_wall_m`` is the arc-length from ``wall.start`` to the foot
    of the perpendicular from *point*.
    """
    if not walls:
        return None, 0.0

    px_m = point_px[0] * scale_m_per_px
    py_m = point_px[1] * scale_m_per_px

    best_wall: Wall | None = None
    best_dist = float("inf")
    best_pos = 0.0

    for wall in walls:
        dx = wall.end.x - wall.start.x
        dy = wall.end.y - wall.start.y
        length_sq = dx * dx + dy * dy
        if length_sq < 1e-12:
            continue

        t = ((px_m - wall.start.x) * dx + (py_m - wall.start.y) * dy) / length_sq
        t = max(0.0, min(1.0, t))
        proj_x = wall.start.x + t * dx
        proj_y = wall.start.y + t * dy
        dist = math.hypot(px_m - proj_x, py_m - proj_y)

        if dist < best_dist:
            best_dist = dist
            best_wall = wall
            best_pos = t * math.sqrt(length_sq)

    return best_wall, best_pos


def _build_floor_polygon(
    mask: np.ndarray,
    letterbox_info: dict,
    scale_m_per_px: float,
    walls: list[Wall],
) -> list[Point2D]:
    """
    Build the floor polygon from the bounding box of all wall endpoints.
    """
    if not walls:
        return []
        
    pts = [(w.start.x, w.start.y) for w in walls] + [(w.end.x, w.end.y) for w in walls]
    min_x = min(p[0] for p in pts)
    max_x = max(p[0] for p in pts)
    min_y = min(p[1] for p in pts)
    max_y = max(p[1] for p in pts)
    
    return [
        Point2D(x=min_x, y=min_y),
        Point2D(x=max_x, y=min_y),
        Point2D(x=max_x, y=max_y),
        Point2D(x=min_x, y=max_y),
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def mask_to_ir(
    mask: np.ndarray,
    letterbox_info: dict,
    original_image_path: str | Path,
    *,
    known_door_width_m: float | None = None,
    scale_m_per_px: float | None = None,
) -> IRFloorPlan:
    """
    Convert a raw segmentation mask to a validated :class:`IRFloorPlan`.

    Parameters
    ----------
    mask:
        Per-pixel class indices, shape (H, W) uint8.
        Values: 0=floor, 1=wall, 2=door, 3=window.
    letterbox_info:
        Dict from :func:`~app.parsers.image_preprocessing.preprocess_image`.
    original_image_path:
        Path to the original input image (used for ``metadata.source_file``).
    known_door_width_m:
        If provided, calibrate the pixel→metre scale using the average detected
        door width.  If ``None``, a fixed default scale is used and
        ``metadata.scale_confidence = "estimated"``.

    Returns
    -------
    IRFloorPlan
        Validated IR document.  May have an empty ``walls`` list if the model
        detected no wall regions above the noise threshold.
    """
    image_path = Path(original_image_path)

    # ------------------------------------------------------------------ #
    # 1. Wall contours
    # ------------------------------------------------------------------ #
    wall_contours = _extract_contours_for_class(mask, CLASS_WALL)
    logger.debug("Found %d wall contour(s) above noise threshold.", len(wall_contours))

    # ------------------------------------------------------------------ #
    # 2. Door contours — needed for optional scale calibration
    # ------------------------------------------------------------------ #
    door_contours = _extract_contours_for_class(mask, CLASS_DOOR)
    window_contours = _extract_contours_for_class(mask, CLASS_WINDOW)

    # ------------------------------------------------------------------ #
    # 3. Scale estimation
    # ------------------------------------------------------------------ #
    scale_confidence: Literal["high", "estimated"] = "estimated"
    if scale_m_per_px is not None:
        # Caller-supplied override (e.g. from DPI when rendering a PDF).
        # This is still 'estimated' since no real-world reference object was used.
        logger.debug(
            "Using caller-supplied scale_m_per_px=%.6f for '%s'.",
            scale_m_per_px, image_path.name,
        )
    else:
        scale_m_per_px = DEFAULT_SCALE_M_PER_PX

    if known_door_width_m is not None and door_contours:
        # Measure average door bounding-box width (longer axis) in mask px
        door_widths_px: list[float] = []
        for dc in door_contours:
            pts_mask = dc.reshape(-1, 2).astype(np.float32)
            pts_orig = _mask_to_canvas_coords(pts_mask, letterbox_info)
            _, (w, h), _ = cv2.minAreaRect(pts_orig.astype(np.float32))
            door_widths_px.append(max(w, h))

        avg_door_px = float(np.mean(door_widths_px))
        if avg_door_px > 1e-6:
            scale_m_per_px = known_door_width_m / avg_door_px
            scale_confidence = "high"
            logger.info(
                "Scale calibrated: avg_door=%.1f px → %.5f m/px (%.2f m/door)",
                avg_door_px, scale_m_per_px, known_door_width_m,
            )
        else:
            logger.warning(
                "known_door_width_m=%s provided but door width in mask is ~0; "
                "falling back to default scale.",
                known_door_width_m,
            )

    logger.debug(
        "Scale: %.5f m/px (%s confidence) for '%s'.",
        scale_m_per_px, scale_confidence, image_path.name,
    )

    # ------------------------------------------------------------------ #
    # 4. Build Wall objects
    # ------------------------------------------------------------------ #
    walls = _extract_walls_via_skeleton(mask, scale_m_per_px, letterbox_info)

    # ------------------------------------------------------------------ #
    # 5. Build Opening objects
    # ------------------------------------------------------------------ #
    openings: list[Opening] = []
    door_idx = 0
    window_idx = 0

    def _add_openings(
        contours: list[np.ndarray],
        opening_type: Literal["door", "window"],
        default_width_m: float,
        default_height_m: float,
        counter_ref: list[int],
    ) -> None:
        for cnt in contours:
            pts_mask = cnt.reshape(-1, 2).astype(np.float32)
            pts_orig = _mask_to_canvas_coords(pts_mask, letterbox_info)
            cx = float(np.mean(pts_orig[:, 0]))
            cy = float(np.mean(pts_orig[:, 1]))

            # Width/height from minAreaRect
            _, (w, h), _ = cv2.minAreaRect(pts_orig.astype(np.float32))
            width_m = max(w, h) * scale_m_per_px
            height_m = default_height_m

            # Sanity check against massive model hallucinations (e.g. continuous thin speckle strings)
            if width_m > 5.0:
                logger.debug(f"Dropping huge {opening_type} (width={width_m:.1f}m)")
                continue

            nearest_wall, pos = _find_nearest_wall((cx, cy), walls, scale_m_per_px)
            if nearest_wall is None:
                logger.debug(
                    "Opening at (%.1f, %.1f) has no nearby wall; skipping.", cx, cy
                )
                return

            oid = f"{opening_type}_{counter_ref[0]}"
            counter_ref[0] += 1
            openings.append(
                Opening(
                    id=oid,
                    type=opening_type,
                    wall_id=nearest_wall.id,
                    position_on_wall=pos,
                    width=max(width_m, 0.3),
                    height=height_m,
                )
            )

    door_counter = [0]
    window_counter = [0]
    _add_openings(door_contours, "door", 0.9, 2.1, door_counter)
    _add_openings(window_contours, "window", 1.2, 1.5, window_counter)  # 1.5 m = sill(0.9)+top(2.4) gap

    def _deduplicate_openings(ops: list[Opening]) -> list[Opening]:
        from collections import defaultdict
        groups = defaultdict(list)
        for op in ops:
            groups[(op.wall_id, op.type)].append(op)
            
        deduped = []
        for group in groups.values():
            merged = []
            for op in group:
                overlap_found = False
                for m in merged:
                    s1, e1 = op.position_on_wall - op.width / 2, op.position_on_wall + op.width / 2
                    s2, e2 = m.position_on_wall - m.width / 2, m.position_on_wall + m.width / 2
                    
                    intersection = max(0.0, min(e1, e2) - max(s1, s2))
                    union = max(e1, e2) - min(s1, s2)
                    iou = intersection / union if union > 0 else 0.0
                    
                    len1, len2 = e1 - s1, e2 - s2
                    is_contained = (intersection >= len1 * 0.95) or (intersection >= len2 * 0.95)
                    
                    if iou > 0.5 or is_contained:
                        overlap_found = True
                        if op.width > m.width:
                            m.position_on_wall = op.position_on_wall
                            m.width = op.width
                        break
                if not overlap_found:
                    merged.append(op)
            deduped.extend(merged)
            
        # Sort by id to preserve deterministic ordering
        deduped.sort(key=lambda x: int(x.id.split('_')[-1]) if '_' in x.id else 0)
        return deduped

    openings = _deduplicate_openings(openings)

    # ------------------------------------------------------------------ #
    # 6. Floor polygon
    # ------------------------------------------------------------------ #
    floor_polygon = _build_floor_polygon(mask, letterbox_info, scale_m_per_px, walls)

    # ------------------------------------------------------------------ #
    # 7. Assemble + validate
    # ------------------------------------------------------------------ #
    ir = IRFloorPlan(
        metadata=Metadata(
            unit="meter",
            source_type="image",
            scale_confidence=scale_confidence,
        ),
        walls=walls,
        openings=openings,
        floor_polygon=floor_polygon,
    )
    ir.validate()

    logger.info(
        "mask_to_ir '%s': %d wall(s), %d opening(s), scale=%s",
        image_path.name, len(walls), len(openings), scale_confidence,
    )
    return ir
