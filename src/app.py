#!/usr/bin/env python3
"""Flask API Backend for NBA Clip Generator."""

import os
import sys

# Ensure src/ is on the import path so `utils.*` and `routes.*` resolve
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, send_file
from flask_cors import CORS
import requests as _requests

from routes.jobs import bp as jobs_bp
from routes.videos import bp as videos_bp
from routes.timestamps import bp as timestamps_bp
from routes.actions import bp as actions_bp

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIST = os.path.join(BASE_DIR, "frontend", "dist")

app = Flask(__name__, static_folder=FRONTEND_DIST, static_url_path="")
CORS(app)

# Register blueprints
app.register_blueprint(jobs_bp)
app.register_blueprint(videos_bp)
app.register_blueprint(timestamps_bp)
app.register_blueprint(actions_bp)


@app.route("/")
def index():
    """Serve the React app."""
    index_path = os.path.join(FRONTEND_DIST, "index.html")
    if os.path.exists(index_path):
        return send_file(index_path)
    return "<h1>Frontend not built</h1><p>Run <code>cd frontend && npm run build</code></p>", 404


@app.route("/api/status")
def get_status():
    """Check if Ollama is running."""
    try:
        response = _requests.get("http://localhost:11434/api/tags", timeout=5)
        if response.status_code == 200:
            models = response.json().get("models", [])
            model_names = [m.get("name", "") for m in models]
            return {"status": "online", "models": model_names}
    except Exception:
        pass
    return {"status": "offline", "models": []}


if __name__ == "__main__":
    os.makedirs(os.path.join(BASE_DIR, "film"), exist_ok=True)
    os.makedirs(os.path.join(BASE_DIR, "playbyplay"), exist_ok=True)
    os.makedirs(os.path.join(BASE_DIR, "output", "cliptimestamps"), exist_ok=True)
    os.makedirs(os.path.join(BASE_DIR, "output", "clips"), exist_ok=True)

    print("Starting NBA Clip Generator API...")
    print("API running at http://localhost:5001")
    print("Make sure Ollama is running on localhost:11434")

    app.run(debug=True, port=5001)
