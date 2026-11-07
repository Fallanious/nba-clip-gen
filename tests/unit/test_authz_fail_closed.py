"""Fail-closed auth helpers. Does not invent a current-user identity."""

import sys

import pytest

sys.path.insert(0, "src")

from auth import User, require_user
from authz import authorize_clip, authorize_play, authorize_player
from errors import ApiError


def test_authorize_none_user_401():
    with pytest.raises(ApiError) as ei:
        authorize_play(None, {"id": 1})
    assert ei.value.status == 401
    assert ei.value.code == "unauthenticated"


def test_authorize_empty_id_401():
    with pytest.raises(ApiError) as ei:
        authorize_player(User("", {"*"}), {"id": 1})
    assert ei.value.status == 401


def test_authorize_empty_scopes_403():
    with pytest.raises(ApiError) as ei:
        authorize_clip(User("someone", set()), {"id": 1})
    assert ei.value.status == 403


def test_authorize_missing_resource_404_after_auth():
    with pytest.raises(ApiError) as ei:
        authorize_play(User("someone", {"read"}), None)
    assert ei.value.status == 404
    assert ei.value.code == "not_found"


def test_authorize_does_not_invent_ownership():
    user = User("someone", {"read"})
    authorize_play(user, {"id": 99})
    authorize_player(user, {"id": 1})


def test_require_user_401_without_g_user(monkeypatch):
    from flask import Flask, g

    app = Flask(__name__)

    @app.route("/probe")
    @require_user
    def probe():
        return "ok"

    with app.test_request_context("/probe"):
        g.pop("user", None)
        with pytest.raises(ApiError) as ei:
            probe()
        assert ei.value.status == 401


def test_create_app_does_not_wire_new_generation_or_playback(monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CURSOR_SECRET", "k" * 32)
    import importlib
    import app as app_module
    importlib.reload(app_module)
    application = app_module.create_app()
    rules = {rule.rule for rule in application.url_map.iter_rules()}
    joined = " ".join(sorted(rules))
    assert "/api/plays" not in joined
    assert "playback-url" not in joined
    assert "clip-jobs" not in joined
    assert "/api/generate" in joined
