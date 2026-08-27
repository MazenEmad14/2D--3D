"""
app/schemas/ir_schema.py

Dataclasses for the Intermediate Representation (IR) produced by every
input-format parser (DXF, PDF, image) and consumed by the 3D engine.

These classes serve two purposes:
  1. Self-documenting schema — a single source of truth for the IR shape.
  2. Runtime validation — `IRFloorPlan.validate()` raises on bad data so
     downstream components receive clean, consistent input.

Schema version: 1.0.0
See docs/json_ir_schema.md for the full narrative documentation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Literal


# ---------------------------------------------------------------------------
# Primitive / nested structures
# ---------------------------------------------------------------------------


@dataclass
class Point2D:
    """A 2-D point in the floor-plan coordinate system (metres)."""

    x: float
    y: float

    def validate(self) -> None:
        if not isinstance(self.x, (int, float)):
            raise TypeError(f"Point2D.x must be numeric, got {type(self.x)}")
        if not isinstance(self.y, (int, float)):
            raise TypeError(f"Point2D.y must be numeric, got {type(self.y)}")


@dataclass
class Metadata:
    """
    Provenance and scale information attached to every IR document.

    Attributes:
        unit            Physical unit of all length values. Always 'meter'.
        source_type     The origin format of the floor plan.
        scale_confidence
                        'high'      — unit was read from DXF header or a
                                      known PDF scale bar.
                        'estimated' — heuristic / AI-derived scale.
        page_count      Total number of pages in the source document.
        page_processed  The specific page index (1-based) that was processed.
        walls_skipped_non_orthogonal
                        Number of vector paths excluded specifically because 
                        they failed the axis-alignment check.
        walls_skipped_curved
                        Number of vector paths excluded specifically because 
                        they are curved (arcs, circles, splines).
    """

    unit: Literal["meter"] = "meter"
    source_type: Literal[
        "dxf", "pdf_vector", "pdf_scanned", "image"
    ] = "dxf"
    scale_confidence: Literal["high", "estimated"] = "high"
    page_count: int = 1
    page_processed: int = 1
    walls_skipped_non_orthogonal: int = 0
    walls_skipped_curved: int = 0

    def validate(self) -> None:
        if self.unit != "meter":
            raise ValueError(f"Unsupported unit '{self.unit}'. Only 'meter' is allowed.")
        valid_sources = {"dxf", "pdf_vector", "pdf_scanned", "image"}
        if self.source_type not in valid_sources:
            raise ValueError(
                f"Invalid source_type '{self.source_type}'. "
                f"Must be one of {sorted(valid_sources)}."
            )
        valid_confidence = {"high", "estimated"}
        if self.scale_confidence not in valid_confidence:
            raise ValueError(
                f"Invalid scale_confidence '{self.scale_confidence}'. "
                f"Must be one of {sorted(valid_confidence)}."
            )
        if self.page_count < 1:
            raise ValueError(f"page_count must be >= 1, got {self.page_count}")
        if self.page_processed < 1 or self.page_processed > self.page_count:
            raise ValueError(f"page_processed must be between 1 and page_count, got {self.page_processed}")
        if self.walls_skipped_non_orthogonal < 0:
            raise ValueError(f"walls_skipped_non_orthogonal must be >= 0, got {self.walls_skipped_non_orthogonal}")
        if self.walls_skipped_curved < 0:
            raise ValueError(f"walls_skipped_curved must be >= 0, got {self.walls_skipped_curved}")


@dataclass
class Wall:
    """
    A single wall segment represented as a directed line from *start* to *end*.

    Attributes:
        id          Unique identifier within the IR document (e.g. 'wall_1').
        start       Origin point of the wall centre-line (metres).
        end         Terminal point of the wall centre-line (metres).
        thickness   Wall thickness in metres (defaults to 0.2 m).
        height      Wall height in metres (defaults to 3.0 m / one storey).
        thickness_confidence 'measured' if read directly, 'estimated' if using fallback.
    """

    id: str
    start: Point2D
    end: Point2D
    thickness: float = 0.2
    height: float = 3.0
    thickness_confidence: Literal["measured", "estimated"] = "estimated"

    def validate(self) -> None:
        if not self.id:
            raise ValueError("Wall.id must be a non-empty string.")
        self.start.validate()
        self.end.validate()
        if self.thickness <= 0:
            raise ValueError(f"Wall '{self.id}': thickness must be > 0.")
        if self.height <= 0:
            raise ValueError(f"Wall '{self.id}': height must be > 0.")
        if self.thickness_confidence not in {"measured", "estimated"}:
            raise ValueError(f"Wall '{self.id}': thickness_confidence must be 'measured' or 'estimated'.")


@dataclass
class Opening:
    """
    A door or window cut into a wall.

    Attributes:
        id                  Unique identifier (e.g. 'door_1', 'window_3').
        type                'door' or 'window'.
        wall_id             id of the Wall this opening belongs to.
        position_on_wall    Distance from wall.start along the wall centre-line
                            to the centre of the opening (metres).
        width               Clear opening width (metres).
        height              Clear opening height (metres).
    """

    id: str
    type: Literal["door", "window"]
    wall_id: str
    position_on_wall: float
    width: float
    height: float

    def validate(self) -> None:
        if not self.id:
            raise ValueError("Opening.id must be a non-empty string.")
        if self.type not in {"door", "window"}:
            raise ValueError(
                f"Opening '{self.id}': type must be 'door' or 'window', "
                f"got '{self.type}'."
            )
        if not self.wall_id:
            raise ValueError(f"Opening '{self.id}': wall_id must be non-empty.")
        if self.position_on_wall < 0:
            raise ValueError(
                f"Opening '{self.id}': position_on_wall must be >= 0."
            )
        if self.width <= 0:
            raise ValueError(f"Opening '{self.id}': width must be > 0.")
        if self.height <= 0:
            raise ValueError(f"Opening '{self.id}': height must be > 0.")


# ---------------------------------------------------------------------------
# Root document
# ---------------------------------------------------------------------------


@dataclass
class IRFloorPlan:
    """
    Root document of the Intermediate Representation.

    This is the object that every parser returns and the 3D engine ingests.

    Attributes:
        metadata        Provenance and scale information.
        walls           Ordered list of wall segments.
        openings        Ordered list of doors and windows.
        floor_polygon   Ordered vertices of the floor outline (metres).
                        The polygon is assumed to be closed (last vertex
                        connects back to the first).
    """

    metadata: Metadata
    walls: list[Wall] = field(default_factory=list)
    openings: list[Opening] = field(default_factory=list)
    floor_polygon: list[Point2D] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> None:
        """
        Perform full structural validation of the IR document.

        Raises:
            TypeError / ValueError on any constraint violation.
        """
        self.metadata.validate()

        wall_ids: set[str] = set()
        for wall in self.walls:
            wall.validate()
            if wall.id in wall_ids:
                raise ValueError(f"Duplicate wall id: '{wall.id}'.")
            wall_ids.add(wall.id)

        opening_ids: set[str] = set()
        for opening in self.openings:
            opening.validate()
            if opening.id in opening_ids:
                raise ValueError(f"Duplicate opening id: '{opening.id}'.")
            opening_ids.add(opening.id)
            if opening.wall_id not in wall_ids:
                raise ValueError(
                    f"Opening '{opening.id}' references unknown "
                    f"wall_id '{opening.wall_id}'."
                )

        for point in self.floor_polygon:
            point.validate()

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a plain-dict representation suitable for JSON serialisation."""
        return asdict(self)

    def to_json(self, **kwargs) -> str:
        """Serialise to a JSON string. Keyword args forwarded to json.dumps."""
        return json.dumps(self.to_dict(), **kwargs)

    @classmethod
    def from_dict(cls, data: dict) -> "IRFloorPlan":
        """
        Deserialise from a plain dict (e.g. loaded from a JSON file).

        Raises:
            KeyError / TypeError if required fields are missing.
        """
        meta = Metadata(**data["metadata"])
        walls = [
            Wall(
                id=w["id"],
                start=Point2D(**w["start"]),
                end=Point2D(**w["end"]),
                thickness=w.get("thickness", 0.2),
                height=w.get("height", 3.0),
                thickness_confidence=w.get("thickness_confidence", "estimated")
            )
            for w in data.get("walls", [])
        ]
        openings = [
            Opening(
                id=o["id"],
                type=o["type"],
                wall_id=o["wall_id"],
                position_on_wall=o["position_on_wall"],
                width=o["width"],
                height=o["height"],
            )
            for o in data.get("openings", [])
        ]
        floor_polygon = [
            Point2D(**p) for p in data.get("floor_polygon", [])
        ]
        return cls(
            metadata=meta,
            walls=walls,
            openings=openings,
            floor_polygon=floor_polygon,
        )
