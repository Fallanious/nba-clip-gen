"""Fail-closed authorization. Does not invent current-user identity or ownership."""

from typing import Any

from errors import ApiError


def _require_authenticated(user: Any) -> None:
    if user is None:
        raise ApiError(
            code="unauthenticated",
            message="Authentication required",
            status=401,
        )
    user_id = getattr(user, "id", None)
    if not isinstance(user_id, str) or not user_id.strip():
        raise ApiError(
            code="unauthenticated",
            message="Authentication required",
            status=401,
        )
    scopes = getattr(user, "scopes", None)
    if not scopes:
        raise ApiError(
            code="forbidden",
            message="Insufficient scope",
            status=403,
        )


def authorize_player(user: Any, player: Any) -> None:
    """Require an authenticated caller. No owner mapping exists yet.

    Missing resource: 404. Missing/invalid user: 401. Empty scopes: 403.
    """
    _require_authenticated(user)
    if player is None:
        raise ApiError(code="not_found", message="Not found", status=404)


def authorize_play(user: Any, play: Any) -> None:
    _require_authenticated(user)
    if play is None:
        raise ApiError(code="not_found", message="Not found", status=404)


def authorize_clip(user: Any, clip: Any) -> None:
    authorize_play(user, clip)
