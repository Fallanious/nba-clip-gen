#!/usr/bin/env python3
"""Flask API Backend - Foundation only."""
import os, sys, uuid, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flask import Flask, g, request
from flask_cors import CORS

def create_app():
    from config import config
    from auth import validate_auth_for_startup
    from errors import register_error_handlers
    from routes.jobs import bp as jobs_bp
    from routes.videos import bp as videos_bp
    from routes.timestamps import bp as timestamps_bp
    from routes.actions import bp as actions_bp
    from routes.players import bp as players_bp
    
    app = Flask(__name__)
    config.validate()
    validate_auth_for_startup()
    CORS(app, origins=config.ALLOWED_ORIGINS)
    register_error_handlers(app)
    
    app.register_blueprint(jobs_bp)
    app.register_blueprint(videos_bp)
    app.register_blueprint(timestamps_bp)
    app.register_blueprint(actions_bp)
    app.register_blueprint(players_bp)
    
    @app.before_request
    def add_request_id():
        """Add or validate request ID."""
        req_id = request.headers.get('X-Request-Id', '')
        # Accept only UUID or alphanumeric with hyphens (1-64 chars)
        if req_id and re.fullmatch(r'[A-Za-z0-9-]{1,64}', req_id):
            g.request_id = req_id
        else:
            g.request_id = str(uuid.uuid4())
    
    @app.after_request
    def add_response_headers(response):
        response.headers['X-Request-Id'] = g.get('request_id', '')
        return response
    
    return app

app = create_app()

if __name__ == "__main__":
    from config import config
    app.run(
        debug=config.DEBUG,
        host=os.getenv("BIND_HOST", "127.0.0.1"),  # Default to localhost; use 0.0.0.0 or gunicorn in prod
        port=int(os.getenv("PORT", "5001"))
    )
