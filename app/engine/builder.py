import math
import logging
from pathlib import Path
import numpy as np
import trimesh
from shapely.geometry import Polygon
import manifold3d

from app.schemas.ir_schema import IRFloorPlan

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Architectural height constants (metres, one-storey residential standard)
#
# Reference: floorplan-to-3d viewer/index.html lines 144-147
#   WALL_HEIGHT = 0.55, DOOR_HEIGHT = 0.40, WINDOW_SILL = 0.16, WINDOW_TOP = 0.45
#   (all in normalised world units where longest plan axis = 8.0)
#
# Real-world equivalents at 3.0 m storey height (proportional conversion):
#   Door   : 0.40/0.55 × 3.0 m ≈ 2.18 m  → we use 2.1 m (standard interior door)
#   Win sill: 0.16/0.55 × 3.0 m ≈ 0.87 m → we use 0.9 m (standard sill height)
#   Win top : 0.45/0.55 × 3.0 m ≈ 2.45 m → we use 2.4 m (lintel at ~80% of wall)
#   Window height (slab) = 2.4 - 0.9 = 1.5 m (≈ reference's 52.7% of wall height)
# ---------------------------------------------------------------------------

#: Bottom of window glass (metres above finished floor)
WINDOW_SILL_HEIGHT_M: float = 0.9

#: Top of window glass (metres above finished floor).
#: Lintel clearance above window = wall_height - WINDOW_TOP_HEIGHT_M ≈ 0.6 m.
WINDOW_TOP_HEIGHT_M: float = 2.4

#: Default clear opening height for doors (metres).
#: Matches reference repo's 0.40/0.55 × 3.0 m ≈ 2.18 m rounded to standard.
DOOR_HEIGHT_DEFAULT_M: float = 2.1

#: Default clear height of the window glass slab = WINDOW_TOP - WINDOW_SILL.
WINDOW_HEIGHT_DEFAULT_M: float = WINDOW_TOP_HEIGHT_M - WINDOW_SILL_HEIGHT_M  # 1.5 m

def generate_3d_model(ir: IRFloorPlan, output_path: str | Path) -> str:
    """
    Consumes a validated JSON IR and produces a .glb 3D model.
    """
    # 1. Validate the IR completely before processing
    ir.validate()
    
    meshes = []
    
    # 2. Build Floor Slab
    if ir.floor_polygon and len(ir.floor_polygon) >= 3:
        try:
            poly = Polygon([(p.x, p.y) for p in ir.floor_polygon])
            if poly.is_valid and not poly.is_empty:
                # extrude_polygon extrudes upwards from z=0 to z=height
                floor_mesh = trimesh.creation.extrude_polygon(poly, height=0.1)
                floor_mesh.apply_translation([0, 0, -0.1])
                # Set color: Dark Grey
                floor_mesh.visual.face_colors = [120, 120, 120, 255]
                # Keep floor separate
                floor_mesh.metadata['name'] = "FloorSlab"
                meshes.append(floor_mesh)
        except Exception as e:
            logger.warning(f"Failed to build floor slab: {e}")

    # 3. Build Walls and Cutouts
    # Group openings by wall_id
    openings_by_wall = {}
    for op in ir.openings:
        openings_by_wall.setdefault(op.wall_id, []).append(op)
        
    wall_meshes = []
    
    for wall in ir.walls:
        dx = wall.end.x - wall.start.x
        dy = wall.end.y - wall.start.y
        length = math.hypot(dx, dy)
        
        if length < 1e-5:
            logger.warning(f"Skipping degenerate wall {wall.id} (length={length:.4f}m)")
            continue
            
        # Determine confidence-based extension
        is_estimated = (wall.thickness_confidence == "estimated" or ir.metadata.scale_confidence == "estimated")
        extension = wall.thickness if is_estimated else (wall.thickness / 2.0)
        
        # Extended length
        ext_length = length + 2 * extension
            
        # Create base wall box centered at origin (X-axis aligned)
        wall_box = trimesh.creation.box(extents=[ext_length, wall.thickness, wall.height])
        # Move up so bottom is at Z=0
        wall_box.apply_translation([0, 0, wall.height / 2.0])
        
        # Build cutouts for this wall (in the local X-aligned space of the wall)
        cutout_meshes = []
        for op in openings_by_wall.get(wall.id, []):
            cutout_length = min(op.width, length)
            cutout_thickness = wall.thickness + 0.2  # Slightly thicker to avoid Z-fighting
            
            if op.type == "door":
                # Doors run floor-to-lintel: Z = 0 → door_height.
                raw_h = op.height
                if raw_h < 1.5 or raw_h > wall.height:
                    logger.debug(
                        "Door %s height %.2f m out of range; using default %.2f m",
                        op.id, raw_h, DOOR_HEIGHT_DEFAULT_M,
                    )
                    raw_h = DOOR_HEIGHT_DEFAULT_M
                cutout_height = min(raw_h, wall.height)
                z_bottom = 0.0
                z_center = z_bottom + cutout_height / 2.0
            else:
                # Windows are extruded as a glass *slab* between sill and lintel
                raw_h = op.height
                max_window_h = wall.height - WINDOW_SILL_HEIGHT_M
                if raw_h < 0.3 or raw_h > max_window_h:
                    logger.debug(
                        "Window %s height %.2f m out of range; using default %.2f m",
                        op.id, raw_h, WINDOW_HEIGHT_DEFAULT_M,
                    )
                    raw_h = min(WINDOW_HEIGHT_DEFAULT_M, max_window_h)
                cutout_height = raw_h
                z_center = WINDOW_SILL_HEIGHT_M + cutout_height / 2.0
                
            cut_box = trimesh.creation.box(extents=[cutout_length, cutout_thickness, cutout_height])
            
            # Position relative to wall center (which is unchanged by symmetric extension)
            rel_x = op.position_on_wall - (length / 2.0)
            cut_box.apply_translation([rel_x, 0, z_center])
            cutout_meshes.append(cut_box)
            
        # Apply booleans in local space if needed
        if cutout_meshes:
            try:
                # Difference using manifold backend
                wall_mesh = trimesh.boolean.difference([wall_box, *cutout_meshes], engine='manifold')
                if wall_mesh.is_empty:
                    logger.warning(f"Wall {wall.id} completely subtracted by openings.")
                    continue
            except Exception as e:
                logger.error(f"Boolean subtraction failed for {wall.id}: {e}")
                wall_mesh = wall_box
        else:
            wall_mesh = wall_box
            
        # Set color before union so the face colors are retained:
        # Light Beige/Grey for measured, Orange/Yellow for estimated
        if hasattr(wall_mesh, 'visual') and hasattr(wall_mesh.visual, 'face_colors'):
            if is_estimated:
                wall_mesh.visual.face_colors = [255, 200, 100, 255] # Warning color
            else:
                wall_mesh.visual.face_colors = [220, 220, 210, 255] # Standard color
                
        # Now transform the finished wall mesh to its final world position
        angle = math.atan2(dy, dx)
        rot_mat = trimesh.transformations.rotation_matrix(angle, [0, 0, 1])
        wall_mesh.apply_transform(rot_mat)
        
        center_x = (wall.start.x + wall.end.x) / 2.0
        center_y = (wall.start.y + wall.end.y) / 2.0
        wall_mesh.apply_translation([center_x, center_y, 0])
        
        wall_meshes.append(wall_mesh)
        
    union_status = "success"
    if len(wall_meshes) > 1:
        try:
            # Pairwise/incremental union to maintain stability
            logger.info("Applying boolean union to resolve corners...")
            unioned_wall = wall_meshes[0]
            for i in range(1, len(wall_meshes)):
                unioned_wall = trimesh.boolean.union([unioned_wall, wall_meshes[i]], engine='manifold')
            
            if not unioned_wall.is_empty:
                if not unioned_wall.is_watertight:
                    logger.warning("Unioned wall mesh is not watertight!")
                meshes.append(unioned_wall)
            else:
                raise ValueError("Union resulted in an empty mesh")
        except Exception as e:
            logger.error(f"Boolean union failed for walls: {e}")
            union_status = "failed_fallback_overlapping_meshes"
            meshes.extend(wall_meshes)
    elif wall_meshes:
        meshes.extend(wall_meshes)
        
    if not meshes:
        raise ValueError("No valid 3D geometry was generated from the provided IR.")
        
    # 4. Export to .glb
    scene = trimesh.Scene(meshes)
    
    # We must embed the custom property into the GLTF extras.
    # trimesh passes 'extras' in the export arguments or from scene.metadata.
    # To be totally safe and compliant, we inject it into scene metadata.
    # (By default trimesh GLTF exporter takes scene.metadata and merges into root extras).
    scene.metadata["geometry_union_status"] = union_status
    
    out_path = Path(output_path)
    
    # trimesh export
    scene.export(str(out_path), file_type='glb')
    
    # A robust fallback to inject extras if trimesh missed it:
    # (Trimesh *should* handle this, but pygltflib is safer for raw json manipulation if needed.
    # For now, relying on trimesh's scene.metadata serialization)
    
    return str(out_path)
