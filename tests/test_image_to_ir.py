"""
tests/test_image_to_ir.py

Tests for the top-level orchestrator app.parsers.image_to_ir.parse_image().
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from app.parsers.exceptions import CorruptFileError, SegmentationError
from app.parsers.image_to_ir import parse_image
from app.schemas.ir_schema import IRFloorPlan


# ---------------------------------------------------------------------------
# Fixtures & Mocks
# ---------------------------------------------------------------------------

REAL_FP_DIR = Path(__file__).parent / "fixtures" / "real_floorplans"
SAMPLE_1 = REAL_FP_DIR / "sample_fp_1.png"

# We mock FloorplanSegmenter to avoid a 100MB download and 3s inference
# in every test run unless explicitly testing the model (which we do in
# test_floorplan_segmenter.py).


@pytest.fixture
def mock_segmenter():
    """Mock the segmenter to return a basic 512x512 mask with a wall."""
    with patch("app.models.floorplan_segmenter.get_segmenter") as mock_get:
        class DummySegmenter:
            def segment(self, image_path: str):
                # Return a mask with one wall to pass downstream mask_to_ir checks
                mask = np.zeros((512, 512), dtype=np.uint8)
                mask[100:150, 100:400] = 1  # wall class
                
                info = {
                    "orig_w": 512, "orig_h": 512,
                    "scale": 1.0,
                    "inner_w": 512, "inner_h": 512,
                    "pad_left": 0, "pad_top": 0,
                    "target_size": 512,
                }
                return mask, info

        mock_get.return_value = DummySegmenter()
        yield mock_get


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestParseImageValidation:
    def test_missing_file_raises_corrupt_file_error(self):
        with pytest.raises(CorruptFileError, match="not found"):
            parse_image("does_not_exist.png")

    def test_non_image_file_raises_corrupt_file_error(self, tmp_path):
        bad_file = tmp_path / "not_an_image.txt"
        bad_file.write_text("this is a text file")
        with pytest.raises(CorruptFileError, match="valid image"):
            parse_image(bad_file)


class TestParseImageHappyPath:
    def test_returns_valid_ir(self, mock_segmenter):
        ir = parse_image(SAMPLE_1)
        assert isinstance(ir, IRFloorPlan)
        assert len(ir.walls) >= 1

    def test_accepts_pathlib_path(self, mock_segmenter):
        ir = parse_image(Path(SAMPLE_1))
        assert isinstance(ir, IRFloorPlan)

    def test_default_scale_confidence_estimated(self, mock_segmenter):
        ir = parse_image(SAMPLE_1)
        assert ir.metadata.scale_confidence == "estimated"

    def test_calibrated_scale_confidence_high(self, mock_segmenter):
        # We need the mock to return a door for calibration to work
        def segment_with_door(image_path):
            mask = np.zeros((512, 512), dtype=np.uint8)
            mask[100:150, 100:400] = 1  # wall class
            mask[120:130, 200:250] = 2  # door class
            info = {
                "orig_w": 512, "orig_h": 512,
                "scale": 1.0,
                "inner_w": 512, "inner_h": 512,
                "pad_left": 0, "pad_top": 0,
                "target_size": 512,
            }
            return mask, info

        mock_segmenter.return_value.segment = segment_with_door
        ir = parse_image(SAMPLE_1, known_door_width_m=0.9)
        assert ir.metadata.scale_confidence == "high"


class TestParseImageErrorPropagation:
    def test_model_inference_error_raises_segmentation_error(self, mock_segmenter):
        def bad_segment(image_path):
            raise RuntimeError("PyTorch OOM")

        mock_segmenter.return_value.segment = bad_segment
        with pytest.raises(SegmentationError, match="failed.*PyTorch OOM"):
            parse_image(SAMPLE_1)

    @patch("app.parsers.image_preprocessing.preprocess_image")
    def test_preprocessing_error_raises_segmentation_error(self, mock_pre, mock_segmenter):
        mock_pre.side_effect = ValueError("bad array shape")
        with pytest.raises(SegmentationError, match="Preprocessing failed.*bad array shape"):
            parse_image(SAMPLE_1)
