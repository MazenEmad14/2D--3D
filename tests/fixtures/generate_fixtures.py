"""
tests/fixtures/generate_fixtures.py

Generates all synthetic test fixtures for the Phase 1 parser tests.

Usage (standalone)
------------------
    python tests/fixtures/generate_fixtures.py

This script is idempotent — re-running it overwrites existing fixtures.
It is also called automatically by tests/conftest.py when the fixtures
directory does not contain the ``.generated`` sentinel file.

Fixtures generated
------------------
DXF:
  fixture_simple.dxf     — 4-wall room, millimetre units, WALLS layer
  fixture_with_door.dxf  — Same room + INSERT on DOOR layer
  fixture_bad_layers.dxf — Same geometry on RANDOM_LAYER (triggers NoWallLayersFoundError)
  fixture_corrupt.dxf    — Random bytes (triggers CorruptFileError)

PDF:
  fixture_simple.pdf     — reportlab vector PDF with 12+ line segments
  fixture_scanned.pdf    — fitz PDF embedding a full-page raster PNG image
  fixture_corrupt.pdf    — Random bytes (triggers CorruptFileError)
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DXF fixtures
# ---------------------------------------------------------------------------


def _make_simple_dxf(out_dir: Path) -> None:
    """4 walls on WALLS layer, millimetre units (5 000 × 4 000 mm = 5 × 4 m)."""
    import ezdxf

    doc = ezdxf.new(dxfversion="R2010")
    doc.header["$INSUNITS"] = 4  # millimetres
    doc.layers.add(name="WALLS")
    msp = doc.modelspace()

    # Rectangle: (0,0) → (5000,0) → (5000,4000) → (0,4000) → (0,0)
    msp.add_line((0, 0), (5000, 0), dxfattribs={"layer": "WALLS"})
    msp.add_line((5000, 0), (5000, 4000), dxfattribs={"layer": "WALLS"})
    msp.add_line((5000, 4000), (0, 4000), dxfattribs={"layer": "WALLS"})
    msp.add_line((0, 4000), (0, 0), dxfattribs={"layer": "WALLS"})

    doc.saveas(str(out_dir / "fixture_simple.dxf"))
    logger.info("Created fixture_simple.dxf")


def _make_with_door_dxf(out_dir: Path) -> None:
    """Same 4-wall room plus one INSERT on DOOR layer at (2500, 0) mm."""
    import ezdxf

    doc = ezdxf.new(dxfversion="R2010")
    doc.header["$INSUNITS"] = 4  # millimetres
    doc.layers.add(name="WALLS")
    doc.layers.add(name="DOOR")
    msp = doc.modelspace()

    # Outer walls
    msp.add_line((0, 0), (5000, 0), dxfattribs={"layer": "WALLS"})
    msp.add_line((5000, 0), (5000, 4000), dxfattribs={"layer": "WALLS"})
    msp.add_line((5000, 4000), (0, 4000), dxfattribs={"layer": "WALLS"})
    msp.add_line((0, 4000), (0, 0), dxfattribs={"layer": "WALLS"})

    # Door block definition (900 mm wide)
    door_block = doc.blocks.new(name="DOOR_SINGLE")
    door_block.add_line((0, 0), (900, 0))   # door sill
    door_block.add_line((0, 0), (0, 900))   # door leaf (closed position)

    # INSERT on DOOR layer at mid-point of the bottom wall
    msp.add_blockref(
        "DOOR_SINGLE",
        (2500, 0),
        dxfattribs={"layer": "DOOR"},
    )

    doc.saveas(str(out_dir / "fixture_with_door.dxf"))
    logger.info("Created fixture_with_door.dxf")


def _make_bad_layers_dxf(out_dir: Path) -> None:
    """Same geometry on RANDOM_LAYER — should trigger NoWallLayersFoundError."""
    import ezdxf

    doc = ezdxf.new(dxfversion="R2010")
    doc.header["$INSUNITS"] = 4
    doc.layers.add(name="RANDOM_LAYER")
    msp = doc.modelspace()

    msp.add_line((0, 0), (5000, 0), dxfattribs={"layer": "RANDOM_LAYER"})
    msp.add_line((5000, 0), (5000, 4000), dxfattribs={"layer": "RANDOM_LAYER"})
    msp.add_line((5000, 4000), (0, 4000), dxfattribs={"layer": "RANDOM_LAYER"})
    msp.add_line((0, 4000), (0, 0), dxfattribs={"layer": "RANDOM_LAYER"})

    doc.saveas(str(out_dir / "fixture_bad_layers.dxf"))
    logger.info("Created fixture_bad_layers.dxf")


def _make_corrupt_dxf(out_dir: Path) -> None:
    """Binary garbage — cannot be parsed as DXF."""
    (out_dir / "fixture_corrupt.dxf").write_bytes(
        b"NOT_A_DXF\x00\x01\x02\x03\xdeadbeef" * 16
    )
    logger.info("Created fixture_corrupt.dxf")


# ---------------------------------------------------------------------------
# PDF fixtures
# ---------------------------------------------------------------------------


def _make_simple_pdf(out_dir: Path) -> None:
    """
    reportlab vector PDF with 12 line segments representing a floor plan.
    Produces ≥ 10 PyMuPDF drawing commands → classified as 'vector'.
    """
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib.units import mm

    dest = str(out_dir / "fixture_simple.pdf")
    # A4 page in points (595 × 842)
    c = rl_canvas.Canvas(dest, pagesize=(595, 842))
    c.setLineWidth(1)

    # Outer walls
    c.line(50, 100, 400, 100)   # bottom
    c.line(400, 100, 400, 600)  # right
    c.line(400, 600, 50, 600)   # top
    c.line(50, 600, 50, 100)    # left

    # Interior partitions — adds enough commands to exceed min_drawings threshold
    c.line(200, 100, 200, 400)
    c.line(50, 350, 200, 350)
    c.line(200, 300, 400, 300)
    c.line(100, 100, 100, 200)
    c.line(50, 200, 150, 200)
    c.line(300, 100, 300, 300)
    c.line(300, 400, 400, 400)
    c.line(100, 400, 300, 400)

    c.save()
    logger.info("Created fixture_simple.pdf  (12 line segments)")


def _make_scanned_pdf(out_dir: Path) -> None:
    """
    fitz PDF with a single full-page raster PNG embedded.
    Has no drawing commands → classified as 'scanned'.
    """
    import pymupdf as fitz
    from PIL import Image, ImageDraw

    # Draw a simple floor plan on a white canvas
    img = Image.new("RGB", (595, 842), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle([30, 30, 565, 812], outline="black", width=4)
    draw.line([30, 430, 565, 430], fill="black", width=3)   # horizontal partition
    draw.line([300, 30, 300, 430], fill="black", width=3)   # vertical partition

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    pdf_doc = fitz.open()
    page = pdf_doc.new_page(width=595, height=842)
    # Embed image to cover the entire page
    page.insert_image(fitz.Rect(0, 0, 595, 842), stream=buf.getvalue())
    pdf_doc.save(str(out_dir / "fixture_scanned.pdf"))
    pdf_doc.close()
    logger.info("Created fixture_scanned.pdf  (full-page embedded raster image)")


def _make_corrupt_pdf(out_dir: Path) -> None:
    """Binary garbage — cannot be parsed as PDF."""
    (out_dir / "fixture_corrupt.pdf").write_bytes(
        b"NOT_A_PDF\x00\x01\x02\x03\xdeadbeef" * 16
    )
    logger.info("Created fixture_corrupt.pdf")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def generate_all(out_dir: Path | None = None) -> None:
    """
    Generate all fixtures into *out_dir* (default: directory of this script).
    """
    if out_dir is None:
        out_dir = Path(__file__).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    logger.info("Generating fixtures in: %s", out_dir)

    # DXF
    _make_simple_dxf(out_dir)
    _make_with_door_dxf(out_dir)
    _make_bad_layers_dxf(out_dir)
    _make_corrupt_dxf(out_dir)

    # PDF
    _make_simple_pdf(out_dir)
    _make_scanned_pdf(out_dir)
    _make_corrupt_pdf(out_dir)

    # Write sentinel so conftest.py can skip regeneration
    (out_dir / ".generated").touch()
    logger.info("All fixtures generated successfully.")


if __name__ == "__main__":
    generate_all()
