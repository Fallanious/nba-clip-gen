"""Authentication and authorization seam."""

from functools import wraps
from flask import g, request
from typing import Optional, Any


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
    
    Currently returns a local user with full access.
    Future: JWT/session verification returning real users.
    """
    return LocalUser()


def before_request_hook():
    """Set g.user on every request."""
    g.user = resolve_user(request)


def require_user(f):
    """Decorator to require authentication (no-op for now)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not hasattr(g, 'user') or g.user is None:
            # In the future, return 401 here
            pass
        return f(*args, **kwargs)
    return decorated


def validate_auth_for_startup():
    """Validate auth configuration at startup.
    
    Refuse to start when APP_ENV != dev until real auth exists.
    """
    import os
    app_env = os.getenv("APP_ENV", "dev")
    if app_env != "dev":
        raise ValueError(
            f"APP_ENV={app_env} requires real authentication. "
            "Stub auth (resolve_user always returns LocalUser with admin scope) is only allowed in dev. "
            "Implement proper auth before deploying to non-dev environments."
        )
