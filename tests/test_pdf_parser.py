"""
tests/test_pdf_parser.py

Tests for app.parsers.pdf_to_ir.parse_pdf().

Verifies vector vs scanned routing, extraction logic, and angled wall skipping.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import cv2
import numpy as np
import pymupdf as fitz
import pytest

from app.parsers.exceptions import CorruptFileError, ParserError
from app.parsers.pdf_to_ir import parse_pdf
from app.schemas.ir_schema import IRFloorPlan


@pytest.fixture
def synthetic_vector_pdf():
    """Create a vector PDF with a single rectangle (1 path) and one diagonal line."""
    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    
    doc = fitz.open()
    page = doc.new_page()
    rect = fitz.Rect(100, 100, 110, 300)
    # The rectangle has a fill and a stroke, meaning it's a valid vector drawing
    page.draw_rect(rect, color=(0,0,0), fill=(0.5, 0.5, 0.5), width=2)
    # Add a diagonal line to test the non-orthogonal wall skipped logic
    page.draw_line(fitz.Point(300, 100), fitz.Point(400, 200), color=(1,0,0), width=2)
    doc.save(path)
    doc.close()
    
    yield path
    os.remove(path)


@pytest.fixture
def synthetic_scanned_pdf():
    """Create a scanned PDF (only an image, no vector strokes)."""
    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    
    # Create a dummy image representing a floor plan mask (CLASS_WALL = 1 usually)
    # For testing routing, just inserting any valid image is enough to trigger Scanned logic
    # because it will have 0 vector paths.
    img = np.zeros((200, 200, 3), dtype=np.uint8)
    cv2.rectangle(img, (50, 50), (150, 150), (255, 255, 255), -1) 
    
    img_fd, img_path = tempfile.mkstemp(suffix=".png")
    os.close(img_fd)
    cv2.imwrite(img_path, img)
    
    doc = fitz.open()
    page = doc.new_page()
    page.insert_image(page.rect, filename=img_path)
    doc.save(path)
    doc.close()
    
    yield path
    os.remove(path)
    os.remove(img_path)


class TestPDFParserRouting:
    def test_vector_pdf_routing_and_extraction(self, synthetic_vector_pdf):
        # Test vector routing
        ir = parse_pdf(synthetic_vector_pdf)
        assert isinstance(ir, IRFloorPlan)
        assert ir.metadata.source_type == "pdf_vector"
        assert ir.metadata.page_count == 1
        assert ir.metadata.page_processed == 1
        assert ir.metadata.scale_confidence == "estimated"
        
        # The diagonal line should be skipped, recording 1 skipped non-orthogonal wall
        assert ir.metadata.walls_skipped_non_orthogonal == 1
        
        # The rectangle should produce 1 thick wall
        assert len(ir.walls) == 1
        
        wall = ir.walls[0]
        # Rect is (100, 100, 110, 300) -> width=10, height=200 -> vertical wall
        # Centerline X = 100 + 10/2 = 105
        # Centerline Y start = 100 + 10/2 = 105
        # Centerline Y end = 300 - 10/2 = 295
        from app.parsers.pdf_to_ir import FALLBACK_SCALE_M_PER_PT
        assert abs(wall.start.x - 105 * FALLBACK_SCALE_M_PER_PT) < 1e-5
        assert abs(wall.start.y - 105 * FALLBACK_SCALE_M_PER_PT) < 1e-5
        assert abs(wall.end.x - 105 * FALLBACK_SCALE_M_PER_PT) < 1e-5
        assert abs(wall.end.y - 295 * FALLBACK_SCALE_M_PER_PT) < 1e-5
        assert abs(wall.thickness - 10 * FALLBACK_SCALE_M_PER_PT) < 1e-5

    def test_scanned_pdf_routing(self, synthetic_scanned_pdf, monkeypatch):
        # To avoid running the entire heavy CV segmentation model in unit tests,
        # we mock `parse_image` to just return a dummy IRFloorPlan.
        from app.parsers import pdf_to_ir
        from app.schemas.ir_schema import Metadata

        def mock_parse_image(image_path, **kwargs):
            return IRFloorPlan(
                metadata=Metadata(unit="meter", source_type="image", scale_confidence="estimated"),
                walls=[],
                openings=[],
                floor_polygon=[]
            )
            
        monkeypatch.setattr(pdf_to_ir, "parse_image", mock_parse_image)
        
        # Test scanned routing
        ir = parse_pdf(synthetic_scanned_pdf)
        assert isinstance(ir, IRFloorPlan)
        assert ir.metadata.source_type == "pdf_scanned"
        assert ir.metadata.page_count == 1
        assert ir.metadata.page_processed == 1


class TestPDFErrorHandling:
    def test_corrupted_pdf(self):
        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.write(fd, b"This is not a pdf")
        os.close(fd)
        
        with pytest.raises(CorruptFileError):
            parse_pdf(path)
            
        os.remove(path)
