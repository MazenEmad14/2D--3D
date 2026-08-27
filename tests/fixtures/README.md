# tests/fixtures/README.md

# Test Fixtures — Phase 1 Parser Tests

All fixtures in this directory are **synthetically generated** by
`tests/fixtures/generate_fixtures.py`.  No real architectural drawings are
included.  Re-run the script to regenerate them at any time.

```
python tests/fixtures/generate_fixtures.py
```

---

## DXF Fixtures

| File | Tool | Content | Tests |
|------|------|---------|-------|
| `fixture_simple.dxf` | ezdxf | 4 LINE entities on `WALLS` layer, **millimetre units** (`$INSUNITS=4`), forming a 5 000 × 4 000 mm (5 × 4 m) room | Happy-path parse, unit conversion correctness |
| `fixture_with_door.dxf` | ezdxf | Same 4-wall room **+** a block `INSERT` on `DOOR` layer at (2 500, 0) mm | Door/opening extraction, nearest-wall assignment |
| `fixture_bad_layers.dxf` | ezdxf | Same geometry on `RANDOM_LAYER` (not in `WALL_LAYERS` config) | `NoWallLayersFoundError` is raised |
| `fixture_corrupt.dxf` | — | Random binary garbage bytes | `CorruptFileError` is raised by `parse_dxf()` |

---

## PDF Fixtures

| File | Tool | Content | Tests |
|------|------|---------|-------|
| `fixture_simple.pdf` | reportlab | A4-sized PDF with **12 line segments** (outer walls + interior partitions); yields ≥ 10 PyMuPDF drawing commands | PDF renders to image; `detect_pdf_content_type` → `"vector"` |
| `fixture_scanned.pdf` | PyMuPDF + Pillow | A4-sized PDF with a **single full-page raster PNG embedded** (no vector paths); floor-plan-like shapes drawn with Pillow | `detect_pdf_content_type` → `"scanned"`; `parse_pdf` renders + raises `PDFRenderedToImage` |
| `fixture_corrupt.pdf` | — | Random binary garbage bytes | `CorruptFileError` raised by `parse_pdf()` |

---

## Sentinel File

`.generated` — created by `generate_fixtures.py` after a successful run.
`tests/conftest.py` checks for this file and skips regeneration if it exists,
keeping the test suite fast on repeated runs.
