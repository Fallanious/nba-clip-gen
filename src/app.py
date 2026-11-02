#!/usr/bin/env python3
"""Flask API Backend - Foundation only."""
import os, sys, uuid
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flask import Flask, g, request
from flask_cors import CORS

def create_app():
    from config import config
    from errors import register_error_handlers
    from routes.jobs import bp as jobs_bp
    from routes.videos import bp as videos_bp
    from routes.timestamps import bp as timestamps_bp
    from routes.actions import bp as actions_bp
    
    app = Flask(__name__)
    config.validate()
    CORS(app, origins=config.ALLOWED_ORIGINS)
    register_error_handlers(app)
    
    app.register_blueprint(jobs_bp)
    app.register_blueprint(videos_bp)
    app.register_blueprint(timestamps_bp)
    app.register_blueprint(actions_bp)
    
    @app.before_request
    def add_request_id():
        g.request_id = request.headers.get('X-Request-Id', str(uuid.uuid4()))
    
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
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5001"))
    )
