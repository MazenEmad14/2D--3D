"""
app/parsers/pdf_content_detector.py

Classify a PDF page as containing vector drawing data or raster imagery.

Role in Phase 1
---------------
This function is retained for **metadata and logging purposes only**.
The ``parse_pdf()`` function in ``pdf_to_ir.py`` renders ALL PDFs to an
image and hands them off to the Phase 2 image pipeline, regardless of
this classification.

The result of ``detect_pdf_content_type()`` may be used to:
- Populate ``metadata.source_type`` ("pdf_vector" vs "pdf_scanned").
- Log useful diagnostic information about input files.
- Support future conditional logic if the design changes.

Heuristic
---------
1. Count vector drawing commands on page 0 via ``page.get_drawings()``.
   If the count ≥ ``min_drawings`` (default: 10) → classify as ``"vector"``.

2. If drawings are sparse/absent, check embedded raster images via
   ``page.get_images()``.  If any image occupies ≥ ``min_image_coverage``
   (default: 50 %) of the page area → classify as ``"scanned"``.

3. Default: ``"vector"`` (sparse vector drawing, not a full-page scan).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

import pymupdf as fitz  # PyMuPDF — use pymupdf alias to silence deprecation warning

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default thresholds (overridable via kwargs for testing)
# ---------------------------------------------------------------------------

#: Minimum number of PyMuPDF drawing-command objects to classify as "vector".
MIN_VECTOR_DRAWING_COMMANDS: int = 10

#: Fraction of page area an image must cover to trigger "scanned" classification.
MIN_IMAGE_COVERAGE_FRACTION: float = 0.50


def detect_pdf_content_type(
    pdf_path: str | Path,
    *,
    min_drawings: int = MIN_VECTOR_DRAWING_COMMANDS,
    min_image_coverage: float = MIN_IMAGE_COVERAGE_FRACTION,
) -> Literal["vector", "scanned"]:
    """
    Inspect page 0 of *pdf_path* and return its content classification.

    Parameters
    ----------
    pdf_path:
        Path to the PDF file.
    min_drawings:
        Minimum PyMuPDF drawing-command count to classify as ``"vector"``.
    min_image_coverage:
        Image-to-page area fraction threshold to classify as ``"scanned"``.

    Returns
    -------
    ``"vector"`` or ``"scanned"``
    """
    pdf_path = Path(pdf_path)
    doc = fitz.open(str(pdf_path))

    try:
        if doc.page_count == 0:
            logger.warning(
                "PDF '%s' has no pages; defaulting to 'vector'.", pdf_path.name
            )
            return "vector"

        page = doc[0]
        page_rect = page.rect
        page_area = page_rect.width * page_rect.height

        # ---------------------------------------------------------------- #
        # Step 1 — count vector drawing commands
        # ---------------------------------------------------------------- #
        drawings = page.get_drawings()
        drawing_count = len(drawings)
        logger.debug(
            "'%s': %d drawing command(s) on page 0 (threshold=%d).",
            pdf_path.name, drawing_count, min_drawings,
        )

        if drawing_count >= min_drawings:
            logger.info("'%s' classified as 'vector' (%d drawings).", pdf_path.name, drawing_count)
            return "vector"

        # ---------------------------------------------------------------- #
        # Step 2 — check raster image coverage
        # ---------------------------------------------------------------- #
        for img_tuple in page.get_images(full=True):
            xref = img_tuple[0]
            try:
                rects = page.get_image_rects(xref)
            except Exception:
                continue  # image rect unavailable — skip this image

            for rect in rects:
                if page_area > 0:
                    coverage = (rect.width * rect.height) / page_area
                else:
                    coverage = 0.0

                logger.debug(
                    "'%s': image xref=%d coverage=%.1f%% (threshold=%.0f%%).",
                    pdf_path.name, xref, coverage * 100, min_image_coverage * 100,
                )
                if coverage >= min_image_coverage:
                    logger.info(
                        "'%s' classified as 'scanned' (image coverage %.1f%%).",
                        pdf_path.name, coverage * 100,
                    )
                    return "scanned"

        # ---------------------------------------------------------------- #
        # Step 3 — default
        # ---------------------------------------------------------------- #
        logger.info(
            "'%s' classified as 'vector' (sparse drawings=%d, no large image).",
            pdf_path.name, drawing_count,
        )
        return "vector"

    finally:
        doc.close()
