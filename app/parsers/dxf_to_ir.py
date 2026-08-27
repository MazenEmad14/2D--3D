import math
from pathlib import Path
import ezdxf
from ezdxf.document import Drawing
from ezdxf.entities import DXFEntity
from ezdxf.math import Vec3

from app.schemas.ir_schema import IRFloorPlan, Metadata, Wall, Point2D
from app.parsers.exceptions import ParserError, NoWallLayersFoundError
from app.parsers.layer_config import (
    WALL_LAYERS,
    INSUNITS_TO_METRES,
    DEFAULT_WALL_THICKNESS_M,
    DEFAULT_WALL_HEIGHT_M
)

# Known annotation suffixes to reject when prefix-matching wall layers
ANNOTATION_SUFFIXES = ["DIM", "TXT", "PATT", "ANNO", "HATCH"]

def _is_wall_layer(layer_name: str, custom_layers: list[str] | None) -> bool:
    """
    Check if a layer name matches the wall layer heuristics.
    If custom_layers is provided, it uses simple case-insensitive substring matching.
    Otherwise, it uses WALL_LAYERS with exact or safe-prefix matching.
    """
    lname = layer_name.upper()
    
    if custom_layers:
        return any(c.upper() in lname for c in custom_layers)
        
    for pattern in WALL_LAYERS:
        pat = pattern.upper()
        if lname == pat:
            return True
        if lname.startswith(pat):
            # Check if the suffix contains any annotation terms
            suffix = lname[len(pat):]
            if not any(ann in suffix for ann in ANNOTATION_SUFFIXES):
                return True
                
    return False

def _is_axis_aligned(p1: Vec3, p2: Vec3, tol_deg: float = 1.0) -> bool:
    """Check if a line segment is orthogonal within a tolerance in degrees."""
    dx = abs(p2.x - p1.x)
    dy = abs(p2.y - p1.y)
    
    if dx < 1e-9: return True # perfectly vertical
    if dy < 1e-9: return True # perfectly horizontal
    
    angle_rad = math.atan2(dy, dx)
    angle_deg = math.degrees(angle_rad)
    
    # Check if close to 0/180 (horizontal) or 90/270 (vertical)
    # Map to 0-90 range
    angle_deg = angle_deg % 180
    if angle_deg > 90:
        angle_deg = 180 - angle_deg
        
    return angle_deg <= tol_deg or (90.0 - angle_deg) <= tol_deg

def parse_dxf(file_path: Path | str, custom_wall_layers: list[str] | None = None) -> IRFloorPlan:
    """
    Parse a DXF file and extract walls from its Modelspace geometry.
    """
    try:
        doc: Drawing = ezdxf.readfile(str(file_path))
    except Exception as e:
        raise ParserError(f"Failed to read DXF file: {e}") from e

    # 1. Determine Units / Scale
    insunits = doc.header.get("$INSUNITS", 0)
    if insunits == 0 or insunits not in INSUNITS_TO_METRES:
        # Fallback to millimeters for architectural DXFs lacking units
        scale_m = 0.001
        scale_confidence = "estimated"
    else:
        scale_m = INSUNITS_TO_METRES[insunits]
        scale_confidence = "high"

    msp = doc.modelspace()
    
    walls: list[Wall] = []
    skipped_non_orthogonal = 0
    skipped_curved = 0
    
    # We will use virtual_entities() to yield resolved entities from block inserts
    def _iter_entities():
        for entity in msp:
            if entity.dxftype() == 'INSERT':
                try:
                    yield from entity.virtual_entities()
                except Exception:
                    # Ignore blocks that fail to explode/virtualize
                    pass
            else:
                yield entity
                
    min_x, min_y, max_x, max_y = float('inf'), float('inf'), float('-inf'), float('-inf')
    
    found_any_wall_layer_entity = False

    for entity in _iter_entities():
        layer = entity.dxf.layer
        if not _is_wall_layer(layer, custom_wall_layers):
            continue
            
        found_any_wall_layer_entity = True
        
        etype = entity.dxftype()
        if etype in {"ARC", "CIRCLE", "SPLINE", "ELLIPSE"}:
            skipped_curved += 1
            continue
            
        if etype == "LINE":
            p1, p2 = entity.dxf.start, entity.dxf.end
            
            if _is_axis_aligned(p1, p2):
                start = Point2D(p1.x * scale_m, p1.y * scale_m)
                end = Point2D(p2.x * scale_m, p2.y * scale_m)
                wall_id = f"wall_{len(walls)+1}"
                walls.append(Wall(
                    id=wall_id,
                    start=start,
                    end=end,
                    thickness=DEFAULT_WALL_THICKNESS_M,
                    height=DEFAULT_WALL_HEIGHT_M,
                    thickness_confidence="estimated"
                ))
                min_x = min(min_x, p1.x, p2.x)
                min_y = min(min_y, p1.y, p2.y)
                max_x = max(max_x, p1.x, p2.x)
                max_y = max(max_y, p1.y, p2.y)
            else:
                skipped_non_orthogonal += 1
                
        elif etype in {"LWPOLYLINE", "POLYLINE"}:
            # Extract line segments
            if etype == "LWPOLYLINE":
                pts = list(entity.get_points(format="xyb")) # x, y, bulge
                const_width = entity.dxf.const_width
            else:
                pts = [(v.dxf.location.x, v.dxf.location.y, v.dxf.bulge) for v in entity.vertices]
                const_width = 0.0

            if entity.is_closed:
                pts.append(pts[0])
                
            for i in range(len(pts) - 1):
                p1_x, p1_y, bulge = pts[i]
                p2_x, p2_y, _ = pts[i+1]
                
                # If there's a bulge, it's a curved segment (arc)
                if abs(bulge) > 1e-5:
                    skipped_curved += 1
                    continue
                    
                p1 = Vec3(p1_x, p1_y, 0)
                p2 = Vec3(p2_x, p2_y, 0)
                
                # Ignore zero-length segments
                if p1.isclose(p2):
                    continue
                    
                if _is_axis_aligned(p1, p2):
                    start = Point2D(p1.x * scale_m, p1.y * scale_m)
                    end = Point2D(p2.x * scale_m, p2.y * scale_m)
                    wall_id = f"wall_{len(walls)+1}"
                    
                    if const_width > 0:
                        thickness = const_width * scale_m
                        confidence = "measured"
                    else:
                        thickness = DEFAULT_WALL_THICKNESS_M
                        confidence = "estimated"
                        
                    walls.append(Wall(
                        id=wall_id,
                        start=start,
                        end=end,
                        thickness=thickness,
                        height=DEFAULT_WALL_HEIGHT_M,
                        thickness_confidence=confidence
                    ))
                    min_x = min(min_x, p1.x, p2.x)
                    min_y = min(min_y, p1.y, p2.y)
                    max_x = max(max_x, p1.x, p2.x)
                    max_y = max(max_y, p1.y, p2.y)
                else:
                    skipped_non_orthogonal += 1

    if not found_any_wall_layer_entity:
        raise NoWallLayersFoundError("No geometry found on any recognized wall layer.")

    if not walls:
        min_x, min_y, max_x, max_y = 0, 0, 10, 10

    floor_poly = [
        Point2D(min_x * scale_m, min_y * scale_m),
        Point2D(max_x * scale_m, min_y * scale_m),
        Point2D(max_x * scale_m, max_y * scale_m),
        Point2D(min_x * scale_m, max_y * scale_m)
    ]

    metadata = Metadata(
        unit="meter",
        source_type="dxf",
        scale_confidence=scale_confidence,
        page_count=1,
        page_processed=1,
        walls_skipped_non_orthogonal=skipped_non_orthogonal,
        walls_skipped_curved=skipped_curved
    )

    return IRFloorPlan(metadata=metadata, walls=walls, openings=[], floor_polygon=floor_poly)
