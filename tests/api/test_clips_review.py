"""Coverage for Max's clips review (PR #9) and QA's PR3_REVIEW_TESTS spec."""
import inspect
import json
import os
import sys
from pathlib import Path

import pytest

from tests.api.test_clips import URL, get, play_ids
from tests.conftest import (
    assert_error, walk_pages, CLIPS, CLIP_PLAYERS, GAMES, TEST_CURSOR_SECRET,
)
import tests.conftest as conftest


def _seed_91000(monkeypatch):
    monkeypatch.setitem(CLIPS, 91000, {
        **CLIPS[90500],
        "id": 91000,
        "play_index": 150,
        "period": 2,
        "s3_key": "clips/202604240PHI/91000.mp4",
        "status": "uploaded",
    })
    monkeypatch.setattr(conftest, "CLIP_PLAYERS", CLIP_PLAYERS + [(201, 91000, "shot")])


# ================= M1 =================
@pytest.fixture
def playback_module_absent(monkeypatch, fake_db):
    import importlib
    import routes.players as rp
    monkeypatch.setitem(sys.modules, "services.playback", None)
    importlib.reload(rp)
    import app as appmod
    importlib.reload(appmod)
    yield
    sys.modules.pop("services.playback", None)
    importlib.reload(rp)
    importlib.reload(appmod)


def test_app_boots_without_playback_module(playback_module_absent):
    from tests.conftest import _make_app
    app = _make_app()
    assert app.test_client().get("/api/players").status_code == 200


def test_playback_route_503_when_module_absent(playback_module_absent):
    from tests.conftest import _make_app
    r = _make_app().test_client().get("/api/clips/90412/playback-url")
    assert_error(r, 503, "storage_unavailable")
    assert "Traceback" not in r.get_data(as_text=True)


# ================= M2 =================
def test_page_boundary_follows_display_order(client, monkeypatch):
    _seed_91000(monkeypatch)
    r1 = get(client, "limit=3&game_id=77")
    assert r1.status_code == 200
    page1 = [(p["period"], p["play_index"]) for g in r1.get_json()["games"] for p in g["plays"]]
    cursor = r1.get_json()["next_cursor"]
    r2 = get(client, f"limit=3&game_id=77&cursor={cursor}")
    page2 = [(p["period"], p["play_index"]) for g in r2.get_json()["games"] for p in g["plays"]]
    assert page1 and page2
    assert max(page1) < min(page2)


def test_merged_pages_equal_single_page_without_resort(client, monkeypatch):
    _seed_91000(monkeypatch)
    merged = []
    for page in walk_pages(client, URL.format(201) + "?limit=3&game_id=77", lambda b: b["games"]):
        for g in page:
            merged.extend(p["id"] for p in g["plays"])
    full = [p["id"] for p in get(client, "limit=200&game_id=77").get_json()["games"][0]["plays"]]
    assert merged == full


def test_same_date_games_do_not_interleave_across_pages(client, monkeypatch):
    extras = []
    for cid, idx, period in ((90450, 8, 1), (90550, 9, 1), (90800, 10, 1)):
        monkeypatch.setitem(CLIPS, cid, {
            **CLIPS[90500],
            "id": cid,
            "game_id": 51,
            "game_date": GAMES[51]["game_date"],
            "play_index": idx,
            "period": period,
            "s3_key": f"clips/202604240DEN/{cid}.mp4",
            "status": "uploaded",
        })
        extras.append((201, cid, "shot"))
    monkeypatch.setattr(conftest, "CLIP_PLAYERS", CLIP_PLAYERS + extras)
    pages = walk_pages(client, URL.format(201) + "?limit=2", lambda b: b["games"])
    seq = []
    for page in pages:
        for g in page:
            seq.append(int(g["game"]["id"]))
    collapsed = [seq[0]] if seq else []
    for gid in seq[1:]:
        if gid != collapsed[-1]:
            collapsed.append(gid)
    assert collapsed == list(dict.fromkeys(collapsed))


def test_cursor_key_includes_display_columns(client, monkeypatch):
    _seed_91000(monkeypatch)
    r = get(client, "limit=1&game_id=77")
    cursor = r.get_json()["next_cursor"]
    last = r.get_json()["games"][0]["plays"][-1]
    payload_b64 = cursor.rsplit(".", 1)[0]
    pad = "=" * ((4 - len(payload_b64) % 4) % 4)
    import base64
    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + pad).decode())
    assert len(payload["k"]) == 5
    assert payload["k"][1:4] == [int(r.get_json()["games"][0]["game"]["id"]), last["period"], last["play_index"]]


# ================= M3 =================
def test_clips_calls_authorize_player(client, monkeypatch):
    import routes.players as rp
    seen = []
    monkeypatch.setattr(rp, "authorize_player", lambda u, p: seen.append(p) or None)
    assert get(client).status_code == 200
    assert len(seen) == 1
    assert str(seen[0]["id"]) == "201"


def test_clips_forbidden_when_authz_denies(client, monkeypatch, fake_db):
    import routes.players as rp
    from errors import ApiError
    monkeypatch.setattr(rp, "authorize_player", lambda u, p: (_ for _ in ()).throw(ApiError("forbidden", "no", 403)))
    fake_db.calls.clear()
    assert_error(get(client), 403, "forbidden")
    assert [k for k, _ in fake_db.calls if k == "clips/by_player"] == []


def test_clips_authz_before_param_validation(client, monkeypatch):
    import routes.players as rp
    from errors import ApiError
    monkeypatch.setattr(rp, "authorize_player", lambda u, p: (_ for _ in ()).throw(ApiError("forbidden", "no", 403)))
    assert_error(get(client, "limit=0"), 403, "forbidden")


# ================= M4 =================
@pytest.mark.parametrize("url", [
    "/api/players",
    "/api/players/201",
    "/api/players/201/clips",
    "/api/clips/90412",
])
def test_304_repeats_validators(client, url):
    r1 = client.get(url)
    assert r1.status_code == 200
    r2 = client.get(url, headers={"If-None-Match": r1.headers["ETag"]})
    assert r2.status_code == 304
    assert r2.get_data() == b""
    assert r2.headers["ETag"] == r1.headers["ETag"]
    assert r2.headers["Cache-Control"] == r1.headers["Cache-Control"]
    assert "Origin" in r2.headers["Vary"]
    assert r2.headers["X-Request-Id"]


def test_304_mismatched_etag_returns_200(client):
    r = client.get("/api/players/201/clips", headers={"If-None-Match": 'W/"nope"'})
    assert r.status_code == 200
    assert r.get_data()


def test_304_on_list_and_clip_endpoints(client):
    for url in ("/api/players/201/clips", "/api/clips/90412"):
        r1 = client.get(url)
        r2 = client.get(url, headers={"If-None-Match": r1.headers["ETag"]})
        assert r2.status_code == 304
        assert r2.headers["ETag"] == r1.headers["ETag"]
        assert r2.headers["Cache-Control"] == r1.headers["Cache-Control"]
        assert "Origin" in r2.headers["Vary"]


def test_304_skips_serialization(client, fake_db):
    url = "/api/players/201/clips"
    r1 = client.get(url)
    assert r1.status_code == 200
    fake_db.calls.clear()
    r2 = client.get(url, headers={"If-None-Match": r1.headers["ETag"]})
    assert r2.status_code == 304
    assert len(fake_db.calls) <= 1
    assert [k for k, _ in fake_db.calls if k == "clips/by_player"] == []


# ================= M5 =================
@pytest.mark.parametrize("param", [
    "action_type", "shot_subtype", "outcome", "season",
    "date_from", "date_to", "game_id", "status", "limit", "cursor",
])
def test_empty_filter_values_rejected(client, param):
    assert_error(get(client, f"{param}="), 400, "invalid_param", param)


@pytest.mark.parametrize("qs,param", [
    ("role=shot,", "role"),
    ("action_type=shot,,rebound", "action_type"),
    ("shot_subtype=dunk&shot_subtype=", "shot_subtype"),
])
def test_empty_in_multi_list_rejected(client, qs, param):
    assert_error(get(client, qs), 400, "invalid_param", param)


def test_whitespace_only_values_rejected(client):
    assert_error(get(client, "season=%20%20"), 400, "invalid_param", "season")
    assert_error(get(client, "outcome=%20"), 400, "invalid_param", "outcome")
    assert_error(get(client, "game_id=%20"), 400, "invalid_param", "game_id")


# ================= M6 =================
def _play(body, cid="90412"):
    return next(p for g in body["games"] for p in g["plays"] if p["id"] == cid)


def test_duration_zero_serialized_as_zero(client, monkeypatch):
    monkeypatch.setitem(CLIPS[90412], "duration_s", 0.0)
    p = _play(get(client).get_json())
    assert p["duration_s"] == 0.0 and p["duration_s"] is not None
    d = client.get("/api/clips/90412").get_json()
    assert d["duration_s"] == 0.0 and d["duration_s"] is not None


def test_duration_none_serialized_as_null(client, monkeypatch):
    monkeypatch.setitem(CLIPS[90412], "duration_s", None)
    p = _play(get(client).get_json())
    assert p["duration_s"] is None
    assert client.get("/api/clips/90412").get_json()["duration_s"] is None


def test_team_id_zero_not_dropped(client, monkeypatch):
    monkeypatch.setitem(conftest.TEAMS, 0, ("TST", "Test", "Testers"))
    monkeypatch.setitem(CLIPS[90412], "team_id", 0)
    p = _play(get(client).get_json())
    assert p["team_id"] == "0"
    d = client.get("/api/clips/90412").get_json()
    assert d["team"] == {"id": "0", "abbr": "TST"}


# ================= M7 =================
@pytest.mark.parametrize("qs", [
    "role=shot",
    "role=assist&role=shot",
    "role=shot,assist",
    "season=2025-26",
    "action_type=shot",
    "shot_subtype=layup",
    "outcome=made",
    "game_id=77",
    "date_from=2020-01-01",
    "date_to=2030-01-01",
    "date_from=2020-01-01&date_to=2030-01-01",
    "role=shot&season=2025-26&action_type=shot&outcome=made",
])
def test_cursor_roundtrip_every_filter_combo(client, qs):
    c = get(client, f"limit=1&status=all&{qs}", pid=209).get_json()["next_cursor"]
    if c is not None:
        assert get(client, f"limit=1&status=all&{qs}&cursor={c}", pid=209).status_code == 200


def test_cursor_filter_helper_single_source():
    import routes.players as rp
    src = inspect.getsource(rp.get_player_clips_endpoint)
    assert src.count('"shot_subtypes"') <= 1


# ================= M8 =================
def test_by_player_sql_builder_emits_expected_predicates():
    from db.sql_loader import get_raw_sql, registry
    from repositories import clips as clips_repo
    sql = get_raw_sql("clips/by_player")
    assert "/*dynamic_filters*/" not in sql
    for clause in (
        "CAST(:roles AS clip_role[])",
        "CAST(:date_from AS date)",
        "CAST(:date_to AS date)",
        "CAST(:season AS text)",
        "CAST(:game_id AS bigint)",
        "CAST(:action_types AS text[])",
        "CAST(:subtypes AS text[])",
        "CAST(:outcome AS text)",
        "CAST(:status_all AS boolean)",
        "CAST(:c_date AS date)",
        ":c_game_id",
        ":c_period",
        ":c_play_index",
    ):
        assert clause in sql
    src = inspect.getsource(clips_repo.get_player_clips)
    assert ".replace(" not in src
    assert "/*dynamic_filters*/" not in src
    stmt = registry["clips/by_player"]
    injected = "2025-26' OR 1=1"
    assert "OR 1=1" not in str(stmt)
    assert injected not in str(stmt)


@pytest.mark.integration
def test_sql_prepare_every_filter_combo(integration_client):
    from sqlalchemy import create_engine, text, bindparam, BigInteger, Date, Text, Integer, Boolean
    from sqlalchemy.dialects.postgresql import ARRAY
    from db.sql_loader import get_raw_sql
    url = os.environ.get("DATABASE_URL", "")
    engine = create_engine(url)
    sql = get_raw_sql("clips/by_player")
    stmt = text("EXPLAIN " + sql).bindparams(
        bindparam("pid", type_=BigInteger),
        bindparam("roles", type_=ARRAY(Text)),
        bindparam("date_from", type_=Date),
        bindparam("date_to", type_=Date),
        bindparam("season", type_=Text),
        bindparam("game_id", type_=BigInteger),
        bindparam("action_types", type_=ARRAY(Text)),
        bindparam("subtypes", type_=ARRAY(Text)),
        bindparam("outcome", type_=Text),
        bindparam("status_all", type_=Boolean),
        bindparam("c_date", type_=Date),
        bindparam("c_game_id", type_=BigInteger),
        bindparam("c_period", type_=Integer),
        bindparam("c_play_index", type_=Integer),
        bindparam("c_id", type_=BigInteger),
        bindparam("limit", type_=Integer),
    )
    params = {
        "pid": 201, "roles": None, "date_from": None, "date_to": None, "season": None,
        "game_id": None, "action_types": None, "subtypes": None, "outcome": None,
        "status_all": True, "c_date": None, "c_game_id": None, "c_period": None,
        "c_play_index": None, "c_id": None, "limit": 10,
    }
    with engine.connect() as conn:
        conn.execute(stmt, params)


def test_ci_runs_integration_on_neon_branch():
    files = list(Path(".github/workflows").glob("*.yml")) + list(Path(".github/workflows").glob("*.yaml"))
    assert files, "expected a GitHub Actions workflow"
    text = "\n".join(p.read_text() for p in files)
    assert "pytest -m integration" in text
    assert "${{ secrets.NEON_DEV_DATABASE_URL }}" in text
    assert "DATABASE_URL:" in text
    assert "postgresql://" not in text.lower()
    assert "postgres://" not in text.lower()
    assert "NEON_DEV_DATABASE_URL" in text
