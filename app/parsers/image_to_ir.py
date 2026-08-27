"""
app/parsers/image_to_ir.py

Orchestration: image file → IRFloorPlan via segmentation model.

This is the single entry point that both:
- Phase 1's ``PDFRenderedToImage.image_path`` (PDF rendered to PNG), and
- Direct image uploads from the future Phase 4 API,
call into.

Pipeline
--------
1. Validate that the file exists and Pillow can open it (``CorruptFileError``).
2. Preprocess: letterbox resize + ImageNet normalisation → (1, 3, 512, 512) tensor.
3. Run inference: ``FloorplanSegmenter.segment()`` → (512, 512) class-index mask.
4. Postprocess: ``mask_to_ir()`` → IRFloorPlan.
5. Propagate known exceptions; wrap unexpected errors in ``SegmentationError``.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from app.parsers.exceptions import CorruptFileError, SegmentationError
from app.schemas.ir_schema import IRFloorPlan

logger = logging.getLogger(__name__)


def parse_image(
    image_path: str | Path,
    *,
    known_door_width_m: float | None = None,
    scale_m_per_px: float | None = None,
) -> IRFloorPlan:
    """
    Segment a floor plan image and return a validated :class:`IRFloorPlan`.

    Parameters
    ----------
    image_path:
        Path to a PNG / JPEG (or any Pillow-readable format) floor plan image.
        This may be:
        - An image directly uploaded by the user (future Phase 4 API).
        - A PNG produced by Phase 1's ``parse_pdf()`` (``PDFRenderedToImage.image_path``).
    known_door_width_m:
        Optional real-world door width in metres used to calibrate the
        pixel-to-metre scale.  If provided, ``metadata.scale_confidence``
        is set to ``"high"``; otherwise ``"estimated"`` (default 0.01 m/px).

    Returns
    -------
    IRFloorPlan
        Validated IR document.  ``walls`` may be empty if the model detected
        no walls — this is a valid (though unusual) result; callers should
        warn the user rather than treating it as an error.

    Raises
    ------
    CorruptFileError
        The file does not exist or cannot be opened as an image.
    SegmentationError
        Any other failure during model loading or inference.
    """
    image_path = Path(image_path)

    # ------------------------------------------------------------------
    # 1. Validate image readability up-front (fast fail before model load)
    # ------------------------------------------------------------------
    if not image_path.exists():
        raise CorruptFileError(f"Image file not found: '{image_path}'")

    try:
        from PIL import Image as PILImage
        with PILImage.open(image_path) as probe:
            probe.verify()  # raises on corrupt files
    except CorruptFileError:
        raise
    except Exception as exc:
        raise CorruptFileError(
            f"Cannot open image '{image_path.name}' as a valid image: {exc}"
        ) from exc

    # ------------------------------------------------------------------
    # 2. Preprocessing
    # ------------------------------------------------------------------
    try:
        from app.parsers.image_preprocessing import preprocess_image
        tensor, letterbox_info = preprocess_image(image_path)
    except CorruptFileError:
        raise
    except Exception as exc:
        raise SegmentationError(
            f"Preprocessing failed for '{image_path.name}': {exc}"
        ) from exc

    # ------------------------------------------------------------------
    # 3. Model inference
    # ------------------------------------------------------------------
    try:
        from app.models.floorplan_segmenter import get_segmenter
        segmenter = get_segmenter()

        t0 = time.perf_counter()
        mask, _ = segmenter.segment(str(image_path))  # letterbox_info already computed
        elapsed = time.perf_counter() - t0

        logger.info(
            "Segmentation of '%s' completed in %.2f s.", image_path.name, elapsed
        )
    except CorruptFileError:
        raise
    except Exception as exc:
        raise SegmentationError(
            f"Segmentation model failed for '{image_path.name}': {exc}"
        ) from exc

    # ------------------------------------------------------------------
    # 4. Postprocessing
    # ------------------------------------------------------------------
    try:
        from app.parsers.mask_to_ir import mask_to_ir
        ir = mask_to_ir(
            mask,
            letterbox_info,
            image_path,
            known_door_width_m=known_door_width_m,
            scale_m_per_px=scale_m_per_px,
        )
    except Exception as exc:
        raise SegmentationError(
            f"Postprocessing (mask→IR) failed for '{image_path.name}': {exc}"
        ) from exc

    return ir
