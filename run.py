"""
run.py
Entry point for the 2D-to-3D Floor Plan Viewer development server.

Usage:
    python run.py
"""

from app import create_app

app = create_app(config={"DEBUG": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=app.config["DEBUG"])
