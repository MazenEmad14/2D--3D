"""
app/parsers/image_preprocessing.py

Image preprocessing for the floor plan segmentation model.

Preprocessing pipeline (mirrors the reference repo's CubiCasaDataset._load):

    1. Open image with Pillow, convert to RGB.
    2. Compute scale to fit the image inside ``target_size × target_size``
       while preserving aspect ratio.
    3. Resize using LANCZOS resampling.
    4. Create a (target_size × target_size × 3) uint8 canvas filled with the
       ImageNet mean colour (≈ [123, 116, 103] in [0, 255] space).  In
       normalized-float space this padding is equivalent to zero, which is
       how the model saw padded regions during training.
    5. Paste the resized image centred on the canvas (symmetric padding on
       both axes).
    6. Convert to float32, normalize per-channel with ImageNet mean/std.
    7. Add batch dimension: (3, H, W) → (1, 3, H, W).

The function also returns a ``letterbox_info`` dict that downstream code uses
to map mask coordinates back to the original image's pixel space.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import torch
from PIL import Image

logger = logging.getLogger(__name__)

# ImageNet statistics — must match the reference repo's data.py exactly.
IMAGENET_MEAN: tuple[float, float, float] = (0.485, 0.456, 0.406)
IMAGENET_STD: tuple[float, float, float] = (0.229, 0.224, 0.225)

#: Model input resolution.
DEFAULT_TARGET_SIZE: int = 512


def preprocess_image(
    image_path: str | Path,
    target_size: int = DEFAULT_TARGET_SIZE,
) -> tuple[torch.Tensor, dict]:
    """
    Load and preprocess a floor plan image for model inference.

    Parameters
    ----------
    image_path:
        Path to any Pillow-readable image (PNG, JPEG, TIFF, BMP …).
    target_size:
        Square canvas size to resize into.  Must match the model's expected
        input size (default: 512).

    Returns
    -------
    tensor : torch.Tensor, shape (1, 3, target_size, target_size), float32
        Normalized, letterboxed image tensor ready for the model.
    letterbox_info : dict
        Contains:
        - ``orig_w`` (int): original image width in pixels.
        - ``orig_h`` (int): original image height in pixels.
        - ``scale`` (float): scale factor applied to both dimensions.
        - ``inner_w`` (int): width of the scaled content area on the canvas.
        - ``inner_h`` (int): height of the scaled content area on the canvas.
        - ``pad_left`` (int): left padding offset on the canvas.
        - ``pad_top`` (int): top padding offset on the canvas.
        - ``target_size`` (int): always equal to ``target_size``.

    Raises
    ------
    CorruptFileError
        The file cannot be opened or is not a valid image.
    """
    from app.parsers.exceptions import CorruptFileError

    image_path = Path(image_path)
    try:
        img = Image.open(image_path).convert("RGB")
    except Exception as exc:
        raise CorruptFileError(
            f"Cannot open image '{image_path.name}': {exc}"
        ) from exc

    orig_w, orig_h = img.size

    # ------------------------------------------------------------------ #
    # 1. Compute scale + inner canvas dimensions
    # ------------------------------------------------------------------ #
    scale = min(target_size / orig_w, target_size / orig_h)
    inner_w = int(round(orig_w * scale))
    inner_h = int(round(orig_h * scale))

    # ------------------------------------------------------------------ #
    # 2. Resize image
    # ------------------------------------------------------------------ #
    resized = img.resize((inner_w, inner_h), Image.LANCZOS)

    # ------------------------------------------------------------------ #
    # 3. Create letterboxed canvas — fill with ImageNet mean colour
    #    (in uint8 space: round(mean_float * 255))
    # ------------------------------------------------------------------ #
    pad_color = tuple(round(m * 255) for m in IMAGENET_MEAN)   # (123, 116, 103)
    canvas = Image.new("RGB", (target_size, target_size), pad_color)

    pad_left = (target_size - inner_w) // 2
    pad_top = (target_size - inner_h) // 2
    canvas.paste(resized, (pad_left, pad_top))

    # ------------------------------------------------------------------ #
    # 4. Convert to float tensor + ImageNet normalization
    # ------------------------------------------------------------------ #
    arr = np.array(canvas, dtype=np.float32) / 255.0          # (H, W, 3), [0,1]
    mean = np.array(IMAGENET_MEAN, dtype=np.float32)
    std = np.array(IMAGENET_STD, dtype=np.float32)
    arr = (arr - mean) / std                                   # normalized

    # HWC → CHW, add batch dim → (1, 3, H, W)
    tensor = torch.from_numpy(arr.transpose(2, 0, 1)).unsqueeze(0)

    letterbox_info: dict = {
        "orig_w": orig_w,
        "orig_h": orig_h,
        "scale": scale,
        "inner_w": inner_w,
        "inner_h": inner_h,
        "pad_left": pad_left,
        "pad_top": pad_top,
        "target_size": target_size,
    }

    logger.debug(
        "Preprocessed '%s': orig=%dx%d scale=%.4f inner=%dx%d pad=(%d,%d)",
        image_path.name, orig_w, orig_h, scale, inner_w, inner_h, pad_left, pad_top,
    )
    return tensor, letterbox_info
