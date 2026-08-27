from flask import Blueprint, render_template

frontend_bp = Blueprint("frontend", __name__)

@frontend_bp.route("/", methods=["GET"])
def index():
    return render_template("index.html")
