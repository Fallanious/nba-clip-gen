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

    Stub admin (LocalUser with full scope) is reachable only when APP_ENV is
    explicitly ``dev``. Future: JWT/session verification returning real users.
    """
    from config import config
    if config.APP_ENV != "dev":
        raise RuntimeError(
            "stub admin user is only available when APP_ENV is explicitly dev"
        )
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
    Reads APP_ENV through config so validation rules stay in one place.
    """
    from config import config
    if config.APP_ENV != "dev":
        raise ValueError(
            "APP_ENV requires real authentication. "
            "Stub auth (resolve_user always returns LocalUser with admin scope) "
            "is only allowed when APP_ENV is explicitly dev. "
            "Implement proper auth before deploying to non-dev environments."
        )
