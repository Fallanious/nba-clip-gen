"""Authentication and authorization seam."""

from functools import wraps
from flask import g, request
from typing import Any

from errors import ApiError


class User:
    """Base user class."""

    def __init__(self, user_id: str, scopes: set[str]):
        self.id = user_id
        self.scopes = scopes


class LocalUser(User):
    """Local development user (no authentication)."""

    def __init__(self):
        super().__init__("local", {"*"})


def resolve_user(req) -> User:
    """Resolve the current user from the request.

    Stub admin (LocalUser with full scope) is reachable only when APP_ENV is
    explicitly ``dev``. Future: JWT/session verification returning real users.
    """
    import os
    if os.getenv("APP_ENV") != "dev":
        raise RuntimeError(
            "stub admin user is only available when APP_ENV is explicitly dev"
        )
    return LocalUser()


def before_request_hook():
    """Set g.user on every request."""
    g.user = resolve_user(request)


def require_user(f):
    """Decorator to require authentication. Fail closed. Does not invent a user."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user = getattr(g, "user", None)
        if user is None or not getattr(user, "id", None):
            raise ApiError(
                code="unauthenticated",
                message="Authentication required",
                status=401,
            )
        return f(*args, **kwargs)
    return decorated


def validate_auth_for_startup():
    """Validate auth configuration at startup.
    
    Refuse to start when APP_ENV != dev until real auth exists.
    """
    import os
    app_env = os.getenv("APP_ENV")
    if app_env != "dev":
        raise ValueError(
            f"APP_ENV={app_env} requires real authentication. "
            "Stub auth (resolve_user always returns LocalUser with admin scope) "
            "is only allowed when APP_ENV is explicitly dev. "
            "Implement proper auth before deploying to non-dev environments."
        )
