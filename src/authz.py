"""Authorization checks for resources."""

from typing import Any


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
