"""API error handling."""

from typing import Optional, Any
from flask import jsonify
from sqlalchemy.exc import OperationalError
from botocore.exceptions import ClientError, NoCredentialsError


class ApiError(Exception):
    """Base API error with standard envelope."""

    def __init__(
        self,
        code: str,
        message: str,
        status: int = 400,
        details: Optional[dict[str, Any]] = None
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}

    def to_dict(self):
        """Convert to error envelope."""
        error_obj = {
            "code": self.code,
            "message": self.message,
        }
        if self.details:
            error_obj["details"] = self.details
        return {"error": error_obj}


def register_error_handlers(app):
    """Register Flask error handlers."""

    @app.errorhandler(ApiError)
    def handle_api_error(e: ApiError):
        """Handle ApiError exceptions."""
        response = jsonify(e.to_dict())
        if e.status == 503:
            response.headers["Retry-After"] = "2"
        return response, e.status

    @app.errorhandler(OperationalError)
    def handle_db_error(e: OperationalError):
        """Handle database operational errors."""
        app.logger.error(f"Database error: {e}")
        error = ApiError(
            code="db_unavailable",
            message="Database temporarily unavailable",
            status=503
        )
        response = jsonify(error.to_dict())
        response.headers["Retry-After"] = "2"
        return response, 503

    @app.errorhandler(ClientError)
    def handle_s3_error(e: ClientError):
        """Handle boto3 S3 client errors."""
        app.logger.error(f"S3 error: {e}")
        if e.response['Error']['Code'] == 'NoSuchKey':
            error = ApiError(
                code="media_missing",
                message="Media file not found",
                status=404
            )
            return jsonify(error.to_dict()), 404
        else:
            error = ApiError(
                code="storage_error",
                message="Storage service error",
                status=502
            )
            return jsonify(error.to_dict()), 502

    @app.errorhandler(NoCredentialsError)
    def handle_no_credentials(e: NoCredentialsError):
        """Handle missing AWS credentials."""
        app.logger.error(f"AWS credentials error: {e}")
        error = ApiError(
            code="storage_unavailable",
            message="Storage service unavailable",
            status=503
        )
        return jsonify(error.to_dict()), 503

    @app.errorhandler(404)
    def handle_not_found(e):
        """Handle Flask 404."""
        return jsonify({
            "error": {
                "code": "not_found",
                "message": "Resource not found"
            }
        }), 404

    @app.errorhandler(500)
    def handle_internal_error(e):
        """Handle internal server errors."""
        app.logger.error(f"Internal error: {e}")
        return jsonify({
            "error": {
                "code": "internal",
                "message": "Internal server error"
            }
        }), 500
