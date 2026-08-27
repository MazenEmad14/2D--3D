import pytest
from run_crosscheck import create_synthetic_vector, create_synthetic_dxf
from run_crosscheck import compare_irs, TOLERANCE_SCANNED_M, TOLERANCE_VECTOR_M
from app.parsers.pdf_to_ir import parse_pdf, _dpi_to_scale_m_per_px, DEFAULT_RENDER_DPI
from app.parsers.image_to_ir import parse_image
from app.parsers.dxf_to_ir import parse_dxf
import fitz
import os

def test_crosscheck_dxf_vs_vector():
    pdf_path = create_synthetic_vector(10, 10, 10, 10)
    dxf_path = create_synthetic_dxf(10, 10, 10, 10)
    
    ir_vector = parse_pdf(pdf_path)
    ir_dxf = parse_dxf(dxf_path)
    
    ok, max_diff = compare_irs("DXF", ir_dxf, "PDF-Vector", ir_vector, TOLERANCE_VECTOR_M)
    
    os.remove(pdf_path)
    os.remove(dxf_path)
    
    assert ok, f"DXF vs Vector crosscheck failed. Max diff: {max_diff}"

def test_crosscheck_scanned_vs_vector_symmetric():
    pdf_path = create_synthetic_vector(10, 10, 10, 10)
    ir_vector = parse_pdf(pdf_path)
    
    dpi = DEFAULT_RENDER_DPI
    physical_scale = _dpi_to_scale_m_per_px(dpi)
    doc = fitz.open(pdf_path)
    pix = doc[0].get_pixmap(dpi=dpi)
    png_path = "synthetic_vector_rendered.png"
    pix.save(png_path)
    doc.close()
    
    ir_scanned = parse_image(png_path, scale_m_per_px=physical_scale)
    
    ok, max_diff = compare_irs("PDF-Scanned", ir_scanned, "PDF-Vector", ir_vector, TOLERANCE_SCANNED_M)
    
    os.remove(pdf_path)
    os.remove(png_path)
    
    assert ok, f"Scanned vs Vector symmetric crosscheck failed. Max diff: {max_diff}"

@pytest.mark.xfail(
    reason="Known Issue #4 in known_issues.md: Global Percentile Erosion fails on highly asymmetric multi-thickness components",
    strict=True
)
def test_crosscheck_scanned_vs_vector_asymmetric_reproduction():
    # 20, 30, 10, 15 thickness reproduction case
    pdf_path = create_synthetic_vector(20, 30, 10, 15)
    ir_vector = parse_pdf(pdf_path)
    
    dpi = DEFAULT_RENDER_DPI
    physical_scale = _dpi_to_scale_m_per_px(dpi)
    doc = fitz.open(pdf_path)
    pix = doc[0].get_pixmap(dpi=dpi)
    png_path = "synthetic_vector_rendered.png"
    pix.save(png_path)
    doc.close()
    
    ir_scanned = parse_image(png_path, scale_m_per_px=physical_scale)
    
    ok, max_diff = compare_irs("PDF-Scanned", ir_scanned, "PDF-Vector", ir_vector, TOLERANCE_SCANNED_M)
    
    os.remove(pdf_path)
    os.remove(png_path)
    
    assert ok, f"Scanned vs Vector asymmetric crosscheck failed as expected. Max diff: {max_diff}"
