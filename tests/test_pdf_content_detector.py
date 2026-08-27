"""
tests/test_pdf_content_detector.py

Tests for app.parsers.pdf_content_detector.detect_pdf_content_type().

The function is tested independently of parse_pdf() since it is retained
as a standalone utility for metadata and logging purposes.

Note on thresholds
------------------
The synthetic fixtures are minimal, so min_drawings and min_image_coverage
are overridden with more lenient values in some tests.  Production code
uses the documented defaults (10 drawings / 50% coverage).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.parsers.pdf_content_detector import (
    MIN_IMAGE_COVERAGE_FRACTION,
    MIN_VECTOR_DRAWING_COMMANDS,
    detect_pdf_content_type,
)

FIXTURES = Path(__file__).parent / "fixtures"


class TestVectorClassification:
    """reportlab-generated PDF with 12 line segments → 'vector'."""

    def test_default_thresholds(self):
        """12 lines should exceed the default MIN_VECTOR_DRAWING_COMMANDS=10."""
        result = detect_pdf_content_type(FIXTURES / "fixture_simple.pdf")
        assert result == "vector", f"Expected 'vector', got '{result}'"

    def test_lenient_threshold(self):
        """Even at threshold=5 the fixture should still be 'vector'."""
        result = detect_pdf_content_type(
            FIXTURES / "fixture_simple.pdf",
            min_drawings=5,
        )
        assert result == "vector"

    def test_returns_literal(self):
        result = detect_pdf_content_type(FIXTURES / "fixture_simple.pdf")
        assert result in ("vector", "scanned")


class TestScannedClassification:
    """Full-page embedded raster image → 'scanned'."""

    def test_default_thresholds(self):
        result = detect_pdf_content_type(
            FIXTURES / "fixture_scanned.pdf",
        )
        assert result == "scanned", f"Expected 'scanned', got '{result}'"

    def test_lenient_coverage_threshold(self):
        result = detect_pdf_content_type(
            FIXTURES / "fixture_scanned.pdf",
            min_image_coverage=0.30,  # 30% threshold
        )
        assert result == "scanned"

    def test_returns_literal(self):
        result = detect_pdf_content_type(FIXTURES / "fixture_scanned.pdf")
        assert result in ("vector", "scanned")


class TestThresholdConstants:
    """Verify documented threshold values are sensible."""

    def test_min_drawing_commands_positive(self):
        assert MIN_VECTOR_DRAWING_COMMANDS > 0

    def test_image_coverage_fraction_range(self):
        assert 0.0 < MIN_IMAGE_COVERAGE_FRACTION <= 1.0
