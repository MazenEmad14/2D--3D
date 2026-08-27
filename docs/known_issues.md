# Known Issues & Roadmap

This document tracks known architectural limitations, edge-cases, and deferred fixes in the 2D to 3D floor plan parser.

## Graph Extraction / Skeletonization

### 1. Global Percentile Erosion Limitation (Double-Stranding & Centerline Drift)
**Location:** `app/parsers/mask_to_ir.py` -> `_adaptive_erode_wall_mask`

**Description:**
To prevent the skeletonizer from producing "ladder" artifacts (double strands) on thick walls, we adaptively erode the wall mask before skeletonization using a `local_radius` auto-calibrated from the image's 95th-percentile distance transform (`p95_dt`).

**The Limitation:** 
Because `p95_dt` is calculated *globally* (per connected component), highly heterogeneous wall thicknesses within the same component smear the distance-transform histogram, causing the global threshold to fail locally in two ways:
1. **Double-Strand Fragmentation:** If thick walls are connected to numerous thin walls, the threshold is dragged down by the thin walls. The thick walls under-erode and retain double-strand artifacts.
2. **Asymmetric Centerline Drift:** If thin walls meet significantly thicker walls (e.g. a T-junction), the global threshold over/under-erodes the junction asymmetrically. The skeleton node is pulled off-axis, creating measurable centerline drift.

**Concrete Evidence (T-Junction Scaling Tests):**
Testing a T-junction with various thickness ratios between the exterior and interior walls shows that extreme asymmetry alone does *not* cause massive drift, so long as the room uses only two thicknesses. But introducing 4 distinct thicknesses into a single component breaks the global heuristic:
- 1.0x (Symmetric, 10pt/10pt): 3.26 cm drift
- 1.5x (Asymmetric, 15pt/10pt): 3.30 cm drift
- 3.0x (Asymmetric, 30pt/10pt): 1.82 cm drift
- 5.0x (Extreme, 50pt/10pt): 1.65 cm drift
- **Reproduction Case (10, 15, 20, and 30pt walls in one component): 16.50 cm drift** (Fails the 15cm cross-check tolerance).

**Attempted Fix & Why it Failed:**
We attempted to replace the single fixed-window dilation with a pure multi-scale approach (assigning each pixel a `local_max_DT` from a dilation window proportional to its own `DT` value). 
This failed catastrophically (fragmenting thin walls into 500+ segments). The root cause was "DT bleed-over": the multi-scale windows applied at thin-wall pixels near a T-junction would capture the high DT value of the junction itself, erroneously classifying the adjacent thin wall as "edge" and eroding it away, causing massive gaps. 

To fully resolve this, a future implementation requires a true per-region adaptive approach that is immune to junction bleed-over (e.g. using the Medial Axis Transform directly instead of distance-transform-ratio thresholding). Until then, the 16.5cm drift failure on multi-thickness rooms is expected and tests enforcing a <15cm tolerance on such geometries are marked as `xfail`.

---

## DXF Parser / Layer Semantics

### 3. Heuristic Layer Matching False Positives/Negatives (2026-08-27)
**Location:** `app/parsers/dxf_to_ir.py` and `app/parsers/layer_config.py`

**Symptom:**
Valid geometry on unconventional layer names might be ignored (false negative), or invalid geometry on strangely named layers (e.g. "A-WALLPAPER") might be incorrectly extracted as walls (false positive).

**Root Cause:**
Layer matching uses string prefixes/substrings because there is no universal CAD layer standard. While we exclude known annotation suffixes (`DIM`, `TXT`, `PATT`), English-centric heuristic matching cannot perfectly handle all arbitrary CAD naming conventions.

**Workaround:**
Users with non-standard DXFs should explicitly pass the `custom_wall_layers` argument to `parse_dxf` to bypass the heuristic matching entirely.

---


### 2. Systematic centerline-vs-outer-edge measurement difference between vector and scanned pipelines (2026-08-27)
**Location:** `app/parsers/pdf_to_ir.py` → `_parse_vector_pdf` and `_parse_scanned_pdf` / `app/parsers/mask_to_ir.py` → `_extract_walls_via_skeleton`

**Symptom:**
When the same floor plan is parsed by both the vector and scanned pipelines and their bounding boxes are compared, a consistent gap of approximately **15–20 cm per wall** is observed in the room-level bounding box dimensions. This is **not a scale calibration bug** — both pipelines now share the same architectural fallback scale (`1.2192/units-per-paper-inch m/unit`). The gap is structural and will recur reliably on any real floor plan cross-check.

**Root Cause:**
The two pipelines measure geometrically different things:

- **Vector pipeline (`_parse_vector_pdf`)**: Extracts the outer bounding edges of filled rectangles. `start` and `end` coordinates on a wall land on the **outer perimeter** of the drawn rectangle. A horizontal wall with `x0=100, x1=400` produces a wall running from 100pt to 400pt — the outer edge of the ink.

- **Scanned pipeline (`_extract_walls_via_skeleton`)**: Skeletonizes the segmentation mask and traces its **medial axis (centerline)**. `start` and `end` coordinates land in the **geometric centre** of the wall's cross-section. For a 10pt-thick wall, the centerline is inset ~5pt (≈ 8.5cm at 1/4"=1' scale) from each outer edge.

Since the outer edges of a room are offset inward to the centerlines by one half-wall-thickness per side, the scanned pipeline's bounding box will always be **~`thickness/2`** smaller on each side than the vector pipeline's, producing a consistent `~thickness` shortfall in each room dimension.

**Why this is not a bug in the IR schema:**
The `Wall` object in the IR schema exports both `start`/`end` (centerline endpoints) **and** `thickness`. This is the correct representation for 3D extrusion: the downstream 3D engine reconstructs outer wall boundaries by extruding `thickness/2` perpendicular to the centerline on each side. Both pipelines produce IR that is complete and self-consistent within their own coordinate frame.

**What IS a limitation:**
The vector pipeline currently outputs outer-edge coordinates, not centerlines, as its `start`/`end`. This means:
- The `thickness` field for vector-extracted walls is measured as the rectangle's short side (correct), but the `start`/`end` positions represent the rectangle's long-axis outer boundary, not the centerline.
- A strict 3D extrusion engine that expects centerline semantics will misplace vector-parsed walls by `thickness/2` outward from their correct geometric centre.

**Future Fix:**
In `_parse_vector_pdf`, when extracting a wall from a filled rectangle, the `start` and `end` coordinates should be offset inward by `thickness/2` perpendicular to the wall axis to convert outer-edge to centerline representation, matching the scanned pipeline's semantics and the IR schema's intent.

**Impact on cross-check tolerance:**
Runs of this cross-check should expect a **systematic bbox-level discrepancy of approximately 1× wall-thickness per room dimension** between the vector and scanned pipelines. The per-wall midpoint criterion (≤15 cm) is the appropriate comparison metric; bbox-level comparison is inherently noisier by this structural offset.
