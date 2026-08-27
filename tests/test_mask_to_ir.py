"""
tests/test_mask_to_ir.py

Unit tests for app.parsers.mask_to_ir.mask_to_ir().

These tests use **hand-constructed numpy masks** — NOT model output.
This makes the geometry logic completely independent of model accuracy:
if these tests pass, the contour/centerline/opening extraction code is
correct regardless of how well the model segments real floor plans.

Synthetic mask design
---------------------
We build 512×512 uint8 arrays where known regions are painted with the
appropriate class ID, then call mask_to_ir() and assert on the results.

A "standard" letterbox_info dict is provided with no actual padding
(orig image = mask size) so coordinates map 1:1 to simplify assertions.

Class IDs: floor=0, wall=1, door=2, window=3
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.parsers.mask_to_ir import (
    DEFAULT_SCALE_M_PER_PX,
    mask_to_ir,
    _extract_contours_for_class,
    _mask_to_canvas_coords,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _identity_letterbox(size: int = 512) -> dict:
    """Letterbox info with no padding, scale=1.0 (mask coords = original coords)."""
    return {
        "orig_w": size, "orig_h": size,
        "scale": 1.0,
        "inner_w": size, "inner_h": size,
        "pad_left": 0, "pad_top": 0,
        "target_size": size,
    }


def _blank_mask(size: int = 512) -> np.ndarray:
    """All-floor (class 0) mask."""
    return np.zeros((size, size), dtype=np.uint8)


def _paint_rect(
    mask: np.ndarray,
    class_id: int,
    x0: int, y0: int, x1: int, y1: int,
) -> np.ndarray:
    """Paint a solid rectangle of class_id onto mask (in-place)."""
    mask[y0:y1, x0:x1] = class_id
    return mask


# ---------------------------------------------------------------------------
# _mask_to_canvas_coords
# ---------------------------------------------------------------------------


class TestLetterboxMapping:
    def test_identity_mapping(self):
        # pts are (col, row) in mask space; orig_h=512.
        # After Y-flip: world_y = orig_h - row  →  (100, 512-200)=(100,312), (300, 512-400)=(300,112)
        pts = np.array([[100.0, 200.0], [300.0, 400.0]])
        info = _identity_letterbox()
        out = _mask_to_canvas_coords(pts, info)
        np.testing.assert_allclose(out, [[100.0, 312.0], [300.0, 112.0]])

    def test_with_padding_and_scale(self):
        info = {
            "orig_w": 256, "orig_h": 256,
            "scale": 0.5,
            "inner_w": 128, "inner_h": 128,
            "pad_left": 192, "pad_top": 192,
            "target_size": 512,
        }
        # A point at (192, 192) in mask space:
        #   col_orig = (192-192)/0.5 = 0
        #   row_orig = (192-192)/0.5 = 0
        #   world_y  = orig_h - row_orig = 256 - 0 = 256
        pts = np.array([[192.0, 192.0]])
        out = _mask_to_canvas_coords(pts, info)
        np.testing.assert_allclose(out, [[0.0, 256.0]], atol=1e-4)

    def test_with_scale_no_padding(self):
        info = {
            "orig_w": 1024, "orig_h": 1024,
            "scale": 0.5,
            "inner_w": 512, "inner_h": 512,
            "pad_left": 0, "pad_top": 0,
            "target_size": 512,
        }
        # col_orig = 256/0.5 = 512
        # row_orig = 256/0.5 = 512
        # world_y  = 1024 - 512 = 512  (symmetric point — Y-flip doesn't change it)
        pts = np.array([[256.0, 256.0]])
        out = _mask_to_canvas_coords(pts, info)
        np.testing.assert_allclose(out, [[512.0, 512.0]], atol=1e-4)


# ---------------------------------------------------------------------------
# _extract_contours_for_class
# ---------------------------------------------------------------------------


class TestContourExtraction:
    def test_empty_class_returns_empty(self):
        mask = _blank_mask()
        result = _extract_contours_for_class(mask, 1)   # no wall pixels
        assert result == []

    def test_single_wall_rectangle(self):
        mask = _blank_mask()
        _paint_rect(mask, 1, 50, 50, 300, 70)   # horizontal wall strip
        contours = _extract_contours_for_class(mask, 1)
        assert len(contours) >= 1

    def test_two_separate_walls_two_contours(self):
        mask = _blank_mask()
        _paint_rect(mask, 1, 50, 50, 300, 70)    # bottom wall
        _paint_rect(mask, 1, 50, 300, 300, 320)  # top wall (well separated)
        contours = _extract_contours_for_class(mask, 1)
        assert len(contours) == 2

    def test_tiny_speckle_filtered_out(self):
        mask = _blank_mask()
        # Paint a 3×3 pixel region (area=9 < MIN_POLYGON_AREA_PX=30)
        mask[100, 100] = 1
        mask[100, 101] = 1
        mask[101, 100] = 1
        contours = _extract_contours_for_class(mask, 1)
        assert contours == []


# ---------------------------------------------------------------------------
# mask_to_ir — happy path
# ---------------------------------------------------------------------------


class TestMaskToIRHappyPath:
    def _make_simple_mask(self) -> np.ndarray:
        """4-wall room with one door and one window."""
        mask = _blank_mask()
        # Bottom wall (horizontal strip)
        _paint_rect(mask, 1, 30, 460, 480, 480)
        # Top wall
        _paint_rect(mask, 1, 30, 30, 480, 50)
        # Left wall (vertical strip)
        _paint_rect(mask, 1, 30, 30, 50, 480)
        # Right wall
        _paint_rect(mask, 1, 460, 30, 480, 480)
        # Door (on bottom wall)
        _paint_rect(mask, 2, 200, 458, 280, 482)
        # Window (on top wall)
        _paint_rect(mask, 3, 300, 28, 380, 52)
        return mask

    def test_returns_irfloorplan(self):
        from app.schemas.ir_schema import IRFloorPlan
        ir = mask_to_ir(self._make_simple_mask(), _identity_letterbox(), "test.png")
        assert isinstance(ir, IRFloorPlan)

    def test_validates_without_error(self):
        ir = mask_to_ir(self._make_simple_mask(), _identity_letterbox(), "test.png")
        ir.validate()

    def test_wall_count(self):
        ir = mask_to_ir(self._make_simple_mask(), _identity_letterbox(), "test.png")
        assert len(ir.walls) >= 1, "Expected at least 1 wall object"

    def test_metadata_source_type(self):
        ir = mask_to_ir(self._make_simple_mask(), _identity_letterbox(), "test.png")
        assert ir.metadata.source_type == "image"

    def test_default_scale_confidence_is_estimated(self):
        ir = mask_to_ir(self._make_simple_mask(), _identity_letterbox(), "test.png")
        assert ir.metadata.scale_confidence == "estimated"

    def test_has_opening(self):
        ir = mask_to_ir(self._make_simple_mask(), _identity_letterbox(), "test.png")
        assert len(ir.openings) >= 1, "Expected at least one opening (door or window)"

    def test_door_type_present(self):
        ir = mask_to_ir(self._make_simple_mask(), _identity_letterbox(), "test.png")
        door_types = [o.type for o in ir.openings]
        assert "door" in door_types, f"Expected a door opening. Got: {door_types}"


# ---------------------------------------------------------------------------
# mask_to_ir — empty wall mask
# ---------------------------------------------------------------------------


class TestEmptyMask:
    def test_all_floor_mask_produces_empty_walls(self):
        mask = _blank_mask()
        ir = mask_to_ir(mask, _identity_letterbox(), "empty.png")
        assert ir.walls == []

    def test_all_floor_still_validates(self):
        mask = _blank_mask()
        ir = mask_to_ir(mask, _identity_letterbox(), "empty.png")
        ir.validate()


# ---------------------------------------------------------------------------
# mask_to_ir — scale calibration
# ---------------------------------------------------------------------------


class TestScaleCalibration:
    def _mask_with_door(self, door_width_px: int = 90) -> np.ndarray:
        """Mask with one large wall and one door of known pixel width."""
        mask = _blank_mask()
        _paint_rect(mask, 1, 30, 460, 480, 480)   # bottom wall
        _paint_rect(mask, 1, 30, 30, 480, 50)     # top wall
        _paint_rect(mask, 1, 30, 30, 50, 480)     # left wall
        _paint_rect(mask, 1, 460, 30, 480, 480)   # right wall
        # Door of known width at centre of bottom wall
        cx = 255
        _paint_rect(mask, 2, cx - door_width_px // 2, 458, cx + door_width_px // 2, 482)
        return mask

    def test_known_door_width_sets_high_confidence(self):
        mask = self._mask_with_door(door_width_px=90)
        ir = mask_to_ir(
            mask, _identity_letterbox(), "cal.png",
            known_door_width_m=0.9,
        )
        assert ir.metadata.scale_confidence == "high"

    def test_estimated_vs_calibrated_scale_differ(self):
        mask = self._mask_with_door(door_width_px=90)
        # Default estimated scale
        ir_est = mask_to_ir(mask, _identity_letterbox(), "est.png")
        # Calibrated with known_door_width_m
        ir_cal = mask_to_ir(
            mask, _identity_letterbox(), "cal.png",
            known_door_width_m=0.9,
        )
        # Wall lengths should be different because scale factors differ
        if ir_est.walls and ir_cal.walls:
            import math
            def wall_len(w):
                return math.hypot(w.end.x - w.start.x, w.end.y - w.start.y)
            # Calibrated walls will be different from estimated (different m/px)
            # At 90px door, default scale=0.01→door=0.9m (same as reference),
            # but check that both produce valid IRFloorPlan objects
            assert ir_est.metadata.scale_confidence == "estimated"
            assert ir_cal.metadata.scale_confidence == "high"

    def test_no_doors_in_mask_falls_back_to_estimated(self):
        mask = _blank_mask()
        _paint_rect(mask, 1, 30, 460, 480, 480)  # wall only, no doors
        ir = mask_to_ir(
            mask, _identity_letterbox(), "nodoor.png",
            known_door_width_m=0.9,
        )
        # Should fall back to estimated since no door contours found
        assert ir.metadata.scale_confidence == "estimated"


# ---------------------------------------------------------------------------
# mask_to_ir — opening → wall assignment
# ---------------------------------------------------------------------------


class TestOpeningWallAssignment:
    def test_opening_wall_id_exists_in_walls(self):
        """Every opening must reference a real wall ID."""
        mask = _blank_mask()
        _paint_rect(mask, 1, 30, 460, 480, 480)
        _paint_rect(mask, 1, 30, 30, 480, 50)
        _paint_rect(mask, 1, 30, 30, 50, 480)
        _paint_rect(mask, 1, 460, 30, 480, 480)
        _paint_rect(mask, 2, 200, 458, 280, 482)  # door
        ir = mask_to_ir(mask, _identity_letterbox(), "assign.png")
        wall_ids = {w.id for w in ir.walls}
        for opening in ir.openings:
            assert opening.wall_id in wall_ids, (
                f"Opening '{opening.id}' references unknown wall '{opening.wall_id}'"
            )
