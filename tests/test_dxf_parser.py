import pytest
from pathlib import Path
import ezdxf

from app.parsers.dxf_to_ir import parse_dxf
from app.schemas.ir_schema import IRFloorPlan
from app.parsers.exceptions import NoWallLayersFoundError

@pytest.fixture
def synthetic_dxf(tmp_path):
    """
    Creates a synthetic DXF with:
    - 1 orthogonal line on A-WALL
    - 1 non-orthogonal line on A-WALL
    - 1 curved LWPOLYLINE on A-WALL
    - 1 orthogonal line on A-WALL-DIM (should be ignored by default)
    """
    doc = ezdxf.new("R2010")
    # Set units to meters (6)
    doc.header["$INSUNITS"] = 6
    msp = doc.modelspace()
    
    # Valid orthogonal wall (Line)
    msp.add_line((0, 0), (10, 0), dxfattribs={"layer": "A-WALL"})
    
    # Non-orthogonal wall
    msp.add_line((0, 0), (10, 10), dxfattribs={"layer": "A-WALL"})
    
    # Curved polyline (bulge = 1)
    msp.add_lwpolyline([(0, 0, 0, 0, 1), (5, 5, 0, 0, 0)], dxfattribs={"layer": "A-WALL"})
    
    # Annotation layer (should be ignored by default)
    msp.add_line((0, 1), (10, 1), dxfattribs={"layer": "A-WALL-DIM"})
    
    dxf_path = tmp_path / "test.dxf"
    doc.saveas(dxf_path)
    return dxf_path

@pytest.fixture
def synthetic_dxf_unitless(tmp_path):
    doc = ezdxf.new("R2010")
    # Unitless (0)
    doc.header["$INSUNITS"] = 0
    msp = doc.modelspace()
    msp.add_line((0, 0), (1000, 0), dxfattribs={"layer": "A-WALL"})
    
    dxf_path = tmp_path / "test_unitless.dxf"
    doc.saveas(dxf_path)
    return dxf_path

@pytest.fixture
def synthetic_dxf_no_wall_layers(tmp_path):
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 0), dxfattribs={"layer": "0"})
    
    dxf_path = tmp_path / "test_empty.dxf"
    doc.saveas(dxf_path)
    return dxf_path

def test_dxf_extraction(synthetic_dxf):
    ir = parse_dxf(synthetic_dxf)
    
    assert isinstance(ir, IRFloorPlan)
    assert ir.metadata.source_type == "dxf"
    assert ir.metadata.scale_confidence == "high"
    
    # Only 1 wall should be extracted (the orthogonal line on A-WALL)
    assert len(ir.walls) == 1
    
    wall = ir.walls[0]
    assert wall.start.x == 0
    assert wall.start.y == 0
    assert wall.end.x == 10
    assert wall.end.y == 0
    
    assert ir.metadata.walls_skipped_non_orthogonal == 1
    assert ir.metadata.walls_skipped_curved == 1

def test_dxf_unitless_fallback(synthetic_dxf_unitless):
    ir = parse_dxf(synthetic_dxf_unitless)
    
    assert ir.metadata.scale_confidence == "estimated"
    assert len(ir.walls) == 1
    
    # 1000 units in mm = 1.0 meters
    wall = ir.walls[0]
    assert wall.end.x == 1.0

def test_dxf_no_wall_layers(synthetic_dxf_no_wall_layers):
    with pytest.raises(NoWallLayersFoundError):
        parse_dxf(synthetic_dxf_no_wall_layers)

def test_dxf_custom_layers(synthetic_dxf_no_wall_layers):
    # Should work if we explicitly allow layer "0"
    ir = parse_dxf(synthetic_dxf_no_wall_layers, custom_wall_layers=["0"])
    assert len(ir.walls) == 1
