"""
tests/test_health.py
Smoke tests for the health-check and root endpoints.
Run with: pytest tests/ -v
"""

import pytest
from app import create_app


@pytest.fixture()
def app():
    """Create a test-mode app instance."""
    return create_app(config={"TESTING": True, "DEBUG": False})


@pytest.fixture()
def client(app):
    """Flask test client."""
    return app.test_client()


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


def test_health_status_code(client):
    """GET /health must return HTTP 200."""
    response = client.get("/health")
    assert response.status_code == 200


def test_health_body(client):
    """GET /health must return {"status": "ok"}."""
    response = client.get("/health")
    data = response.get_json()
    assert data == {"status": "ok"}


def test_health_content_type(client):
    """GET /health must return application/json."""
    response = client.get("/health")
    assert response.content_type.startswith("application/json")


# ---------------------------------------------------------------------------
# /
# ---------------------------------------------------------------------------


def test_root_status_code(client):
    """GET / must return HTTP 200."""
    response = client.get("/")
    assert response.status_code == 200


def test_root_content_type(client):
    """GET / must return text/html."""
    response = client.get("/")
    assert response.content_type.startswith("text/html")


# ---------------------------------------------------------------------------
# IR Schema
# ---------------------------------------------------------------------------


def test_ir_schema_round_trip():
    """IRFloorPlan serialises to dict and back without data loss."""
    from app.schemas.ir_schema import IRFloorPlan, Metadata, Wall, Opening, Point2D

    plan = IRFloorPlan(
        metadata=Metadata(unit="meter", source_type="dxf", scale_confidence="high"),
        walls=[
            Wall(
                id="wall_1",
                start=Point2D(x=0.0, y=0.0),
                end=Point2D(x=5.0, y=0.0),
                thickness=0.2,
                height=3.0,
            )
        ],
        openings=[
            Opening(
                id="door_1",
                type="door",
                wall_id="wall_1",
                position_on_wall=2.5,
                width=0.9,
                height=2.1,
            )
        ],
        floor_polygon=[
            Point2D(x=0.0, y=0.0),
            Point2D(x=5.0, y=0.0),
            Point2D(x=5.0, y=4.0),
            Point2D(x=0.0, y=4.0),
        ],
    )

    plan.validate()

    # Round-trip through dict
    restored = IRFloorPlan.from_dict(plan.to_dict())
    restored.validate()

    assert restored.metadata.source_type == "dxf"
    assert restored.walls[0].id == "wall_1"
    assert restored.openings[0].wall_id == "wall_1"
    assert len(restored.floor_polygon) == 4


def test_ir_schema_duplicate_wall_id_raises():
    """validate() must raise ValueError for duplicate wall ids."""
    import pytest
    from app.schemas.ir_schema import IRFloorPlan, Metadata, Wall, Point2D

    plan = IRFloorPlan(
        metadata=Metadata(),
        walls=[
            Wall(id="wall_1", start=Point2D(0, 0), end=Point2D(1, 0)),
            Wall(id="wall_1", start=Point2D(1, 0), end=Point2D(1, 1)),  # duplicate
        ],
    )
    with pytest.raises(ValueError, match="Duplicate wall id"):
        plan.validate()


def test_ir_schema_orphan_opening_raises():
    """validate() must raise ValueError when opening references a missing wall."""
    import pytest
    from app.schemas.ir_schema import IRFloorPlan, Metadata, Opening

    plan = IRFloorPlan(
        metadata=Metadata(),
        openings=[
            Opening(
                id="door_1",
                type="door",
                wall_id="nonexistent_wall",
                position_on_wall=1.0,
                width=0.9,
                height=2.1,
            )
        ],
    )
    with pytest.raises(ValueError, match="unknown wall_id"):
        plan.validate()
