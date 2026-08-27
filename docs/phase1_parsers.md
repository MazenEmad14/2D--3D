# Phase 1 — Parser Documentation

**Version:** 1.0.0  
**Phase:** 1 — DXF & PDF Parsing

---

## Overview

Phase 1 delivers two parsing pipelines that convert architectural floor-plan
files into a common intermediate form ready for the Phase 2 AI model.

| Pipeline | Input | Output |
|----------|-------|--------|
| DXF      | `.dxf` (AutoCAD R12+) | `IRFloorPlan` (direct coordinate extraction) |
| PDF      | `.pdf` (any content) | Raster PNG image via `PDFRenderedToImage` → Phase 2 |

---

## DXF Parser (`app/parsers/dxf_to_ir.py`)

### Supported Entity Types

| DXF Type | Extracts | Layer config key |
|----------|----------|-----------------|
| `LINE` | Start/end points | `WALL_LAYERS` |
| `LWPOLYLINE` | Consecutive segment pairs (+ closing segment if closed) | `WALL_LAYERS` |
| `INSERT` (block ref) | Insertion point, default opening dimensions | `DOOR_LAYERS`, `WINDOW_LAYERS` |

All other entity types (`ARC`, `CIRCLE`, `SPLINE`, `HATCH`, etc.) are
silently ignored in Phase 1.

### Supported Layer Name Conventions

Layer matching is **case-insensitive** and **substring-based**:
a layer named `"A-WALL-EXT"` matches the pattern `"A-WALL"`.

Patterns are defined in `app/parsers/layer_config.py` and can be extended
at any time without touching parser logic.

**Default wall layers:**  
`WALLS`, `WALL`, `A-WALL`, `S-WALL`, `MWALL`, `PARTI`, `STR-WALL`

**Default door layers:**  
`DOORS`, `DOOR`, `A-DOOR`, `A-GLAZ-DOOR`

**Default window layers:**  
`WINDOWS`, `WINDOW`, `A-GLAZ`, `A-WIND`, `GLAZING`

### Unit Conversion

The DXF header variable `$INSUNITS` is read and converted to a metres
scale factor via `INSUNITS_TO_METRES` in `layer_config.py`.

| $INSUNITS code | Unit | Scale factor |
|---------------|------|-------------|
| 0 | Unitless | 1.0 (estimated) |
| 1 | Inches | 0.0254 |
| 2 | Feet | 0.3048 |
| 4 | Millimetres | 0.001 |
| 5 | Centimetres | 0.01 |
| 6 | Metres | 1.0 |

`metadata.scale_confidence` is `"high"` for all known unit codes, and
`"estimated"` for code 0 (unitless).

### Wall Thickness Detection (Parallel-Pair Heuristic)

After collecting all wall segments the parser attempts to detect wall
thickness by finding **parallel segment pairs**:

1. For each segment *A*, search for a segment *B* (not yet paired) such that:
   - The angular difference between the two segments is < `PARALLEL_ANGLE_TOLERANCE_DEG` (default 10°).
   - The midpoint-to-midpoint distance is < `DEFAULT_WALL_THICKNESS_M × PARALLEL_DISTANCE_FACTOR` (default 0.2 × 4 = 0.8 m).
2. If a pair is found, both segments are merged into a **centred wall** whose:
   - Centre-line = average of the two segment endpoints.
   - Thickness = midpoint separation distance.
3. If no pair is found, the segment becomes its own wall with `DEFAULT_WALL_THICKNESS_M = 0.2 m`.

This heuristic works well when the CAD drafter drew both faces of each
wall.  Single-line wall drawings (common in schematic plans) will
produce walls with the default thickness.

### Opening Placement

`INSERT` entities on door/window layers are assigned to walls by
**perpendicular projection**:

1. Project the insertion point onto each wall centre-line.
2. Select the wall with the minimum perpendicular distance.
3. `position_on_wall` = arc length from `wall.start` to the foot of the perpendicular.

Block dimensions are **not** read in Phase 1 — `width` and `height` use
`DEFAULT_DOOR_WIDTH_M / DEFAULT_DOOR_HEIGHT_M` (doors) or
`DEFAULT_WINDOW_WIDTH_M / DEFAULT_WINDOW_HEIGHT_M` (windows).

---

## PDF Handling (`app/parsers/pdf_to_ir.py`)

### Approach

**All PDFs follow a single code path regardless of content type:**

1. Open with PyMuPDF (`fitz`).
2. Render page 0 to a PNG at `DEFAULT_RENDER_DPI` (150 DPI).
3. Save the PNG to a temporary directory.
4. Raise `PDFRenderedToImage` — a routing signal that carries `image_path`.

The calling code (Phase 4 API layer) catches `PDFRenderedToImage` and
forwards `exception.image_path` to the Phase 2 image segmentation model.

### Why Not Extract Vector Geometry from PDFs?

PDFs lack the semantic layer information that DXF files carry.  Even when
a PDF contains genuine vector CAD data, it is impossible to reliably:

- Distinguish wall lines from annotation lines, hatching, or dimension
  leaders using geometric heuristics alone.
- Detect door and window openings purely from path geometry.

Delegating to a trained segmentation model (Phase 2) is more robust and
produces **consistent results for both vector and scanned PDF inputs**.

### Trade-off: Pixel Accuracy vs. Semantic Understanding

| Approach | Coordinate accuracy | Door/window detection |
|----------|--------------------|-----------------------|
| Direct vector extraction | Sub-millimetre | ❌ Unreliable |
| Phase 2 model (via render) | Pixel-level (±1–2 mm at 150 DPI) | ✅ Reliable |

This is an **accepted design decision**, not a bug.  For projects where
sub-millimetre coordinate accuracy is critical, DXF input should be used.

### DPI Setting

`DEFAULT_RENDER_DPI = 150`

At 150 DPI a standard A4 page (595 × 842 points) renders to
approximately **1 240 × 1 754 pixels** — sufficient for the segmentation
model while keeping file sizes manageable (~1–3 MB uncompressed PNG).

Increase to 200–300 DPI for dense drawings; decrease to 72–100 DPI for
fast preview rendering.  Pass `dpi=` to `parse_pdf()` to override.

---

## PDF Content Detector (`app/parsers/pdf_content_detector.py`)

> **Note:** This classifier is kept for **metadata and logging purposes only**.
> Its result does NOT change the processing path in Phase 1.

### Function

```python
detect_pdf_content_type(pdf_path, *, min_drawings=10, min_image_coverage=0.50)
    -> Literal["vector", "scanned"]
```

### Heuristic

1. Count drawing commands on page 0 via `page.get_drawings()`.  
   If count ≥ `min_drawings` → `"vector"`.
2. Check embedded raster images via `page.get_images()`.  
   If any image covers ≥ `min_image_coverage` of page area → `"scanned"`.
3. Default: `"vector"`.

The result is exposed as `metadata.source_type` (`"pdf_vector"` or
`"pdf_scanned"`) in future phases when PDFs produce IRFloorPlan objects
directly.

---

## Custom Exceptions (`app/parsers/exceptions.py`)

| Exception | Base class | Raised by | Meaning |
|-----------|-----------|-----------|---------|
| `CorruptFileError` | `IOError` | DXF + PDF parsers | File cannot be opened or is structurally invalid |
| `UnsupportedFileFormatError` | `ValueError` | — (reserved for Phase 4 router) | File extension not supported |
| `NoWallLayersFoundError` | `ValueError` | DXF parser | No entities on any recognised wall layer |
| `PDFRenderedToImage` | `Exception` | PDF parser | Routing signal — PDF rendered to PNG, route to Phase 2 |

`ScannedPDFDetected` is a backward-compatibility alias for `PDFRenderedToImage`.

---

## Known Limitations

| Limitation | Impact | Planned fix |
|------------|--------|-------------|
| Curved walls (`ARC`, `SPLINE`) not extracted from DXF | Curved walls appear as straight segments or are omitted | Phase 3+ |
| Door/window block dimensions not read from DXF | Default widths used (0.9 m / 1.2 m) | Phase 1.5 |
| Non-standard DXF layer names silently skipped | Missing walls/openings | Extend `WALL_LAYERS` in `layer_config.py` |
| DXF: only model-space processed | Paper-space layouts ignored | Phase 3+ |
| PDF: only page 0 processed | Multi-storey sets require manual split | Phase 3+ |
| PDF: rasterisation introduces pixel-level positional error | ±1–2 mm at 150 DPI | Accepted design decision |
| Rotated / skewed DXF drawings not normalised | Coordinates in drawing CRS | Phase 3+ |
