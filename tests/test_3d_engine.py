import pytest
from pathlib import Path
import trimesh

from app.schemas.ir_schema import IRFloorPlan, Metadata, Point2D, Wall, Opening
from app.engine.builder import generate_3d_model

def test_generate_3d_single_wall_with_door(tmp_path):
    ir = IRFloorPlan(
        metadata=Metadata(),
        walls=[
            Wall(id="w1", start=Point2D(0, 0), end=Point2D(5, 0), thickness=0.2, height=3.0)
        ],
        openings=[
            Opening(id="d1", type="door", wall_id="w1", position_on_wall=2.5, width=1.0, height=2.0)
        ],
        floor_polygon=[Point2D(0, -1), Point2D(5, -1), Point2D(5, 1), Point2D(0, 1)]
    )
    out_file = tmp_path / "single.glb"
    res = generate_3d_model(ir, out_file)
    
    assert Path(res).exists()
    
    scene = trimesh.load(res)
    assert isinstance(scene, trimesh.Scene)
    # 1 wall + 1 floor = 2 geometries expected
    assert len(scene.geometry) == 2
    
    # Check that boolean subtraction happened by checking faces
    # A simple box has 12 faces. A box with a door cutout will have more.
    total_faces = sum(len(g.faces) for g in scene.geometry.values())
    assert total_faces > 24  # Much more than two simple boxes

def test_generate_3d_room(tmp_path):
    # A 4x4 closed room
    ir = IRFloorPlan(
        metadata=Metadata(),
        walls=[
            Wall(id="w1", start=Point2D(0, 0), end=Point2D(4, 0)),
            Wall(id="w2", start=Point2D(4, 0), end=Point2D(4, 4)),
            Wall(id="w3", start=Point2D(4, 4), end=Point2D(0, 4)),
            Wall(id="w4", start=Point2D(0, 4), end=Point2D(0, 0)),
        ],
        openings=[
            Opening(id="win1", type="window", wall_id="w2", position_on_wall=2.0, width=1.0, height=1.0)
        ],
        floor_polygon=[Point2D(0, 0), Point2D(4, 0), Point2D(4, 4), Point2D(0, 4)]
    )
    out_file = tmp_path / "room.glb"
    res = generate_3d_model(ir, out_file)
    assert Path(res).exists()
    
    scene = trimesh.load(res)
    # 4 walls unioned into 1 mesh + 1 floor = 2 meshes
    assert len(scene.geometry) == 2

def test_generate_3d_edge_cases(tmp_path):
    # Test zero length wall, missing floor polygon, and oversized door
    ir = IRFloorPlan(
        metadata=Metadata(),
        walls=[
            Wall(id="w1", start=Point2D(0, 0), end=Point2D(0, 0)), # Degenerate
            Wall(id="w2", start=Point2D(1, 1), end=Point2D(2, 1))  # Valid 1m wall
        ],
        openings=[
            # Oversized door (2m wide on 1m wall)
            Opening(id="d1", type="door", wall_id="w2", position_on_wall=0.5, width=2.0, height=2.0)
        ],
        floor_polygon=[] # Missing floor
    )
    out_file = tmp_path / "edge.glb"
    res = generate_3d_model(ir, out_file)
    assert Path(res).exists()
    scene = trimesh.load(res)
    # Degenerate wall is skipped. Floor is skipped. So only w2 is generated.
    assert len(scene.geometry) == 1

def test_validation_failure():
    ir = IRFloorPlan(
        metadata=Metadata(unit="feet"), # Invalid
        walls=[]
    )
    with pytest.raises(ValueError):
        generate_3d_model(ir, "test.glb")
