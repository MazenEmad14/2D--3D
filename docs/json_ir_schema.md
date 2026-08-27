# JSON Intermediate Representation (IR) Schema

**Schema version:** 1.0.0  
**Last updated:** Phase 0

---

## Purpose

Every input-format parser (DXF, PDF, image) in this project outputs a single, canonical **Intermediate Representation** (IR) document — a JSON object describing a floor plan in physical units (metres). The 3D geometry engine reads *only* this IR; it never touches the original file. This decoupling means:

- Parsers can be added, swapped, or improved independently.
- The 3D engine has one clean, well-typed input contract.
- Validation is centralised in `app/schemas/ir_schema.py`.

---

## Top-Level Structure

```json
{
  "metadata":       { ... },
  "walls":          [ ... ],
  "openings":       [ ... ],
  "floor_polygon":  [ ... ]
}
```

| Field            | Type            | Required | Description |
|------------------|-----------------|----------|-------------|
| `metadata`       | object          | ✅       | Provenance and scale information |
| `walls`          | array of Wall   | ✅       | All wall segments in the plan |
| `openings`       | array of Opening| ✅       | All doors and windows |
| `floor_polygon`  | array of Point2D| ✅       | Ordered vertices of the floor outline |

---

## `metadata`

```json
{
  "unit": "meter",
  "source_type": "dxf | pdf_vector | pdf_scanned | image",
  "scale_confidence": "high | estimated",
  "page_count": 1,
  "page_processed": 1,
  "walls_skipped_non_orthogonal": 0
}
```

| Field              | Type   | Allowed values                                    | Description |
|--------------------|--------|---------------------------------------------------|-------------|
| `unit`             | string | `"meter"` (only)                                  | Physical unit of all length values. Always metres. |
| `source_type`      | string | `"dxf"` · `"pdf_vector"` · `"pdf_scanned"` · `"image"` | Origin format of the floor plan. |
| `scale_confidence` | string | `"high"` · `"estimated"`                          | `"high"` — scale read from DXF header or a known PDF scale bar. `"estimated"` — heuristic or AI-derived. Downstream UI should flag "estimated" to the user. |
| `page_count`       | int    | ≥ 1                                               | Total number of pages in the source document. |
| `page_processed`   | int    | ≥ 1                                               | The specific page index (1-based) that was processed. |
| `walls_skipped_non_orthogonal` | int | ≥ 0                                      | Number of vector paths excluded specifically because they failed the axis-alignment check. Downstream UI should warn if > 0. |

---

## `walls`

Each entry is a **Wall** object representing a single wall segment as a directed line.

```json
{
  "id": "wall_1",
  "start": { "x": 0.0, "y": 0.0 },
  "end":   { "x": 5.0, "y": 0.0 },
  "thickness": 0.2,
  "height": 3.0
}
```

| Field       | Type    | Required | Default | Description |
|-------------|---------|----------|---------|-------------|
| `id`        | string  | ✅       | —       | Document-unique identifier (e.g. `"wall_1"`). |
| `start`     | Point2D | ✅       | —       | Origin of the wall centre-line (metres). |
| `end`       | Point2D | ✅       | —       | Terminal point of the wall centre-line (metres). |
| `thickness` | float   | ✅       | `0.2`   | Wall thickness in metres. Must be > 0. |
| `height`    | float   | ✅       | `3.0`   | Wall height in metres. Must be > 0. |

### `Point2D`

```json
{ "x": 0.0, "y": 0.0 }
```

Both `x` and `y` are floats representing metres in the plan's local coordinate system.

---

## `openings`

Each entry is an **Opening** object — a door or window cut into a specific wall.

```json
{
  "id": "door_1",
  "type": "door",
  "wall_id": "wall_1",
  "position_on_wall": 2.5,
  "width": 0.9,
  "height": 2.1
}
```

| Field               | Type   | Required | Description |
|---------------------|--------|----------|-------------|
| `id`                | string | ✅       | Document-unique identifier. |
| `type`              | string | ✅       | `"door"` or `"window"`. |
| `wall_id`           | string | ✅       | Must match an `id` in the `walls` array. |
| `position_on_wall`  | float  | ✅       | Distance from `wall.start` to the **centre** of the opening, measured along the wall centre-line (metres). Must be ≥ 0. |
| `width`             | float  | ✅       | Clear opening width (metres). Must be > 0. |
| `height`            | float  | ✅       | Clear opening height (metres). Must be > 0. |

---

## `floor_polygon`

An ordered list of `Point2D` vertices defining the floor outline. The polygon is **implicitly closed** — the last vertex connects back to the first.

```json
[
  { "x": 0.0, "y": 0.0 },
  { "x": 5.0, "y": 0.0 },
  { "x": 5.0, "y": 4.0 },
  { "x": 0.0, "y": 4.0 }
]
```

Vertices must be listed in **counter-clockwise** order when viewed from above (positive Z-up convention). At minimum, 3 vertices are required to form a valid polygon.

---

## Complete Example

See [`sample_ir_output.json`](sample_ir_output.json) for a full annotated example of a simple two-room floor plan.

```json
{
  "metadata": {
    "unit": "meter",
    "source_type": "dxf",
    "scale_confidence": "high",
    "page_count": 1,
    "page_processed": 1,
    "walls_skipped_non_orthogonal": 0
  },
  "walls": [
    {
      "id": "wall_1",
      "start": { "x": 0.0, "y": 0.0 },
      "end":   { "x": 5.0, "y": 0.0 },
      "thickness": 0.2,
      "height": 3.0
    },
    {
      "id": "wall_2",
      "start": { "x": 5.0, "y": 0.0 },
      "end":   { "x": 5.0, "y": 4.0 },
      "thickness": 0.2,
      "height": 3.0
    },
    {
      "id": "wall_3",
      "start": { "x": 5.0, "y": 4.0 },
      "end":   { "x": 0.0, "y": 4.0 },
      "thickness": 0.2,
      "height": 3.0
    },
    {
      "id": "wall_4",
      "start": { "x": 0.0, "y": 4.0 },
      "end":   { "x": 0.0, "y": 0.0 },
      "thickness": 0.2,
      "height": 3.0
    }
  ],
  "openings": [
    {
      "id": "door_1",
      "type": "door",
      "wall_id": "wall_1",
      "position_on_wall": 2.5,
      "width": 0.9,
      "height": 2.1
    },
    {
      "id": "window_1",
      "type": "window",
      "wall_id": "wall_2",
      "position_on_wall": 2.0,
      "width": 1.2,
      "height": 1.4
    }
  ],
  "floor_polygon": [
    { "x": 0.0, "y": 0.0 },
    { "x": 5.0, "y": 0.0 },
    { "x": 5.0, "y": 4.0 },
    { "x": 0.0, "y": 4.0 }
  ]
}
```

---

## Validation Rules

| Rule | Detail |
|------|--------|
| All `id` fields | Must be unique within their respective arrays. |
| `opening.wall_id` | Must reference an existing `wall.id`. |
| `thickness`, `height`, `width` | Must be strictly > 0. |
| `position_on_wall` | Must be ≥ 0. |
| `floor_polygon` | Must contain ≥ 3 vertices. |
| `unit` | Always `"meter"` — other units are not supported. |

---

## Python Dataclasses

The schema is mirrored as Python dataclasses in [`app/schemas/ir_schema.py`](../app/schemas/ir_schema.py). Use `IRFloorPlan.validate()` to enforce all constraints at runtime, and `IRFloorPlan.from_dict()` / `IRFloorPlan.to_dict()` for serialisation.

```python
from app.schemas.ir_schema import IRFloorPlan

floor_plan = IRFloorPlan.from_dict(raw_json_dict)
floor_plan.validate()          # raises ValueError / TypeError on bad data
json_str = floor_plan.to_json(indent=2)
```
