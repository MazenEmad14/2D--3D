"""
app/parsers/exceptions.py

Custom exception hierarchy for all parser modules.

Design rationale
----------------
Using typed exceptions (instead of generic ValueError/IOError) lets the
Phase 4 API layer map each failure mode to a specific HTTP response code
without inspecting error messages:

    CorruptFileError           → 422 Unprocessable Entity
    UnsupportedFileFormatError → 415 Unsupported Media Type
    NoWallLayersFoundError     → 422 Unprocessable Entity  (DXF-specific)
    PDFRenderedToImage         → used as a routing signal, not an error
"""

from __future__ import annotations


class CorruptFileError(IOError):
    """
    Raised when a file cannot be opened or is structurally invalid.

    Examples
    --------
    - A .dxf file that is not valid DXF (e.g. truncated, wrong encoding).
    - A .pdf file that cannot be parsed by PyMuPDF.
    - A file that exists on disk but is empty or contains only garbage bytes.
    """


class ParserError(ValueError):
    """
    Raised when a parser encounters a structural/semantic error that prevents parsing, 
    but the file itself is not technically corrupt (e.g. password protected, missing pages).
    """


class UnsupportedFileFormatError(ValueError):
    """
    Raised when the file extension or detected format is not handled.

    Examples
    --------
    - A .dwg file submitted where a .dxf is expected.
    - A file with no extension passed to a parser that requires one.
    """


class NoWallLayersFoundError(ValueError):
    """
    Raised by the DXF parser when no entities exist on any recognised
    wall layer (as defined in ``app/parsers/layer_config.WALL_LAYERS``).

    This indicates either:
    - The DXF uses non-standard layer names → extend WALL_LAYERS in layer_config.py.
    - The file is a non-architectural DXF (e.g. mechanical drawing).
    """


class PDFRenderedToImage(Exception):
    """
    Raised by ``parse_pdf()`` after successfully rendering the PDF to a PNG.

    This is a *routing signal*, not an error. The caller (Phase 4 API layer)
    should catch this exception and forward ``image_path`` to the Phase 2
    image segmentation pipeline.

    Attributes
    ----------
    image_path : str
        Absolute path to the rendered PNG file on disk.
    dpi : int
        The DPI at which the page was rendered.
    source_pdf : str
        Absolute path to the original PDF file.
    """

    def __init__(self, image_path: str, dpi: int = 150, source_pdf: str = "") -> None:
        self.image_path = image_path
        self.dpi = dpi
        self.source_pdf = source_pdf
        super().__init__(
            f"PDF rendered to image at '{image_path}' "
            f"({dpi} DPI). Route to Phase 2 image pipeline."
        )


class SegmentationError(RuntimeError):
    """
    Raised by ``parse_image()`` when the segmentation model fails to run.

    Examples
    --------
    - Model weights could not be downloaded from Hugging Face Hub.
    - An internal PyTorch or smp error occurred during inference.
    - The model produced an output of unexpected shape.
    """


# Backward-compatibility alias (matches the name used in the original plan).
ScannedPDFDetected = PDFRenderedToImage
