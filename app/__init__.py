"""
app/__init__.py
Flask Application Factory for the 2D-to-3D Floor Plan Viewer.
"""

from flask import Flask


def create_app(config: dict | None = None) -> Flask:
    """
    Application factory.

    Args:
        config: Optional dictionary of configuration overrides.
                Useful for injecting test configs.

    Returns:
        A fully configured Flask application instance.
    """
    app = Flask(__name__)

    # ------------------------------------------------------------------ #
    # Default configuration
    # ------------------------------------------------------------------ #
    app.config.setdefault("DEBUG", False)
    app.config.setdefault("TESTING", False)
    # 20MB limit for file uploads
    app.config.setdefault("MAX_CONTENT_LENGTH", 20 * 1024 * 1024)

    # Apply any caller-supplied overrides
    if config:
        app.config.update(config)

    # ------------------------------------------------------------------ #
    # Register Blueprints
    # ------------------------------------------------------------------ #
    from app.routes.health import health_bp  # noqa: PLC0415
    from app.routes.convert import convert_bp
    from app.routes.frontend import frontend_bp

    app.register_blueprint(health_bp)
    app.register_blueprint(convert_bp)
    app.register_blueprint(frontend_bp)

    # Start background tasks
    from app.engine.cleanup import start_background_cleanup_task
    start_background_cleanup_task()

    return app
