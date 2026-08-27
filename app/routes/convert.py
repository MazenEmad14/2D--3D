import os
import uuid
import time
import logging
from pathlib import Path
import tempfile

from flask import Blueprint, request, jsonify, send_file
from werkzeug.utils import secure_filename

from app.parsers.dxf_to_ir import parse_dxf
from app.parsers.pdf_to_ir import parse_pdf
from app.parsers.image_to_ir import parse_image
from app.parsers.exceptions import PDFRenderedToImage
from app.engine.builder import generate_3d_model

import trimesh

logger = logging.getLogger(__name__)

convert_bp = Blueprint("convert", __name__, url_prefix="/api")

TEMP_DIR = Path(tempfile.gettempdir()) / "2d_to_3d_uploads"
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# Cleanup Policy Documented:
# 1. Input files: Deleted immediately via a `finally` block after parsing finishes (whether success or failure).
# 2. Output .glb files: Stored in TEMP_DIR. They should be cleaned up by a system cron job (e.g., standard /tmp cleanup) 
#    or explicitly deleted by the client after download. We do not automatically delete after download to support 
#    resuming failed downloads or multiple fetches.

@convert_bp.route("/convert", methods=["POST"])
def convert_file():
    if "file" not in request.files:
        return jsonify({"error": "No file field in request"}), 400
        
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No selected file"}), 400
        
    known_door_width_m = request.form.get("known_door_width_m", type=float)
    
    ext = Path(file.filename).suffix.lower()
    allowed_exts = {".dxf", ".pdf", ".png", ".jpg", ".jpeg"}
    
    if ext not in allowed_exts:
        return jsonify({"error": f"Unsupported file type: {ext}. Allowed: {allowed_exts}"}), 400
        
    job_id = str(uuid.uuid4())
    safe_filename = secure_filename(file.filename)
    input_path = TEMP_DIR / f"{job_id}_{safe_filename}"
    glb_path = TEMP_DIR / f"{job_id}.glb"
    
    file.save(input_path)
    
    try:
        t0 = time.time()
        
        # 1. Parse to IR
        try:
            if ext == ".dxf":
                ir = parse_dxf(input_path)
            elif ext == ".pdf":
                try:
                    ir = parse_pdf(input_path)
                except PDFRenderedToImage as p_exc:
                    ir = parse_image(p_exc.image_path, known_door_width_m=known_door_width_m)
                    # Try to clean up the temporary image
                    try:
                        os.remove(p_exc.image_path)
                    except:
                        pass
            else:
                ir = parse_image(input_path, known_door_width_m=known_door_width_m)
        except Exception as e:
            logger.exception("Parsing failed")
            return jsonify({"error": f"Parsing stage failed: {str(e)}"}), 422
            
        # 2. IR Validation (Already done natively by parsers, but just in case)
        try:
            ir.validate()
        except Exception as e:
            return jsonify({"error": f"IR validation failed: {str(e)}"}), 422
            
        # 3. Sanity-Check Gate
        warnings = []
        if len(ir.walls) > 30:
            warnings.append("Unusually high wall count detected. Input may be noisy.")
            
        has_absurd_dimension = False
        for w in ir.walls:
            if w.thickness > 5.0:
                has_absurd_dimension = True
                break
        for o in ir.openings:
            if o.width > 5.0:
                has_absurd_dimension = True
                break
                
        if has_absurd_dimension:
            warnings.append("Absurdly large structural dimensions detected. Geometry may be distorted.")
            
        if ir.metadata.scale_confidence == "estimated":
            warnings.append("Scale is estimated. Physical dimensions are likely architecturally inaccurate without calibration.")
            
        if ir.metadata.page_count > 1:
            warnings.append(f"Only page {ir.metadata.page_processed} was processed from the {ir.metadata.page_count}-page document.")
            
        if ir.metadata.walls_skipped_non_orthogonal > 0:
            warnings.append(f"{ir.metadata.walls_skipped_non_orthogonal} non-orthogonal walls were skipped and excluded from the 3D model.")
            
        # 4. Generate 3D Model
        try:
            generate_3d_model(ir, glb_path)
        except Exception as e:
            logger.exception("3D Generation failed")
            return jsonify({"error": f"3D Generation stage failed: {str(e)}"}), 500
            
        # Check union status from the generated GLB
        try:
            scene = trimesh.load(glb_path, process=False)
            if scene.metadata.get("geometry_union_status") == "failed_fallback_overlapping_meshes":
                warnings.append("CRITICAL: The floor plan was too complex for a clean 3D union. The model may contain visible seams or overlapping wall artifacts.")
        except Exception as e:
            logger.warning(f"Failed to read union status from GLB: {e}")
            
        t1 = time.time()
        
        estimated_walls = sum(1 for w in ir.walls if w.thickness_confidence == "estimated")
        
        return jsonify({
            "job_id": job_id,
            "download_url": f"/api/download/{job_id}",
            "metadata": {
                "wall_count": len(ir.walls),
                "opening_count": len(ir.openings),
                "estimated_walls_count": estimated_walls,
                "scale_confidence": ir.metadata.scale_confidence,
                "processing_time_sec": round(t1 - t0, 2)
            },
            "warnings": warnings
        }), 200
        
    finally:
        # Cleanup input file immediately
        if input_path.exists():
            try:
                os.remove(input_path)
            except Exception as e:
                logger.warning(f"Failed to cleanup input file {input_path}: {e}")

@convert_bp.route("/download/<job_id>", methods=["GET"])
def download_glb(job_id):
    glb_path = TEMP_DIR / f"{job_id}.glb"
    if not glb_path.exists():
        return jsonify({"error": "File not found or expired"}), 404
        
    return send_file(
        glb_path,
        as_attachment=True,
        download_name=f"model_{job_id}.glb",
        mimetype="model/gltf-binary"
    )
