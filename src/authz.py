"""Authorization checks for resources."""

from typing import Any
from errors import ApiError


def is_admin(user: Any) -> bool:
    scopes = getattr(user, "scopes", None) or set()
    return "*" in scopes or "admin" in scopes


def require_admin(user: Any) -> None:
    """status=all and other unpublished views require admin scope."""
    if not is_admin(user):
        raise ApiError(
            code="forbidden",
            message="Administrator access required",
            status=403,
        )


def authorize_player(user: Any, player: Any) -> None:
    """Check if user can access player.
    
    Currently a no-op. Future: per-org scoping.
    Would raise ApiError with code='forbidden', status=403.
    """
    pass


def authorize_clip(user: Any, clip: Any) -> None:
    """Check if user can access clip.
    
    Currently a no-op. Future: per-org scoping.
    Would raise ApiError with code='forbidden', status=403.
    """
    pass
