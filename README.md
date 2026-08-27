# 2D CAD / Image → 3D Floor Plan Viewer

> Convert DXF files, PDFs, and raster images of architectural floor plans into interactive 3D models viewable in a browser.

## Project Overview

This tool accepts 2-D architectural floor plans in three input formats — AutoCAD DXF, PDF (vector or scanned), and raster images (PNG/JPEG) — and outputs an interactive 3-D model (`.glb`) that can be explored directly in a web browser. 

### How It Works: The Pipeline
The pipeline works through a series of decoupled stages:
1. **Input Parsing**: Raw files (DXF/PDF/Images) are read. For images, a Computer Vision segmentation model (`app.models.floorplan_segmenter`) detects walls, doors, and windows to create a pixel mask.
2. **Intermediate Representation (IR)**: The mask or vector data is converted into a unified **JSON IR** (`app/schemas/ir_schema.py`). This format describes the floor plan in physical units (meters), identifying wall centerlines, thicknesses, heights, and door/window openings.
3. **3D Geometry Engine**: The engine reads the JSON IR and extrudes walls, places doors and windows, and generates a 3D mesh.
4. **Export**: The scene is exported as a standard glTF binary (`.glb`) for web consumption.

---

## Local Setup (< 10 minutes)

### Prerequisites
- **Python**: 3.11+
- **pip**: 23+
- **Git**

### 1 — Clone the repository

```bash
git clone <repository-url>
cd "2D to 3D"
```

### 2 — Create and activate a virtual environment

**Windows (PowerShell)**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

**macOS / Linux**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3 — Install dependencies

> **Note:** PyTorch is listed in `requirements.txt` without a CUDA build suffix so that `pip` installs the CPU-only wheel by default. If you have a CUDA-capable GPU and want GPU acceleration, see the [PyTorch installation guide](https://pytorch.org/get-started/locally/) and install the matching CUDA wheel manually.

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4 — Start the development server

```bash
python run.py
```
The server starts at **http://localhost:5000**.

### 5 — Verify the setup

Check the health endpoint (should return `{"status": "ok"}`):
```bash
curl http://localhost:5000/health
```

---

## Project Structure & Architecture

```text
project-root/
├── app/
│   ├── __init__.py          # Flask app factory (create_app)
│   ├── routes/              # HTTP Endpoints (e.g., /health, /api/process)
│   ├── parsers/             # Ingestion pipelines for DXF, PDF, Image formats
│   ├── engine/              # 3D geometry engine (Meshing, extrusion)
│   ├── models/              # AI model loaders & CV inference (Segmentation)
│   └── schemas/             # JSON IR dataclasses & validation (ir_schema.py)
├── docs/
│   ├── json_ir_schema.md    # Full IR specification
│   └── sample_ir_output.json
├── scripts/
│   └── benchmark.py         # Performance profiling and benchmarking
├── tests/                   # Pytest test suite
├── debug_pipeline.py        # Utility to generate visual overlays for debugging
├── run.py                   # Dev server entry point
└── requirements.txt         # Dependencies
```

---

## The JSON Intermediate Representation (IR)

All parsing pipelines produce a common JSON IR. The 3D engine strictly consumes this IR, completely decoupling the parsing logic from the 3D generation logic. 

**Key Components of the IR:**
- **metadata**: Scale confidence, source type, units.
- **walls**: Segments defined by `start`, `end`, `thickness`, and `height`.
- **openings**: Doors and windows defined by `position_on_wall`, `width`, and `height`.
- **floor_polygon**: Ordered 2D vertices of the floor outline.

📄 **Full specification:** [`docs/json_ir_schema.md`](docs/json_ir_schema.md)  
📄 **Sample output:** [`docs/sample_ir_output.json`](docs/sample_ir_output.json)  
🐍 **Python dataclasses:** [`app/schemas/ir_schema.py`](app/schemas/ir_schema.py)

---

## Debugging and Visualization

If you are modifying the parsing or AI models, the project includes visualization tools to help trace how an image is transformed into the IR.

To run a debug visualization that overlays the detected walls and openings over a test image:
```bash
python debug_pipeline.py
```
This generates a `debug_output.png` highlighting wall centerlines, door bounding boxes (magenta), and window bounding boxes (orange). 

---

## Running Tests

The project uses `pytest`. To run the full suite:
```bash
pytest tests/ -v
```

---

## Roadmap

| Phase | Scope | Status |
|-------|-------|--------|
| **0** | Repository structure, Flask backend, JSON IR specification | ✅ |
| **1** | DXF parser, PDF parser (vector + scanned OCR), image parser | In Progress |
| **2** | AI-based wall/opening detection model integration | In Progress |
| **3** | 3D geometry engine, `.glb` export | Pending |
| **4** | Frontend — browser-based 3D viewer | Pending |
| **5** | Packaging, Docker, deployment | Pending |
