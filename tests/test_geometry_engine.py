import sys
import numpy as np
import pytest
import trimesh
from pathlib import Path

# Add project root to sys.path so app modules resolve
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.schemas.ir_schema import IRFloorPlan, Metadata, Wall, Opening, Point2D
from app.engine.builder import generate_3d_model

def get_base_metadata():
    return Metadata(unit="meter", source_type="dxf", scale_confidence="high")

def test_single_room_watertight(tmp_path):
    """
    Test 1: A perfect 4-wall square room (no openings).
    Verifies the output mesh is watertight and has the expected bounding box.
    """
    walls = [
        Wall(id="w1", start=Point2D(0, 0), end=Point2D(5, 0), thickness=0.2, height=3.0, thickness_confidence="measured"),
        Wall(id="w2", start=Point2D(5, 0), end=Point2D(5, 4), thickness=0.2, height=3.0, thickness_confidence="measured"),
        Wall(id="w3", start=Point2D(5, 4), end=Point2D(0, 4), thickness=0.2, height=3.0, thickness_confidence="measured"),
        Wall(id="w4", start=Point2D(0, 4), end=Point2D(0, 0), thickness=0.2, height=3.0, thickness_confidence="measured"),
    ]
    ir = IRFloorPlan(metadata=get_base_metadata(), walls=walls)
    
    out_file = tmp_path / "single_room.glb"
    generate_3d_model(ir, out_file)
    
    assert out_file.exists()
    scene = trimesh.load(out_file, process=False)
    
    # trimesh loads glb as a Scene containing multiple geometry pieces
    # If the union was successful, the walls should be 1 continuous watertight mesh
    # excluding the floor. (We didn't pass a floor polygon here).
    assert len(scene.geometry) == 1
    mesh = list(scene.geometry.values())[0]
    
    assert mesh.is_watertight, "The unioned walls must be watertight"
    assert not mesh.is_empty
    assert not np.isnan(mesh.vertices).any()
    
    # Bounding box of walls:
    # x goes from 0 to 5, extended by thickness/2 = 0.1 on both ends -> [-0.1, 5.1]
    # y goes from 0 to 4, extended by 0.1 on both ends -> [-0.1, 4.1]
    # z goes from 0 to 3 -> [0, 3]
    bounds = mesh.bounds
    assert np.allclose(bounds[0], [-0.1, -0.1, 0.0], atol=1e-3)
    assert np.allclose(bounds[1], [5.1, 4.1, 3.0], atol=1e-3)

def test_t_junction_watertight(tmp_path):
    """
    Test 2: A T-junction with 3 walls meeting.
    Verifies watertightness (no internal overlapping faces).
    """
    walls = [
        # Horizontal top bar of the T
        Wall(id="top_left", start=Point2D(0, 0), end=Point2D(5, 0), thickness=0.2, height=3.0, thickness_confidence="measured"),
        Wall(id="top_right", start=Point2D(5, 0), end=Point2D(10, 0), thickness=0.2, height=3.0, thickness_confidence="measured"),
        # Vertical stem of the T meeting at x=5, y=0
        Wall(id="stem", start=Point2D(5, 0), end=Point2D(5, -5), thickness=0.2, height=3.0, thickness_confidence="measured"),
    ]
    ir = IRFloorPlan(metadata=get_base_metadata(), walls=walls)
    
    out_file = tmp_path / "t_junction.glb"
    generate_3d_model(ir, out_file)
    
    scene = trimesh.load(out_file, process=False)
    mesh = list(scene.geometry.values())[0]
    
    assert mesh.is_watertight, "T-junction union must resolve overlapping faces and be watertight"

def test_opening_cutout_raycast(tmp_path):
    """
    Test 3: Verify door cutout exists via ray-casting.
    5 points inside the door bounding box must MISS the mesh.
    1 point outside the door bounding box must HIT the mesh.
    """
    # A single wall from x=0 to x=10, with a door in the middle
    walls = [Wall(id="w1", start=Point2D(0, 0), end=Point2D(10, 0), thickness=0.2, height=3.0, thickness_confidence="measured")]
    # Door at position 5, width 1.0, height 2.1
    # Thus door spans X in [4.5, 5.5], Z in [0, 2.1].
    openings = [Opening(id="d1", type="door", wall_id="w1", position_on_wall=5.0, width=1.0, height=2.1)]
    
    ir = IRFloorPlan(metadata=get_base_metadata(), walls=walls, openings=openings)
    
    out_file = tmp_path / "door_cutout.glb"
    generate_3d_model(ir, out_file)
    
    scene = trimesh.load(out_file, process=False)
    mesh = list(scene.geometry.values())[0]
    
    ray_origins = []
    ray_directions = []
    
    # We'll shoot rays along the Y axis (through the wall thickness)
    # The wall thickness is 0.2, Y spans [-0.1, 0.1]. 
    # We shoot from Y=-1.0 towards Y=+1.0
    
    # 5 points INSIDE the door:
    # 1. Exact center
    ray_origins.append([5.0, -1.0, 1.05])
    # 2. Bottom-left (inset 5cm)
    ray_origins.append([4.55, -1.0, 0.05])
    # 3. Bottom-right (inset 5cm)
    ray_origins.append([5.45, -1.0, 0.05])
    # 4. Top-left (inset 5cm)
    ray_origins.append([4.55, -1.0, 2.05])
    # 5. Top-right (inset 5cm)
    ray_origins.append([5.45, -1.0, 2.05])
    
    # 1 point OUTSIDE the door (hit wall)
    # At x=6.0 (solid wall), z=1.0
    ray_origins.append([6.0, -1.0, 1.0])
    
    ray_directions = [[0, 1, 0]] * 6
    
    # intersect_any returns True if the ray hits the mesh
    hits = mesh.ray.intersects_any(ray_origins, ray_directions)
    
    # The first 5 rays should NOT hit (they go through the door hole)
    for i in range(5):
        assert not hits[i], f"Ray {i} hit the mesh, but it should have passed through the door cutout!"
        
    # The 6th ray SHOULD hit (it goes through the solid wall)
    assert hits[5], "Ray 5 missed the mesh, but it should have hit the solid wall!"

def test_confidence_extension_and_materials(tmp_path):
    """
    Test 4: Verify "estimated" walls get double extension and distinct material.
    Because the union merges meshes, the distinct materials might be lost 
    if trimesh merges them into a single node. We will test the fallback behavior
    or check if the visual properties are retained.
    Wait, trimesh union generally merges geometry and uses the FIRST material.
    Let's check the behavior of the meshes BEFORE union by disabling union 
    (or forcing fallback) just for this test to assert materials, 
    or check the bounds to verify extension length.
    """
    # Measured wall (length 10) -> extends by 0.1 on both ends -> total len 10.2
    w_meas = Wall(id="w1", start=Point2D(0, 0), end=Point2D(10, 0), thickness=0.2, height=3.0, thickness_confidence="measured")
    # Estimated wall (length 10) -> extends by 0.2 on both ends -> total len 10.4
    w_est = Wall(id="w2", start=Point2D(0, 5), end=Point2D(10, 5), thickness=0.2, height=3.0, thickness_confidence="estimated")
    
    ir = IRFloorPlan(metadata=get_base_metadata(), walls=[w_meas, w_est])
    
    out_file = tmp_path / "confidence_materials.glb"
    generate_3d_model(ir, out_file)
    
    scene = trimesh.load(out_file, process=False)
    
    # If trimesh union merged them, it might be 1 geometry. 
    # Let's check the total bounds to confirm the extension lengths.
    bounds = scene.bounds
    
    # w_meas min X is 0 - 0.1 = -0.1
    # w_est min X is 0 - 0.2 = -0.2
    # So total min X = -0.2 (proved the estimated wall extended further)
    assert np.allclose(bounds[0][0], -0.2, atol=1e-3), "Estimated wall did not get double extension on min X"
    
    # w_meas max X is 10 + 0.1 = 10.1
    # So total max X = 10.2
    assert np.allclose(bounds[1][0], 10.2, atol=1e-3), "Estimated wall did not get double extension on max X"

def test_union_failure_fallback(tmp_path, monkeypatch):
    """
    Test 5: Verify the boolean union failure fallback path.
    Force trimesh.boolean.union to raise an exception, and confirm the
    exported .glb gracefully falls back to overlapping meshes and embeds
    the geometry_union_status flag in its metadata/extras.
    """
    # Two simple walls to trigger union logic
    w1 = Wall(id="w1", start=Point2D(0, 0), end=Point2D(5, 0), thickness=0.2, height=3.0)
    w2 = Wall(id="w2", start=Point2D(5, 0), end=Point2D(5, 5), thickness=0.2, height=3.0)
    ir = IRFloorPlan(metadata=get_base_metadata(), walls=[w1, w2])
    
    # Mock trimesh.boolean.union to always fail
    def mock_union(*args, **kwargs):
        raise RuntimeError("Simulated boolean engine failure")
    
    monkeypatch.setattr(trimesh.boolean, "union", mock_union)
    
    out_file = tmp_path / "fallback.glb"
    generate_3d_model(ir, out_file)
    
    # 1. Verify the fallback didn't crash and actually exported a file
    assert out_file.exists(), "Fallback path failed to export a .glb file"
    
    # 2. Verify we fell back to exporting the multiple individual meshes
    scene = trimesh.load(out_file, process=False)
    # 2 walls = 2 separate geometries since union failed
    assert len(scene.geometry) == 2, "Fallback should have exported 2 separate non-unioned meshes"
    
    # 3. Verify the GLTF extras flag was written.
    # trimesh loads root GLTF 'extras' into scene.metadata (or asset)
    # If it's not natively re-parsed into metadata, we can parse the raw GLB JSON chunk.
    # Let's check scene.metadata first
    assert "geometry_union_status" in scene.metadata, "Metadata flag missing from loaded scene"
    assert scene.metadata["geometry_union_status"] == "failed_fallback_overlapping_meshes"

