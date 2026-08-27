import pytest
import io
import json
from flask import url_for
from pathlib import Path

from app import create_app

@pytest.fixture
def app():
    # Use TESTING=True and MAX_CONTENT_LENGTH=20MB for tests
    app = create_app({"TESTING": True, "MAX_CONTENT_LENGTH": 20 * 1024 * 1024})
    yield app

@pytest.fixture
def client(app):
    return app.test_client()

def test_missing_file(client):
    res = client.post("/api/convert")
    assert res.status_code == 400
    assert b"No file field" in res.data

def test_unsupported_file_type(client):
    data = {
        "file": (io.BytesIO(b"dummy data"), "test.txt")
    }
    res = client.post("/api/convert", data=data, content_type="multipart/form-data")
    assert res.status_code == 400
    assert b"Unsupported file type" in res.data

def test_oversized_file(client):
    # App is configured with 20MB max. Send 21MB.
    large_data = b"0" * (21 * 1024 * 1024)
    data = {
        "file": (io.BytesIO(large_data), "huge.png")
    }
    res = client.post("/api/convert", data=data, content_type="multipart/form-data")
    # Werkzeug aborts with 413 Payload Too Large
    assert res.status_code == 413

def test_successful_dxf_upload(client):
    # Use real DXF fixture
    dxf_path = Path("tests/fixtures/fixture_simple.dxf")
    if not dxf_path.exists():
        pytest.skip("No fixture_simple.dxf fixture found")
        
    with open(dxf_path, "rb") as f:
        data = {"file": (f, "test.dxf")}
        res = client.post("/api/convert", data=data, content_type="multipart/form-data")
        
    assert res.status_code == 200
    json_data = res.get_json()
    assert "download_url" in json_data
    assert "job_id" in json_data
    
    # Check download endpoint
    dl_res = client.get(json_data["download_url"])
    assert dl_res.status_code == 200
    assert dl_res.mimetype == "model/gltf-binary"

def test_image_sanity_check_trigger(client):
    # Feed wikipedia_real.png to trigger sanity check warnings
    img_path = Path("tests/fixtures/real_floorplans/wikipedia_real.png")
    if not img_path.exists():
        pytest.skip("No wikipedia image fixture found")
        
    with open(img_path, "rb") as f:
        data = {"file": (f, "wikipedia_real.png")}
        res = client.post("/api/convert", data=data, content_type="multipart/form-data")
        
    assert res.status_code == 200
    json_data = res.get_json()
    
    warnings = json_data.get("warnings", [])
    
    # Assert actual warning texts
    assert any("Unusually high wall count detected" in w for w in warnings)
    assert any("Scale is estimated" in w for w in warnings)

def test_pdf_upload(client):
    pdf_path = Path("tests/fixtures/fixture_simple.pdf")
    if not pdf_path.exists():
        pytest.skip("No fixture_simple.pdf fixture found")
        
    with open(pdf_path, "rb") as f:
        data = {"file": (f, "test.pdf")}
        res = client.post("/api/convert", data=data, content_type="multipart/form-data")
        
    assert res.status_code == 200
    json_data = res.get_json()
    assert "download_url" in json_data
