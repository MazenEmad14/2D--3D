import numpy as np
from app.parsers.mask_to_ir import _trace_skeleton_graph

def test_t_junction_tracing():
    # Create a 20x20 boolean skeleton
    skel = np.zeros((20, 20), dtype=bool)
    
    # Horizontal line from x=2 to x=18 at y=10
    skel[10, 2:19] = True
    
    # Vertical line from y=10 to y=18 at x=10
    # This forms a perfect T-junction at (10, 10)
    skel[10:19, 10] = True
    
    # The endpoints should be (2, 10), (18, 10), and (10, 18)
    
    # Trace the graph
    branches = _trace_skeleton_graph(skel, min_branch_length=2)
    
    assert len(branches) == 3, f"Expected 3 branches for a T-junction, got {len(branches)}"
    
    # Check that (10, 10) is a shared endpoint for all 3 branches
    junction_pt = (10, 10)
    junction_hits = 0
    for branch in branches:
        endpoints = [branch[0], branch[-1]]
        if junction_pt in endpoints:
            junction_hits += 1
            
    assert junction_hits == 3, f"Expected 3 branches to share junction {junction_pt}, got {junction_hits}"

def test_short_stub_preserved():
    # Create a skeleton with a long line and a very short perpendicular stub (3 px long)
    skel = np.zeros((20, 20), dtype=bool)
    
    # Long horizontal line
    skel[10, 2:19] = True
    
    # Short vertical stub (x=10, y=10 to 12) -> Length is ~2 px
    skel[10:13, 10] = True
    
    branches = _trace_skeleton_graph(skel, min_branch_length=2)
    
    # The stub is 2px long (y=10, 11, 12 is 3 pixels -> length 2)
    # Since min_branch_length=2, it should be preserved.
    assert len(branches) == 3, "Short stub wall should be preserved, not dropped"

def test_closed_loop():
    import skimage.morphology
    
    # Create a 20x20 hollow square
    mask = np.zeros((20, 20), dtype=bool)
    mask[4:16, 4:16] = True
    mask[6:14, 6:14] = False
    
    # Use actual skeletonize to guarantee a true 1-pixel wide 8-connected line
    skel = skimage.morphology.skeletonize(mask)
    
    branches = _trace_skeleton_graph(skel, min_branch_length=2)
    
    assert len(branches) == 1, f"Expected exactly 1 branch for a perfectly closed loop, got {len(branches)}"
    branch = branches[0]
    
    # Check if loop is closed (start == end)
    assert branch[0] == branch[-1], "The closed loop branch should start and end at the same pixel"


def test_stub_healing_merges_fragmented_wall():
    """
    Regression test for audit finding 1.1 (Massive Straight-Wall Fragmentation).

    Skeleton: a 40-pixel horizontal line at y=20 (from x=2 to x=42), with a
    2-pixel perpendicular stub at x=22, y=20 → y=22.

    Before the healing pass, the stub creates a junction at (22, 20) that splits
    the horizontal line into two collinear branches:
        left  branch: (2,20) → (22,20)
        right branch: (22,20) → (42,20)
        stub  branch: (22,20) → (22,22)  [length=2, dropped by min_branch_length=4]

    After the healing pass, the junction at (22, 20) should have degree 2 (only
    the left and right main-wall branches remain), so it must be dissolved and
    the two halves concatenated into a SINGLE branch spanning (2,20)→(42,20).

    We assert:
    - The stub branch is absent (< min_branch_length).
    - Exactly ONE wall branch remains (not two collinear fragments).
    - That branch spans at least 36 pixels in length (the full horizontal extent
      minus rounding from approxPolyDP).
    """
    skel = np.zeros((50, 50), dtype=bool)

    # Long horizontal line: y=20, x=2..42  (41 pixels)
    skel[20, 2:43] = True

    # Short stub: x=22, y=20..22  (3 pixels → length 2)
    # The pixel at (22, 20) is already set by the horizontal line above.
    skel[21:23, 22] = True   # adds y=21 and y=22

    MIN_BRANCH = 4  # stub length (2 px) < 4, so it should be dropped

    branches = _trace_skeleton_graph(skel, min_branch_length=MIN_BRANCH)

    # ── assertion 1: stub is gone, exactly 1 branch survives ──────────────────
    assert len(branches) == 1, (
        f"Expected exactly 1 healed wall branch, got {len(branches)}: {branches}"
    )

    # ── assertion 2: the surviving branch spans the full horizontal wall ───────
    branch = branches[0]
    xs = [pt[0] for pt in branch]
    span = max(xs) - min(xs)
    assert span >= 36, (
        f"Healed wall spans only {span} px in x; expected >=36 px "
        f"(branch endpoints: {branch[0]} -> {branch[-1]})"
    )


def test_thick_wall_single_strand_skeleton_and_accurate_thickness():
    """
    Regression test for the double-strand ladder skeleton produced by thick walls
    (Option-B adaptive pre-erosion fix).

    A 30-pixel-wide wall mask with bumpy/irregular edges — mimicking real
    segmentation output — produces junction pixels (degree >= 3) in its raw
    skeleton because the ragged edges create local width variations that cause
    skimage.skeletonize to branch.

    After _adaptive_erode_wall_mask the eroded mask is thin enough that
    skeletonize produces a single-strand centerline with no junction pixels.

    Additionally, Wall.thickness is verified to be sampled from the ORIGINAL
    (pre-erosion) distance transform so the returned value is close to the
    true 30 px wall width (~0.30 m at 0.01 m/px).
    """
    import cv2 as _cv2
    import skimage.morphology as _skmorph
    from app.parsers.mask_to_ir import (
        CLASS_WALL,
        _adaptive_erode_wall_mask,
        _extract_walls_via_skeleton,
    )

    WALL_WIDTH  = 30
    WALL_LENGTH = 110
    PAD         = 10
    H = WALL_WIDTH + 2 * PAD + 10   # extra room for bumps
    W = WALL_LENGTH + 2 * PAD

    # --- Build a bumpy-edge wall mask (seed=0 is deterministic) ---------------
    # A plain rectangle doesn't produce junction pixels; real segmented walls
    # have local width variation that causes double-strand skeletons.
    rng = np.random.default_rng(seed=0)
    mask = np.zeros((H, W), dtype=np.uint8)
    mask[PAD : PAD + WALL_WIDTH, PAD : PAD + WALL_LENGTH] = CLASS_WALL

    closed_for_bumps = (mask == CLASS_WALL).astype(np.uint8)
    # Add bumps on both long edges to create width variation
    for _ in range(8):
        y_top = int(rng.integers(PAD, PAD + 4))
        x     = int(rng.integers(PAD, PAD + WALL_LENGTH))
        r     = int(rng.integers(3, 7))
        _cv2.circle(closed_for_bumps, (x, y_top), r, 1, -1)
    for _ in range(8):
        y_bot = int(rng.integers(PAD + WALL_WIDTH - 4, PAD + WALL_WIDTH))
        x     = int(rng.integers(PAD, PAD + WALL_LENGTH))
        r     = int(rng.integers(3, 7))
        _cv2.circle(closed_for_bumps, (x, y_bot), r, 1, -1)

    kn = np.array([[1, 1, 1], [1, 0, 1], [1, 1, 1]], dtype=np.uint8)

    # ── Part 1: baseline — confirm junctions EXIST without erosion ────────────
    skel_raw = _skmorph.skeletonize(closed_for_bumps > 0).astype(np.uint8)
    nc_raw   = _cv2.filter2D(skel_raw, -1, kn, borderType=_cv2.BORDER_CONSTANT)
    junc_raw = int(((skel_raw > 0) & (nc_raw >= 3)).sum())
    assert junc_raw > 0, (
        f"Baseline check: expected junction pixels on bumpy-edge 30px-wide skeleton, "
        f"got {junc_raw}. Regenerate the mask or check the seed."
    )

    # ── Part 2: after adaptive erosion — no junction pixels ───────────────────
    dist_t   = _cv2.distanceTransform(closed_for_bumps, _cv2.DIST_L2, 5)
    eroded   = _adaptive_erode_wall_mask(closed_for_bumps, dist_t)
    skel_er  = _skmorph.skeletonize(eroded > 0).astype(np.uint8)
    nc_er    = _cv2.filter2D(skel_er, -1, kn, borderType=_cv2.BORDER_CONSTANT)
    junc_er  = int(((skel_er > 0) & (nc_er >= 3)).sum())
    assert junc_er == 0, (
        f"After adaptive erosion, skeleton should have 0 junction pixels, "
        f"got {junc_er}. Check thickness_factor / min_half_thickness_to_erode."
    )

    # ── Part 3: Wall.thickness must reflect original mask, not eroded one ─────
    # Rebuild full class mask for _extract_walls_via_skeleton
    full_mask = np.zeros((H, W), dtype=np.uint8)
    full_mask[closed_for_bumps > 0] = CLASS_WALL

    letterbox_info = {
        "scale":    1.0,
        "pad_left": 0,
        "pad_top":  0,
        "orig_h":   H,
        "orig_w":   W,
    }
    walls = _extract_walls_via_skeleton(full_mask, scale_m_per_px=0.01,
                                         letterbox_info=letterbox_info)

    assert len(walls) >= 1, (
        "Expected at least one Wall segment for a 110px-long bumpy wall."
    )

    # True half-thickness ≈ 15 px → thickness ≈ 0.30 m.
    # Allow ±50 % margin because bumps widen the DT at their peaks.
    for w in walls:
        assert 0.15 <= w.thickness <= 0.55, (
            f"Wall thickness {w.thickness:.3f} m outside [0.15, 0.55] m. "
            "Erosion must not affect thickness — DT must be from original mask."
        )

