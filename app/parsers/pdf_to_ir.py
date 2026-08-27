"""
app/parsers/pdf_to_ir.py

PDF parser — Automatically detects whether a PDF is Vector (CAD) or Scanned (raster),
and routes it to the appropriate processing pipeline to output an IRFloorPlan.
"""

from __future__ import annotations

import logging
import math
import tempfile
from pathlib import Path

import pymupdf as fitz  # PyMuPDF alias

from app.parsers.exceptions import CorruptFileError, ParserError
from app.schemas.ir_schema import IRFloorPlan, Metadata, Wall, Point2D
from app.parsers.image_to_ir import parse_image

logger = logging.getLogger(__name__)

DEFAULT_RENDER_DPI: int = 300

# Fallback scale for vector PDFs that carry no embedded scale annotation.
# Agreed fallback: architectural 1/4 inch = 1 foot (common US residential drawing scale).
# This means: 1 paper inch = 4 feet = 4 * 0.3048 m = 1.2192 m.
# 1 PDF point = 1/72 paper inch, so:
#   FALLBACK_SCALE_M_PER_PT = (1/72) * 1.2192 = 0.016933 m/pt
# A 300pt-wide room (at 1:48 scale) represents 300 * 0.016933 ≈ 5.08 m — a plausible room width.
#
# PREVIOUS BUG: was set to 0.0254/72 (treating 1pt = 1 physical inch, not 1/4in=1ft),
# producing values 48x too small.
FALLBACK_SCALE_M_PER_PT: float = (1.0 / 72.0) * 1.2192  # ≈ 0.016933 m/pt

# Physical scale derived from render DPI for the scanned pipeline, using the same
# architectural fallback convention as the vector pipeline (1/4 inch = 1 foot).
# Chain: 1 px → (1/DPI) paper inches → apply architectural scale → metres.
# (1/DPI) inch × 1.2192 m/paper-inch = 1.2192/DPI m/px
#
# Note: This matches the vector pipeline's convention exactly:
#   vector: 1 pt = (1/72) inch * 1.2192 m/inch
#   scanned: 1 px = (1/DPI) inch * 1.2192 m/inch  (at DPI rendering of same pt coords)
# Both reduce to 1.2192/N m/unit where N is the units-per-paper-inch.
M_PER_PAPER_INCH_AT_FALLBACK_SCALE: float = 1.2192  # 1 paper inch = 4 feet = 1.2192m

def _dpi_to_scale_m_per_px(dpi: int) -> float:
    """Converts render DPI to m/px using the same architectural fallback as the vector pipeline."""
    return M_PER_PAPER_INCH_AT_FALLBACK_SCALE / dpi


def _is_vector_page(page: fitz.Page) -> bool:
    """
    Classification rule for Vector vs. Scanned:
    If the page contains >= 1 vector path with a visible stroke or fill
    (excluding invisible clipping paths), it is classified as Vector.
    Otherwise, Scanned.
    """
    drawings = page.get_drawings()
    visible_path_count = 0
    for d in drawings:
        # Check if it has a stroke or fill
        has_stroke = d.get("color") is not None and d.get("width", 0) > 0
        has_fill = d.get("fill") is not None
        if has_stroke or has_fill:
            visible_path_count += 1
            if visible_path_count >= 1:
                return True
    return False


def _is_axis_aligned(p1: fitz.Point, p2: fitz.Point, tol: float = 1.0) -> bool:
    """Check if a line segment is orthogonal (axis-aligned)."""
    return abs(p1.x - p2.x) <= tol or abs(p1.y - p2.y) <= tol

def _rect_to_centerline_wall(rect: fitz.Rect, scale: float) -> tuple[Point2D, Point2D, float]:
    """
    Convert a filled rectangle to a centerline-based Wall (start, end, thickness).

    The IR schema requires start/end to be on the wall *centerline*, not the outer
    perimeter. For a horizontal rectangle (w > h), the centerline runs along the
    long axis at the mid-height, and is inset by thickness/2 (= h/2) from each
    short end so the endpoint reflects the centre of the end cap, not the corner.

    For a vertical rectangle (h > w), the same logic applies rotated 90°.

    This matches the skeletonisation pipeline's medial-axis semantics exactly.
    """
    w = rect.width
    h = rect.height
    if w > h:
        # Horizontal wall: centerline runs left-right at mid y
        half_t = h / 2.0
        cx_start = rect.x0 + half_t   # inset from left outer edge
        cx_end   = rect.x1 - half_t   # inset from right outer edge
        cy       = rect.y0 + half_t   # mid-height
        start    = Point2D(cx_start * scale, cy * scale)
        end      = Point2D(cx_end   * scale, cy * scale)
    else:
        # Vertical wall: centerline runs top-bottom at mid x
        half_t  = w / 2.0
        cx      = rect.x0 + half_t
        cy_start = rect.y0 + half_t   # inset from top outer edge
        cy_end   = rect.y1 - half_t   # inset from bottom outer edge
        start   = Point2D(cx * scale, cy_start * scale)
        end     = Point2D(cx * scale, cy_end   * scale)
    thickness = min(w, h) * scale
    return start, end, thickness


def _parse_vector_pdf(page: fitz.Page, page_count: int, page_number: int) -> IRFloorPlan:
    """
    Extracts orthogonal walls from a Vector PDF page.
    Follows priority: 1. Filled Rectangles, 2. Thick Strokes.

    All start/end coordinates emitted are centerline positions (inset by thickness/2
    from the outer rectangle edges), consistent with the IR schema contract and the
    scanned pipeline's medial-axis output.
    """
    drawings = page.get_drawings()
    walls = []
    skipped_non_orthogonal = 0

    # Bounding box tracked in raw PDF points, updated with centerline endpoints
    min_x, min_y, max_x, max_y = float('inf'), float('inf'), float('-inf'), float('-inf')

    for d in drawings:
        has_stroke = d.get("color") is not None and d.get("width", 0) > 0
        has_fill = d.get("fill") is not None

        if not (has_stroke or has_fill):
            continue

        items = d.get("items", [])

        for item in items:
            cmd = item[0]
            if cmd == "re":
                # Filled rectangle: convert to centerline wall
                rect = item[1]
                w = rect.width
                h = rect.height

                # Must be elongated enough to be a wall (aspect ratio > 3)
                if w > 0 and h > 0 and (w / h > 3 or h / w > 3):
                    start, end, thickness = _rect_to_centerline_wall(rect, FALLBACK_SCALE_M_PER_PT)
                    wall_id = f"wall_{len(walls)+1}"
                    walls.append(Wall(
                        id=wall_id, 
                        start=start, 
                        end=end, 
                        thickness=thickness, 
                        height=3.0,
                        thickness_confidence="measured"
                    ))

                    # Track bbox using centerline endpoints (in pts)
                    min_x = min(min_x, start.x / FALLBACK_SCALE_M_PER_PT, end.x / FALLBACK_SCALE_M_PER_PT)
                    min_y = min(min_y, start.y / FALLBACK_SCALE_M_PER_PT, end.y / FALLBACK_SCALE_M_PER_PT)
                    max_x = max(max_x, start.x / FALLBACK_SCALE_M_PER_PT, end.x / FALLBACK_SCALE_M_PER_PT)
                    max_y = max(max_y, start.y / FALLBACK_SCALE_M_PER_PT, end.y / FALLBACK_SCALE_M_PER_PT)

            elif cmd == "l":
                # Stroked line: centerline is the line itself (stroke is centred on it)
                p1, p2 = item[1], item[2]
                if has_stroke:
                    thickness = d.get("width", 1.0) * FALLBACK_SCALE_M_PER_PT
                    if _is_axis_aligned(p1, p2):
                        # For a stroked line, PyMuPDF's coordinates ARE the centerline
                        start = Point2D(p1.x * FALLBACK_SCALE_M_PER_PT, p1.y * FALLBACK_SCALE_M_PER_PT)
                        end   = Point2D(p2.x * FALLBACK_SCALE_M_PER_PT, p2.y * FALLBACK_SCALE_M_PER_PT)
                        wall_id = f"wall_{len(walls)+1}"
                        walls.append(Wall(
                            id=wall_id, 
                            start=start, 
                            end=end, 
                            thickness=thickness, 
                            height=3.0,
                            thickness_confidence="measured"
                        ))

                        min_x = min(min_x, p1.x, p2.x)
                        min_y = min(min_y, p1.y, p2.y)
                        max_x = max(max_x, p1.x, p2.x)
                        max_y = max(max_y, p1.y, p2.y)
                    else:
                        skipped_non_orthogonal += 1

    if len(walls) == 0:
        min_x, min_y, max_x, max_y = 0, 0, 10, 10

    floor_poly = [
        Point2D(min_x * FALLBACK_SCALE_M_PER_PT, min_y * FALLBACK_SCALE_M_PER_PT),
        Point2D(max_x * FALLBACK_SCALE_M_PER_PT, min_y * FALLBACK_SCALE_M_PER_PT),
        Point2D(max_x * FALLBACK_SCALE_M_PER_PT, max_y * FALLBACK_SCALE_M_PER_PT),
        Point2D(min_x * FALLBACK_SCALE_M_PER_PT, max_y * FALLBACK_SCALE_M_PER_PT)
    ]

    metadata = Metadata(
        unit="meter",
        source_type="pdf_vector",
        scale_confidence="estimated",
        page_count=page_count,
        page_processed=page_number,
        walls_skipped_non_orthogonal=skipped_non_orthogonal
    )

    return IRFloorPlan(metadata=metadata, walls=walls, openings=[], floor_polygon=floor_poly)


def _parse_scanned_pdf(page: fitz.Page, page_count: int, page_number: int, original_path: Path, dpi: int = DEFAULT_RENDER_DPI) -> IRFloorPlan:
    """
    Renders a scanned PDF page to an image and routes it to the image pipeline.
    Passes a DPI-derived physical scale (metres/pixel) so mask_to_ir uses the
    correct physical size rather than the hardcoded 0.01 m/px model-space heuristic.
    """
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    pixmap = page.get_pixmap(matrix=matrix, alpha=False)
    
    out_dir = Path(tempfile.gettempdir())
    image_path = out_dir / f"{original_path.stem}_p{page_number}_{dpi}dpi.png"
    pixmap.save(str(image_path))
    
    # Compute physical scale from render DPI: 1 px = 0.0254/DPI metres.
    # This maps each rendered pixel to its true physical size, consistent with
    # the vector pipeline's fallback scale.
    physical_scale = _dpi_to_scale_m_per_px(dpi)
    
    # Route to image pipeline, passing the DPI-derived scale
    ir = parse_image(image_path, scale_m_per_px=physical_scale)
    
    # Override metadata
    ir.metadata.source_type = "pdf_scanned"
    ir.metadata.page_count = page_count
    ir.metadata.page_processed = page_number
    
    return ir


def parse_pdf(
    path: str | Path,
    *,
    page_number: int = 1
) -> IRFloorPlan:
    """
    Parse a PDF floor plan (Vector or Scanned) and return a validated :class:`IRFloorPlan`.
    """
    path = Path(path)

    if not path.exists():
        raise CorruptFileError(f"PDF file not found: '{path}'")

    try:
        doc = fitz.open(str(path))
    except Exception as exc:
        raise CorruptFileError(f"Cannot open PDF '{path.name}': {exc}") from exc

    if doc.needs_pass:
        doc.close()
        raise ParserError("PDF is password protected")

    page_count = doc.page_count
    if page_count == 0:
        doc.close()
        raise CorruptFileError(f"PDF '{path.name}' contains no pages.")
        
    if page_number < 1 or page_number > page_count:
        doc.close()
        raise ParserError(f"Invalid page_number {page_number}. PDF has {page_count} pages.")

    page = doc[page_number - 1]
    
    try:
        if _is_vector_page(page):
            ir = _parse_vector_pdf(page, page_count, page_number)
        else:
            ir = _parse_scanned_pdf(page, page_count, page_number, path)
    finally:
        doc.close()
        
    ir.validate()
    return ir
