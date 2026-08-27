"""
app/models/floorplan_segmenter.py

ResNet-34 / UNet segmentation model for floor plan parsing.

Architecture findings (reference repo analysis)
------------------------------------------------
The model at Yytsi/floorplan-to-3d-walls was trained using the
``segmentation_models_pytorch`` (smp) library:

    smp.Unet(
        encoder_name    = "resnet34",
        encoder_weights = None,       # weights loaded separately from .safetensors
        in_channels     = 3,
        classes         = 4,
    )

This was confirmed by reading ``src/buildingcv/model.py`` from the reference
repository, which contains:

    import segmentation_models_pytorch as smp
    def build_model(encoder_name="resnet34", ...):
        return smp.Unet(...)

Class taxonomy (``src/buildingcv/labels.py``):
    0 = floor,  1 = wall,  2 = door,  3 = window

Initialization strategy — lazy singleton
-----------------------------------------
A module-level ``_INSTANCE`` is created on first call to ``get_segmenter()``.
Rationale:
- Flask's import phase happens before the first request, but model loading
  (~2-3 s on CPU) should not block startup — especially in development where
  ``create_app()`` is called many times.
- Lazy loading keeps test imports fast: tests that do not exercise the model
  (e.g. DXF parser tests) pay zero startup cost.
- Thread-safety: ``_LOCK`` ensures only one thread performs the model download
  and weight loading; subsequent threads reuse the cached instance.
- To pre-warm the model at startup, call ``get_segmenter()`` inside
  ``create_app()`` (or a Flask ``before_serving`` hook) after any
  ``app.config`` setup is complete.

Weight caching
--------------
Model files are cached under ``app/models/weights/`` (gitignored).
On a fresh clone the first call downloads ~98 MB from the HF Hub.
Subsequent calls use the local cache via ``huggingface_hub.hf_hub_download``.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import torch

if TYPE_CHECKING:
    import segmentation_models_pytorch as smp  # only for type hints

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HF_REPO_ID: str = "Yytsi/floorplan-to-3d-walls"

#: Local weights directory (gitignored).
WEIGHTS_DIR: Path = Path(__file__).parent / "weights"

#: Class indices must match labels.py in the reference repo.
CLASS_FLOOR: int = 0
CLASS_WALL: int = 1
CLASS_DOOR: int = 2
CLASS_WINDOW: int = 3
NUM_CLASSES: int = 4

#: ImageNet normalization constants (from data.py in the reference repo).
IMAGENET_MEAN: tuple[float, float, float] = (0.485, 0.456, 0.406)
IMAGENET_STD: tuple[float, float, float] = (0.229, 0.224, 0.225)

# ---------------------------------------------------------------------------
# Singleton state
# ---------------------------------------------------------------------------

_INSTANCE: "FloorplanSegmenter | None" = None
_LOCK: threading.Lock = threading.Lock()


# ---------------------------------------------------------------------------
# Model class
# ---------------------------------------------------------------------------


class FloorplanSegmenter:
    """
    Wraps the pretrained ResNet-34 / UNet segmentation model.

    Do not instantiate directly — use :func:`get_segmenter`.

    Parameters
    ----------
    device:
        PyTorch device to run inference on.  Must be CPU in Phase 2;
        GPU support is not a requirement and is **not tested**.
    """

    def __init__(self, device: torch.device | None = None) -> None:
        if device is None:
            device = torch.device("cpu")
        self.device = device
        self._model: "smp.Unet" = self._load_model()

    # ------------------------------------------------------------------ #
    # Private helpers
    # ------------------------------------------------------------------ #

    def _download_weights(self) -> tuple[Path, Path]:
        """
        Download ``best.safetensors`` and ``config.yaml`` from the HF Hub
        (if not already cached) and return their local paths.
        """
        from huggingface_hub import hf_hub_download

        WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)

        logger.info(
            "Downloading model weights from '%s' (first run only) …", HF_REPO_ID
        )
        weights_path = hf_hub_download(
            repo_id=HF_REPO_ID,
            filename="best.safetensors",
            local_dir=str(WEIGHTS_DIR),
            local_dir_use_symlinks=False,
        )
        config_path = hf_hub_download(
            repo_id=HF_REPO_ID,
            filename="config.yaml",
            local_dir=str(WEIGHTS_DIR),
            local_dir_use_symlinks=False,
        )
        logger.info("Weights cached at '%s'.", weights_path)
        return Path(weights_path), Path(config_path)

    def _load_model(self) -> "smp.Unet":
        """Build the UNet, load safetensors weights, and set eval mode."""
        import segmentation_models_pytorch as smp
        from safetensors.torch import load_file

        weights_path, _config_path = self._download_weights()

        # Build architecture — encoder_weights=None because we load our own
        model = smp.Unet(
            encoder_name="resnet34",
            encoder_weights=None,   # weights loaded below from .safetensors
            in_channels=3,
            classes=NUM_CLASSES,
        )

        state_dict = load_file(str(weights_path), device="cpu")
        model.load_state_dict(state_dict)
        model.to(self.device)
        model.eval()

        logger.info(
            "FloorplanSegmenter loaded on %s (%d parameters).",
            self.device, sum(p.numel() for p in model.parameters()),
        )
        return model

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    @torch.no_grad()
    def segment(
        self, image_path: str
    ) -> tuple[np.ndarray, dict]:
        """
        Run segmentation on a floor plan image.

        Parameters
        ----------
        image_path:
            Path to a PNG / JPEG image.  The image is preprocessed internally
            (letterbox + ImageNet normalization at 512×512) before inference.

        Returns
        -------
        mask : np.ndarray, shape (H, W), dtype uint8
            Per-pixel class-index array at the model's input resolution
            (512×512 by default).  Values: 0=floor, 1=wall, 2=door, 3=window.
        letterbox_info : dict
            Letterbox metadata required to map mask coordinates back to the
            original image's pixel space:
            - ``orig_w``, ``orig_h``: original image dimensions.
            - ``scale``: uniform scale factor (px in original → px in 512×512).
            - ``pad_top``, ``pad_left``: padding offsets in the 512×512 tensor.
            - ``inner_w``, ``inner_h``: pixel dimensions of the content area
              (the non-padded part of the 512×512 tensor).
            - ``target_size``: always 512.
        """
        from app.parsers.image_preprocessing import preprocess_image

        tensor, letterbox_info = preprocess_image(image_path)
        tensor = tensor.to(self.device)

        logits = self._model(tensor)          # (1, 4, 512, 512)
        mask = logits.argmax(dim=1).squeeze(0).cpu().numpy().astype(np.uint8)
        return mask, letterbox_info


# ---------------------------------------------------------------------------
# Module-level singleton accessor
# ---------------------------------------------------------------------------


def get_segmenter() -> FloorplanSegmenter:
    """
    Return the process-wide :class:`FloorplanSegmenter` instance.

    Thread-safe.  Loads weights on first call (blocks for ~2-3 s on CPU).
    Subsequent calls return the cached instance immediately.
    """
    global _INSTANCE
    if _INSTANCE is not None:
        return _INSTANCE
    with _LOCK:
        # Double-checked locking — check again inside lock
        if _INSTANCE is None:
            _INSTANCE = FloorplanSegmenter()
    return _INSTANCE


def reset_segmenter() -> None:
    """
    Force the next call to :func:`get_segmenter` to reload the model.

    Intended for testing only — do NOT call in production code.
    """
    global _INSTANCE
    with _LOCK:
        _INSTANCE = None
