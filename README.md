# 2D CAD / Image → 3D Floor Plan Viewer

> Convert DXF files, PDFs, and raster images of architectural floor plans into interactive 3D models viewable in a browser.

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Flask](https://img.shields.io/badge/Flask-3.0.0+-green.svg)](https://flask.palletsprojects.com/)
[![Trimesh](https://img.shields.io/badge/Trimesh-5.0.0-orange.svg)](https://trimesh.org/)

## 📖 Project Overview

This tool is a comprehensive pipeline designed to accept 2-D architectural floor plans in various formats — **AutoCAD DXF**, **PDF** (vector or scanned), and **raster images** (PNG/JPEG) — and autonomously output an interactive 3-D model (`.glb`). This 3D model can be embedded or explored directly in any modern web browser.

It solves the problem of manual 3D modeling from 2D plans by using a combination of geometric parsing for vector formats and computer vision (Semantic Segmentation) for rasterized inputs.

### 🚀 Key Features
- **Multi-format Ingestion:** Supports `.dxf`, `.pdf`, `.png`, `.jpg`, and `.jpeg`.
- **Intelligent Scaling:** Automatically estimates real-world dimensions (meters) if scale is missing, using average door widths.
- **Robust 3D Engine:** Extrudes walls, subtracts door/window openings, and outputs an optimized GLB file using solid boolean operations (via Manifold3D).
- **JSON IR (Intermediate Representation):** Decouples parsing from 3D generation, providing a unified and standardized schema for floor plans.
- **API First:** Includes a robust Flask REST API for easy integration.

---

## ⚙️ How It Works: The Pipeline

The system is designed with a decoupled architecture working through four distinct stages:

1. **Input Parsing** (`app/parsers/`): 
   - **Vector Data (DXF/PDF):** Extracts lines, polylines, and blocks directly, identifying walls and openings based on geometry.
   - **Raster Data (Images/Scanned PDFs):** A Computer Vision segmentation model (`app/models/`) detects walls, doors, and windows, generating pixel masks.
2. **Intermediate Representation (IR)**: The parsed geometric data or masks are converted into a unified **JSON IR** (`app/schemas/ir_schema.py`). This strictly validates and normalizes the floor plan into physical units (meters), identifying wall centerlines, thicknesses, heights, and door/window openings.
3. **3D Geometry Engine** (`app/engine/`): The engine reads the standardized JSON IR, processes it using computational geometry (Shapely, Trimesh, Manifold3D), extrudes walls, boolean-subtracts openings, and generates a seamless 3D mesh.
4. **Export & Delivery**: The generated scene is exported as a standard glTF binary (`.glb`) and delivered to the user via the API for immediate web consumption.

---

## 🚀 Local Setup (< 10 minutes)

Follow these steps to run the project locally on your machine.

### Prerequisites
- **Python**: 3.11 or higher
- **pip**: 23 or higher
- **Git**

### 1 — Clone the repository

```bash
git clone <repository-url>
cd "2D to 3D"
```

### 2 — Create and activate a virtual environment

**Windows (PowerShell):**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

**macOS / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3 — Install dependencies

> **Note:** PyTorch is listed in `requirements.txt` without a CUDA build suffix so that `pip` installs the CPU-only wheel by default for broad compatibility. If you have a CUDA-capable GPU and want GPU acceleration for raster images, see the [PyTorch installation guide](https://pytorch.org/get-started/locally/) and install the matching CUDA wheel manually.

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4 — Start the development server

Start the Flask application using the provided run script:
```bash
python run.py
```
The server will start at **http://localhost:5000**.

### 5 — Verify the setup & How to Use

1. **Health Check:** 
   Check the health endpoint (should return `{"status": "ok"}`):
   ```bash
   curl http://localhost:5000/health
   ```
2. **Using the API:**
   You can convert a file by sending a `POST` request to `/api/convert`:
   ```bash
   curl -F "file=@your_floorplan.png" http://localhost:5000/api/convert
   ```
   The API will return a JSON response containing a `job_id` and a `download_url` for the resulting `.glb` file.
   Download the model:
   ```bash
   curl -O -J http://localhost:5000/api/download/<job_id>
   ```

---

## 📁 Project Structure & File Organization

The repository is modularized into dedicated components:

```text
2D to 3D/
├── app/                     # Main Application Package
│   ├── __init__.py          # Flask app factory (create_app initialization)
│   ├── routes/              # HTTP Endpoints
│   │   ├── convert.py       # Main API endpoint (/api/convert and /api/download)
│   │   └── health.py        # Healthcheck endpoint (if separated)
│   ├── parsers/             # Ingestion pipelines for various formats
│   │   ├── dxf_to_ir.py     # DXF parsing logic
│   │   ├── pdf_to_ir.py     # PDF (vector/scanned) parsing logic
│   │   ├── image_to_ir.py   # Raster image segmentation & OpenCV logic
│   │   └── exceptions.py    # Custom parser exceptions
│   ├── engine/              # 3D geometry engine
│   │   ├── builder.py       # Orchestrator for 3D generation
│   │   └── meshing.py       # Trimesh & boolean operations (if applicable)
│   ├── models/              # AI model loaders & CV inference (Segmentation)
│   ├── schemas/             # Data validation and structures
│   │   └── ir_schema.py     # Pydantic/Dataclass JSON IR definition
│   └── templates/           # HTML templates (if serving frontend directly)
├── docs/                    # Project Documentation
│   ├── json_ir_schema.md    # Full documentation of the IR specification
│   ├── sample_ir_output.json# Example of what the parsers produce
│   ├── known_issues.md      # Documented limitations
│   └── phase*.md            # Detailed documentation mapping to project phases
├── tests/                   # Pytest test suite for unit and integration testing
│   ├── test_pdf_parser.py   # Tests specifically for the PDF parser
│   └── fixtures/            # Test assets (DXF, PDF, Images)
├── run.py                   # Development server entry point
├── debug_pipeline.py        # Utility to generate visual overlays for CV debugging
└── requirements.txt         # Python Dependencies list
```

---

## 🧩 The JSON Intermediate Representation (IR)

The core architectural decision of this project is the **JSON IR**. All parsing pipelines, regardless of how complex or different the input format is, produce this common JSON object. The 3D engine strictly consumes this IR, completely decoupling the parsing logic from the 3D generation logic. 

**Key Components of the IR:**
- **`metadata`**: Details like scale confidence (known vs estimated), source type, and units.
- **`walls`**: Wall segments defined by `start` (x,y), `end` (x,y), `thickness`, and `height`.
- **`openings`**: Doors and windows defined by their `position_on_wall`, `width`, and `height`.
- **`floor_polygon`**: Ordered 2D vertices defining the outer footprint/outline of the floor.

📄 **Full specification:** [`docs/json_ir_schema.md`](docs/json_ir_schema.md)  
📄 **Sample output:** [`docs/sample_ir_output.json`](docs/sample_ir_output.json)  
🐍 **Python dataclasses:** [`app/schemas/ir_schema.py`](app/schemas/ir_schema.py)

---

## 🛠️ Debugging and Visualization

If you are modifying the parsing pipelines or AI models, the project includes visualization tools to help trace how an image is transformed into the structural IR.

To run a debug visualization that overlays the detected walls and openings over a test image:
```bash
python debug_pipeline.py
```
This generates a `debug_output.png` highlighting wall centerlines (blue), door bounding boxes (magenta), and window bounding boxes (orange).

---

## ✅ Running Tests

The project uses `pytest` for rigorous testing of the pipelines. To run the full test suite:
```bash
pytest tests/ -v
```

---
