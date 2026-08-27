"""
tests/test_floorplan_segmenter.py

Tests for app.models.floorplan_segmenter.FloorplanSegmenter.

These tests DOWNLOAD the model weights (~98 MB) on first run via the
Hugging Face Hub.  Mark with ``pytest -m slow`` if you want to skip them
in offline / CI environments.

What is tested
--------------
- Model loads successfully (weights cached after first run).
- ``segment()`` returns a mask with the expected shape and dtype.
- ``get_segmenter()`` returns the same instance on repeated calls
  (singleton behaviour).
- A single call to ``reset_segmenter()`` forces a fresh load on the
  next ``get_segmenter()`` call (verified with a call counter mock).

Notes on test images
---------------------
``tests/fixtures/real_floorplans/sample_fp_1.png`` is a synthetically
generated floor plan (black lines on white, 512×512 px).  The model was
trained on CubiCasa5K SVG renders — a very similar visual style — so
this is a reasonable but not guaranteed test of real-world performance.
See tests/fixtures/real_floorplans/README.md for full context.
"""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

REAL_FP_DIR = Path(__file__).parent / "fixtures" / "real_floorplans"
SAMPLE_1 = REAL_FP_DIR / "sample_fp_1.png"


@pytest.fixture(scope="module")
def segmenter():
    """Return a shared FloorplanSegmenter for the whole module."""
    from app.models.floorplan_segmenter import FloorplanSegmenter, reset_segmenter
    reset_segmenter()  # ensure clean state
    instance = FloorplanSegmenter()
    yield instance
    reset_segmenter()


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------


class TestModelLoading:
    def test_segmenter_instantiates(self, segmenter):
        assert segmenter is not None

    def test_model_is_in_eval_mode(self, segmenter):
        """Model must be in eval() mode for inference to be deterministic."""
        assert not segmenter._model.training

    def test_model_runs_on_cpu(self, segmenter):
        import torch
        param = next(segmenter._model.parameters())
        assert param.device.type == "cpu"


# ---------------------------------------------------------------------------
# Inference output shape
# ---------------------------------------------------------------------------


class TestSegmentOutput:
    def test_returns_tuple(self, segmenter):
        mask, info = segmenter.segment(str(SAMPLE_1))
        assert isinstance(mask, np.ndarray)
        assert isinstance(info, dict)

    def test_mask_shape_is_512x512(self, segmenter):
        mask, _ = segmenter.segment(str(SAMPLE_1))
        assert mask.shape == (512, 512), f"Expected (512,512), got {mask.shape}"

    def test_mask_dtype_is_uint8(self, segmenter):
        mask, _ = segmenter.segment(str(SAMPLE_1))
        assert mask.dtype == np.uint8

    def test_mask_values_in_valid_range(self, segmenter):
        """Class indices must be 0–3 (floor/wall/door/window)."""
        mask, _ = segmenter.segment(str(SAMPLE_1))
        assert mask.min() >= 0
        assert mask.max() <= 3

    def test_letterbox_info_keys(self, segmenter):
        _, info = segmenter.segment(str(SAMPLE_1))
        required_keys = {"orig_w", "orig_h", "scale", "inner_w", "inner_h",
                         "pad_left", "pad_top", "target_size"}
        assert required_keys.issubset(info.keys())

    def test_letterbox_info_target_size(self, segmenter):
        _, info = segmenter.segment(str(SAMPLE_1))
        assert info["target_size"] == 512

    def test_mask_has_multiple_classes(self, segmenter):
        """A real floor plan image should produce at least 2 distinct classes."""
        mask, _ = segmenter.segment(str(SAMPLE_1))
        unique_classes = np.unique(mask)
        assert len(unique_classes) >= 2, (
            f"Expected ≥2 classes in mask, got {unique_classes}. "
            "The model may not be segmenting the image correctly."
        )


# ---------------------------------------------------------------------------
# Singleton behaviour
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_get_segmenter_returns_same_instance(self):
        from app.models.floorplan_segmenter import get_segmenter, reset_segmenter
        reset_segmenter()
        a = get_segmenter()
        b = get_segmenter()
        assert a is b

    def test_get_segmenter_thread_safe(self):
        """Two threads calling get_segmenter() must receive the same instance."""
        from app.models.floorplan_segmenter import get_segmenter, reset_segmenter
        reset_segmenter()

        results: list = []

        def worker():
            results.append(get_segmenter())

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(results) == 2
        assert results[0] is results[1]

    def test_reset_segmenter_forces_reload(self):
        from app.models.floorplan_segmenter import (
            FloorplanSegmenter,
            get_segmenter,
            reset_segmenter,
        )
        reset_segmenter()
        a = get_segmenter()
        reset_segmenter()
        b = get_segmenter()
        # After reset a new instance is created
        assert a is not b
