"""
app/routes/health.py
Blueprint for the health-check and root welcome endpoints.
"""

from flask import Blueprint, jsonify

health_bp = Blueprint("health", __name__)


@health_bp.get("/health")
def health_check():
    """
    GET /health

    Lightweight liveness probe used by orchestrators (e.g., Docker, k8s)
    and monitoring systems to confirm the server is running.

    Returns:
        200 {"status": "ok"}
    """
    return jsonify({"status": "ok"}), 200


