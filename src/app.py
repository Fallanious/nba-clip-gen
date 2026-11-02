#!/usr/bin/env python3
<<<<<<< HEAD
"""Flask API Backend - Foundation only."""
import os, sys, uuid, re
=======
"""Flask API Backend for NBA Clip Generator."""

import os
import sys
import uuid

# Ensure src/ is on the import path so `utils.*` and `routes.*` resolve
>>>>>>> 4b1197c (Layer 2: DB)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, send_file, g, request
from flask_cors import CORS
import requests as _requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIST = os.path.join(BASE_DIR, "frontend", "dist")


def create_app():
    """Application factory."""
    # Import config here so env vars are read fresh
    from config import config
<<<<<<< HEAD
    from auth import validate_auth_for_startup
=======
    from auth import before_request_hook
>>>>>>> 4b1197c (Layer 2: DB)
    from errors import register_error_handlers
    from routes.jobs import bp as jobs_bp
    from routes.videos import bp as videos_bp
    from routes.timestamps import bp as timestamps_bp
    from routes.actions import bp as actions_bp
    from routes.players import bp as players_bp

    app = Flask(__name__, static_folder=FRONTEND_DIST, static_url_path="")

    # Validate config
    config.validate()
    validate_auth_for_startup()

    # Configure CORS with explicit origins
    CORS(app, origins=config.ALLOWED_ORIGINS)

    # Register error handlers
    register_error_handlers(app)

    # Auth seam
    app.before_request(before_request_hook)
    
    # Database session management - lazy session per request
    @app.teardown_request
    def teardown_request_session(exception=None):
        """Close the database session for this request (if one was opened)."""
        if hasattr(g, '_db_session'):
            try:
                if exception:
                    g._db_session.__exit__(type(exception), exception, exception.__traceback__)
                else:
                    g._db_session.__exit__(None, None, None)
            except Exception as teardown_error:
                # Log teardown errors and surface them as 500
                app.logger.error(f"Session teardown failed: {teardown_error}", exc_info=True)
                # Re-raise to let Flask error handler convert to 500
                raise
            finally:
                if hasattr(g, '_db_session'):
                    delattr(g, '_db_session')
                if hasattr(g, '_db_session_ctx'):
                    delattr(g, '_db_session_ctx')

    # Register blueprints
    app.register_blueprint(jobs_bp)
    app.register_blueprint(videos_bp)
    app.register_blueprint(timestamps_bp)
    app.register_blueprint(actions_bp)
    app.register_blueprint(players_bp)

    @app.before_request
    def add_request_id():
<<<<<<< HEAD
        """Add or validate request ID."""
        req_id = request.headers.get('X-Request-Id', '')
        # Accept only UUID or alphanumeric with hyphens (1-64 chars)
        if req_id and re.match(r'^[A-Za-z0-9-]{1,64}$', req_id):
            g.request_id = req_id
        else:
            g.request_id = str(uuid.uuid4())
    
=======
        """Add request ID to g for logging and error tracking."""
        g.request_id = request.headers.get('X-Request-Id', str(uuid.uuid4()))

>>>>>>> 4b1197c (Layer 2: DB)
    @app.after_request
    def add_response_headers(response):
        """Add standard headers to all responses."""
        response.headers['X-Request-Id'] = g.get('request_id', '')
        if 'Vary' not in response.headers:
            response.headers['Vary'] = 'Origin'
        return response

    return app


# Create app instance for direct execution
app = create_app()


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
            response = _requests.get(config.OLLAMA_URL + "/api/tags", timeout=5)
            if response.status_code == 200:
                models = response.json().get("models", [])
                model_names = [m.get("name", "") for m in models]
                return {"status": "online", "models": model_names}
        except Exception:
            pass
        return {"status": "offline", "models": []}

    @app.route("/api/health/db")
    def health_db():
        """Check database connectivity."""
        if not config.DATABASE_URL:
            return {"status": "not_configured"}, 503

        try:
            from db.session import get_session
            with get_session() as session:
                session.execute("SELECT 1")
            return {"status": "healthy"}
        except Exception as e:
            app.logger.error(f"Database health check failed: {e}")
            return {"status": "unhealthy", "error": str(e)}, 503


if __name__ == "__main__":
    os.makedirs(os.path.join(BASE_DIR, "film"), exist_ok=True)
    os.makedirs(os.path.join(BASE_DIR, "playbyplay"), exist_ok=True)
    os.makedirs(os.path.join(BASE_DIR, "output", "cliptimestamps"), exist_ok=True)
    os.makedirs(os.path.join(BASE_DIR, "output", "clips"), exist_ok=True)

    print("Starting NBA Clip Generator API...")
    print("API running at http://localhost:5001")
    print("Make sure Ollama is running on localhost:11434")

    from config import config
    app.run(
        debug=config.DEBUG,
        host=os.getenv("BIND_HOST", "127.0.0.1"),  # Default to localhost; use 0.0.0.0 or gunicorn in prod
        port=int(os.getenv("PORT", "5001"))
    )
