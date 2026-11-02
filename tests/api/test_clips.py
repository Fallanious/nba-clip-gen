"""GET /api/players/:id/clips, GET /api/clips/:id, GET /api/clips/:id/playback-url (spec §4-6)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from sqlalchemy.exc import OperationalError
from tests.conftest import (assert_error, b64url, tamper_cursor, walk_pages, CLIPS, NOT_READY, PLAYABLE_CLIP,
                      NOT_READY_WITH_GAME_KEY, NOT_READY_NO_GAME_KEY)

URL = "/api/players/{}/clips"
PLAYABLE_201 = {90977, 90412, 90500, 90600, 88102, 88103, 70001, 70002}
ALL_201 = PLAYABLE_201 | {88104, 88105, 70003}


def play_ids(body):
    return [int(p["id"]) for g in body["games"] for p in g["plays"]]


def get(client, qs="", pid=201):
    return client.get(URL.format(pid) + ("?" + qs if qs else ""))


# ================= /api/players/:id/clips =================
def test_clips_shape(client):
    r = get(client)
    assert r.status_code == 200 and r.headers["Cache-Control"] == "private, max-age=300" and r.headers.get("ETag")
    b = r.get_json()
    assert b["player_id"] == "201" and b["limit"] == 50 and b["item_count"] == len(play_ids(b))
    for g in b["games"]:
        assert set(g["game"]) == {"id", "bbref_id", "date", "season", "home_abbr", "away_abbr"}
        assert isinstance(g["continues"], bool)
        for p in g["plays"]:
            assert isinstance(p["id"], str) and "s3_key" not in p
            assert p["playable"] is (p["status"] == "uploaded")


def test_default_status_playable_only(client):
    assert set(play_ids(get(client).get_json())) == PLAYABLE_201


def test_status_all_includes_pending_failed(client):
    b = get(client, "status=all").get_json()
    assert set(play_ids(b)) == ALL_201
    assert {p["playable"] for g in b["games"] for p in g["plays"]} == {True, False}


def test_display_order_games_and_plays(client):
    b = get(client).get_json()
    keys = [(g["game"]["date"], int(g["game"]["id"])) for g in b["games"]]
    assert keys == sorted(keys, reverse=True)
    for g in b["games"]:
        k = [(p["period"], p["play_index"]) for p in g["plays"]]
        assert k == sorted(k)
    # clip ids don't follow play order: game 77 must show 90977(p1,#38) before 90600? no: 90600 is p1,#5
    g77 = next(g for g in b["games"] if g["game"]["id"] == "77")
    assert [p["id"] for p in g77["plays"]] == ["90600", "90977", "90500", "90412"]


def test_game_clock_format(client):
    p = get(client).get_json()["games"][0]["plays"][0]
    assert p["game_clock"] == "PT4M12S" and p["game_clock_display"] == "4:12"


def test_multi_role_dedup_and_aggregation(client):
    b = get(client).get_json()
    ids = play_ids(b)
    assert ids.count(90500) == 1
    p = next(p for g in b["games"] for p in g["plays"] if p["id"] == "90500")
    assert sorted(p["roles"]) == ["foul", "rebound"]


def test_role_filter_lists_only_matching_roles(client):
    b = get(client, "role=foul").get_json()
    assert play_ids(b) == [90500]
    assert b["games"][0]["plays"][0]["roles"] == ["foul"]


@pytest.mark.parametrize("qs", ["role=shot&role=block", "role=shot,block", "role=shot,block,shot"])
def test_role_multi_syntaxes(client, qs):
    assert set(play_ids(get(client, qs).get_json())) == {90977, 90412, 90600, 88103, 70001}


@pytest.mark.parametrize("qs,expected", [
    ("season=2024-25", {70001, 70002}),
    ("action_type=rebound", {90500, 88102}),
    ("action_type=steal,block", {70002, 90600}),
    ("shot_subtype=dunk", {90977}),
    ("shot_subtype=hook&shot_subtype=midrange", {70001, 90412}),
    ("outcome=missed", {88103}),
    ("game_id=61", {88102, 88103}),
    ("game_id=123456", set()),
    ("date_from=2026-01-01", {90977, 90412, 90500, 90600, 88102, 88103}),
    ("date_to=2026-01-03", {88102, 88103, 70001, 70002}),
    ("date_from=2026-01-03&date_to=2026-01-03", {88102, 88103}),
])
def test_single_filters(client, qs, expected):
    assert set(play_ids(get(client, qs).get_json())) == expected


@pytest.mark.parametrize("qs,expected", [
    ("season=2025-26&action_type=shot&outcome=made", {90977, 90412}),
    ("role=shot&shot_subtype=midrange,hook&date_to=2026-12-31", {90412, 70001}),
    ("status=all&outcome=made&season=2024-25", {70001}),
    ("status=all&game_id=61&action_type=turnover", {88104}),
    ("role=steal&season=2025-26", set()),
])
def test_combined_filters(client, qs, expected):
    assert set(play_ids(get(client, qs).get_json())) == expected


def test_empty_list_existing_player_200(client):
    b = get(client, pid=208).get_json()
    assert b["games"] == [] and b["item_count"] == 0 and b["next_cursor"] is None


@pytest.mark.parametrize("qs,param", [
    ("role=dunk", "role"), ("role=shot,bogus", "role"), ("role=", "role"),
    ("season=2025", "season"), ("season=25-26", "season"), ("season=2025-2026", "season"),
    ("action_type=jumpball", "action_type"), ("shot_subtype=alleyoop", "shot_subtype"),
    ("outcome=blocked", "outcome"), ("outcome=made,missed", "outcome"),
    ("game_id=0", "game_id"), ("game_id=-5", "game_id"), ("game_id=abc", "game_id"),
    ("date_from=2026-13-01", "date_from"), ("date_to=04/24/2026", "date_to"), ("date_from=2026-02-30", "date_from"),
    ("status=uploaded", "status"), ("limit=0", "limit"), ("limit=201", "limit"), ("limit=x", "limit"),
])
def test_invalid_params_400(client, qs, param):
    assert_error(get(client, qs), 400, "invalid_param", param)


def test_date_range_inverted_422(client):
    assert_error(get(client, "date_from=2026-04-25&date_to=2026-04-24"), 422, "invalid_range")


@pytest.mark.parametrize("limit", [1, 200])
def test_limit_bounds_ok(client, limit):
    assert get(client, f"limit={limit}").get_json()["limit"] == limit


@pytest.mark.parametrize("pid", ["999999", "abc", "0", "-3"])
def test_unknown_player_404(client, pid):
    assert_error(client.get(URL.format(pid)), 404, "not_found")


def test_unknown_player_404_beats_param_errors_or_documented(client):
    # Q-6: precedence of 404 vs 400 is unspecified; we accept either but it must be one of them.
    assert client.get(URL.format(999999) + "?limit=0").status_code in (400, 404)


@pytest.mark.parametrize("limit", [1, 2, 3, 7, 50])
def test_paging_no_dupes_no_gaps_bulk(client, limit):
    full = play_ids(get(client, "limit=200", pid=209).get_json())
    assert len(full) == 131  # 130 bulk + 90412 (assist)
    pages = walk_pages(client, URL.format(209) + f"?limit={limit}", play_ids)
    flat = [x for p in pages for x in p]
    assert sorted(flat) == sorted(full) and len(set(flat)) == len(flat)
    assert all(len(p) == limit for p in pages[:-1])


def test_paging_keyset_order_across_pages(client):
    pages = walk_pages(client, URL.format(209) + "?limit=10", play_ids)
    # within a page display order differs, but page sets must be contiguous in (date desc, id desc)
    key = lambda cid: (CLIPS[cid]["game_date"], cid)
    for a, b in zip(pages, pages[1:]):
        assert min(map(key, a)) > max(map(key, b))


def test_continues_flag_when_game_straddles(client):
    b = get(client, "limit=1").get_json()  # game 77 has 4 playable clips for 201
    assert b["next_cursor"] and b["games"][-1]["continues"] is True
    last = get(client, "limit=200").get_json()
    assert last["next_cursor"] is None and all(g["continues"] is False for g in last["games"])


def test_continues_false_at_game_boundary(client):
    b = get(client, "limit=4").get_json()  # exactly game 77's 4 clips; next row is game 61
    assert [g["game"]["id"] for g in b["games"]] == ["77"] and b["games"][0]["continues"] is False


def test_client_merge_reconstructs_full_order(client):
    merged = {}
    for page in walk_pages(client, URL.format(201) + "?limit=3", lambda b: b["games"]):
        for g in page:
            merged.setdefault(g["game"]["id"], []).extend(g["plays"])
    full = {g["game"]["id"]: g["plays"] for g in get(client, "limit=200").get_json()["games"]}
    for gid, plays in merged.items():
        plays.sort(key=lambda p: (p["period"], p["play_index"]))
        assert [p["id"] for p in plays] == [p["id"] for p in full[gid]]


def test_same_date_two_games_ordering(client):
    # game 77 & 51 share 2026-04-24; player 202 only in 51. Ensure no crash & id DESC grouping rule
    b = get(client, "status=all", pid=202).get_json()
    assert [g["game"]["id"] for g in b["games"]] == ["51"]


@pytest.mark.parametrize("change", ["role=shot", "season=2025-26", "status=all", "outcome=made",
                                    "action_type=shot", "shot_subtype=layup", "game_id=77",
                                    "date_from=2020-01-01", "date_to=2030-01-01"])
def test_cursor_filter_hash_mismatch(client, change):
    c = get(client, "limit=2", pid=209).get_json()["next_cursor"]
    assert_error(get(client, f"limit=2&{change}&cursor={c}", pid=209), 400, "invalid_cursor")


def test_cursor_filter_hash_normalized(client):
    # role=shot,assist vs role=assist&role=shot (deduped/sorted) must hash identically
    c = get(client, "limit=1&role=shot,assist").get_json()["next_cursor"]
    assert get(client, f"limit=1&role=assist&role=shot&cursor={c}").status_code == 200


def test_cursor_from_other_player_rejected(client):
    # Q-5: spec hashes filters; player id should be part of the hash
    c = get(client, "limit=2", pid=209).get_json()["next_cursor"]
    assert_error(get(client, f"limit=2&cursor={c}", pid=201), 400, "invalid_cursor")


def test_cursor_from_players_endpoint_rejected(client):
    c = client.get("/api/players?limit=2").get_json()["next_cursor"]
    assert_error(get(client, f"cursor={c}"), 400, "invalid_cursor")


@pytest.mark.parametrize("bad", ["zzz", "e30", "e30.0000000000000000"])
def test_cursor_malformed(client, bad):
    assert_error(get(client, f"cursor={bad}"), 400, "invalid_cursor")


def test_cursor_tampered(client):
    c = get(client, "limit=2", pid=209).get_json()["next_cursor"]
    assert_error(get(client, f"limit=2&cursor={tamper_cursor(c)}", pid=209), 400, "invalid_cursor")


def test_db_params_types(client, fake_db):
    get(client, "role=shot,shot&action_type=&date_from=2026-01-01")
    key, p = [c for c in fake_db.calls if c[0] == "clips/by_player"][-1]
    assert p["roles"] == ["shot"]                   # deduped
    assert p.get("action_types") in (None,)         # empty list omitted (dynamic filters)
    assert str(p["date_from"]) == "2026-01-01"
    assert p.get("status_all") in (False, None)


def test_forged_cursor_rejected(client):
    payload = b64url({"v": 1, "k": ["2026-01-01", 1], "f": "deadbeefdeadbeef", "iat": 1, "kv": 1})
    assert_error(get(client, f"cursor={payload}.{'ab'*32}"), 400, "invalid_cursor")


def test_expired_clip_cursor_rejected(client, monkeypatch):
    c = get(client, "limit=2", pid=209).get_json()["next_cursor"]
    import time
    now = time.time()
    monkeypatch.setenv("CURSOR_MAX_AGE_S", "1")
    monkeypatch.setattr("pagination.time.time", lambda: now + 10)
    assert_error(get(client, f"limit=2&cursor={c}", pid=209), 400, "invalid_cursor")


def test_status_all_forbidden_for_non_admin(fake_db, monkeypatch):
    from auth import User
    from tests.conftest import _make_app
    monkeypatch.setattr("auth.resolve_user", lambda req: User("viewer", set()))
    r = _make_app().test_client().get("/api/players/201/clips?status=all")
    assert_error(r, 403, "forbidden")


# ================= /api/clips/:id =================
def test_clip_detail(client):
    r = client.get(f"/api/clips/{PLAYABLE_CLIP}")
    assert r.status_code == 200 and r.headers["Cache-Control"] == "private, max-age=300"
    b = r.get_json()
    assert b["id"] == "90412" and "s3_key" not in b and "s3_key" not in r.get_data(as_text=True)
    assert b["team"] == {"id": "20", "abbr": "PHI"}
    assert {(p["player_id"], p["role"]) for p in b["players"]} == {("201", "shot"), ("209", "assist")}
    assert b["playable"] is True and b["fallback_available"] is False
    assert {"video_start_s", "video_end_s", "attributes", "play_index"} <= set(b)


@pytest.mark.parametrize("allow,cid,expected", [
    ("true", NOT_READY_WITH_GAME_KEY, True), ("false", NOT_READY_WITH_GAME_KEY, False),
    ("true", NOT_READY_NO_GAME_KEY, False)])
def test_clip_detail_fallback_available(client, monkeypatch, allow, cid, expected):
    monkeypatch.setenv("PLAYBACK_ALLOW_FALLBACK", allow)
    from tests.conftest import _make_app
    b = _make_app().test_client().get(f"/api/clips/{cid}").get_json()
    assert b["playable"] is False and b["fallback_available"] is expected


@pytest.mark.parametrize("cid", ["1", "abc", "0", "-1", "99999999999999999999"])
def test_clip_detail_404(client, cid):
    assert_error(client.get(f"/api/clips/{cid}"), 404, "not_found")


def test_clip_detail_503(client, fake_db):
    fake_db.fail_with = OperationalError("x", {}, Exception("cold start"))
    r = client.get(f"/api/clips/{PLAYABLE_CLIP}")
    assert_error(r, 503, "db_unavailable") and r.headers["Retry-After"] == "2"


# ================= /api/clips/:id/playback-url =================
PB = "/api/clips/{}/playback-url"


def assert_no_store(r):
    assert r.headers["Cache-Control"] == "no-store" and r.headers.get("Pragma") == "no-cache"


def test_playback_503_when_unconfigured(client):
    """Env unset (base_env strips S3_BUCKET/AWS_REGION/creds) -> 503, never a 500 or a real AWS call."""
    r = client.get(PB.format(PLAYABLE_CLIP))
    err = assert_error(r, 503, "storage_unavailable")  # Q-1: code name to confirm
    assert "qa-fake" not in r.get_data(as_text=True)


def test_playback_unconfigured_still_404_for_missing_clip(client):
    assert client.get(PB.format(1)).status_code in (404, 503)  # Q-2: ordering of validation vs config check


def test_playback_clip_mode_success(playback_client, playback_configured):
    r = playback_client.get(PB.format(PLAYABLE_CLIP))
    assert r.status_code == 200, r.get_data(as_text=True)
    assert_no_store(r)
    b = r.get_json()
    assert b["clip_id"] == "90412" and b["mode"] == "clip" and b["mime"] == "video/mp4"
    assert b["ttl_s"] == 300 and b["expires_at"].endswith("Z") and "start_s" not in b
    op, params, ttl = playback_configured.calls[-1]
    assert op == "get_object" and ttl == 300
    assert params["Bucket"] == "qa-fake-bucket" and params["Key"] == CLIPS[90412]["s3_key"]
    assert params["ResponseContentType"] == "video/mp4"
    assert params["ResponseCacheControl"] == "private, max-age=300"


def test_playback_custom_ttl(client, monkeypatch, playback_configured):
    monkeypatch.setenv("PLAYBACK_URL_TTL", "600")
    from tests.conftest import _make_app
    b = _make_app().test_client().get(PB.format(PLAYABLE_CLIP)).get_json()
    assert b["ttl_s"] == 600


@pytest.mark.parametrize("ttl", ["59", "3601", "abc"])
def test_playback_ttl_out_of_range_fails_startup(monkeypatch, fake_db, playback_configured, ttl):
    monkeypatch.setenv("PLAYBACK_URL_TTL", ttl)
    from tests.conftest import _make_app
    with pytest.raises(Exception):
        _make_app()


def test_playback_fallback_mode(playback_client, playback_configured):
    r = playback_client.get(PB.format(NOT_READY_WITH_GAME_KEY))
    assert r.status_code == 200
    b = r.get_json()
    c = CLIPS[NOT_READY_WITH_GAME_KEY]
    assert b["mode"] == "fallback" and b["ttl_s"] == 900
    assert b["start_s"] == c["video_start_s"] and b["end_s"] == c["video_end_s"]
    assert playback_configured.calls[-1][1]["Key"] == "games/202504010PHI.mp4"


def test_fallback_presign_content_type_follows_key(playback_client, fake_db, playback_configured):
    """QA: Verify ResponseContentType and mime derive from the game key's extension."""
    # Patch a clip with .mkv game key
    fake_db.q_clips_playback_target = lambda p: [{
        "id": 500,
        "game_id": 1,
        "status": "pending",
        "s3_key": None,
        "game_key": "games/test.mkv",
        "video_start_s": 10.0,
        "video_end_s": 20.0
    }]
    
    r = playback_client.get(PB.format(500))
    
    assert r.status_code == 200
    b = r.get_json()
    
    # Verify mime type in response
    assert b["mime"] == "video/x-matroska"
    
    # Verify ResponseContentType in presigned URL params
    assert len(playback_configured.calls) == 1
    assert playback_configured.calls[0][1]["ResponseContentType"] == "video/x-matroska"


@pytest.mark.parametrize("status", ["pending", "failed", "rendered"])
def test_playback_409_fallback_disabled(client, monkeypatch, playback_configured, status):
    monkeypatch.setenv("PLAYBACK_ALLOW_FALLBACK", "false")
    from tests.conftest import _make_app
    r = _make_app().test_client().get(PB.format(NOT_READY[status]))
    err = assert_error(r, 409, "clip_not_ready")
    assert err["details"] == {"status": status, "fallback_available": False}


def test_playback_409_no_game_key(playback_client, playback_configured):
    err = assert_error(playback_client.get(PB.format(NOT_READY_NO_GAME_KEY)), 409, "clip_not_ready")
    assert err["details"]["status"] == "pending"


def test_playback_mode_clip_on_unrendered_409(playback_client, playback_configured):
    assert_error(playback_client.get(PB.format(NOT_READY_WITH_GAME_KEY) + "?mode=clip"), 409, "clip_not_ready")


def test_playback_mode_fallback_on_uploaded(playback_client, playback_configured):
    b = playback_client.get(PB.format(PLAYABLE_CLIP) + "?mode=fallback").get_json()
    assert b["mode"] == "fallback"  # game 77 has s3_key


def test_playback_mode_invalid(playback_client, playback_configured):
    assert_error(playback_client.get(PB.format(PLAYABLE_CLIP) + "?mode=stream"), 400, "invalid_param", "mode")


@pytest.mark.parametrize("cid", ["1", "abc", "0"])
def test_playback_404(playback_client, playback_configured, cid):
    assert_error(playback_client.get(PB.format(cid)), 404, "not_found")
    assert playback_configured.calls == []


def _client_error(code):
    from botocore.exceptions import ClientError
    return ClientError({"Error": {"Code": code, "Message": "x"}}, "HeadObject")


def _verify_client(monkeypatch):
    monkeypatch.setenv("PLAYBACK_VERIFY_OBJECT", "true")
    from tests.conftest import _make_app
    return _make_app().test_client()


@pytest.mark.parametrize("code", ["NoSuchKey", "404"])
def test_playback_media_missing_404(monkeypatch, fake_db, playback_configured, code):
    playback_configured.head_exc = _client_error(code)
    assert_error(_verify_client(monkeypatch).get(PB.format(PLAYABLE_CLIP)), 404, "media_missing")


@pytest.mark.parametrize("code", ["AccessDenied", "500", "SlowDown"])
def test_playback_storage_error_502(monkeypatch, fake_db, playback_configured, code):
    playback_configured.head_exc = _client_error(code)
    r = _verify_client(monkeypatch).get(PB.format(PLAYABLE_CLIP))
    assert_error(r, 502, "storage_error")


def test_playback_head_timeout_502(monkeypatch, fake_db, playback_configured):
    from botocore.exceptions import ReadTimeoutError
    playback_configured.head_exc = ReadTimeoutError(endpoint_url="https://s3")
    assert_error(_verify_client(monkeypatch).get(PB.format(PLAYABLE_CLIP)), 502, "storage_error")


def test_playback_no_credentials_503(playback_client, playback_configured):
    from botocore.exceptions import NoCredentialsError
    playback_configured.sign_exc = NoCredentialsError()
    assert_error(playback_client.get(PB.format(PLAYABLE_CLIP)), 503, "storage_unavailable")


def test_playback_verify_head_called_with_key(monkeypatch, fake_db, playback_configured):
    assert _verify_client(monkeypatch).get(PB.format(PLAYABLE_CLIP)).status_code == 200
    assert playback_configured.head_calls == [("qa-fake-bucket", CLIPS[90412]["s3_key"])]


def test_playback_url_not_logged(playback_client, playback_configured, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    playback_client.get(PB.format(PLAYABLE_CLIP))
    assert "X-Amz-Signature" not in caplog.text


def test_playback_db_503(fake_db, playback_client, playback_configured):
    fake_db.fail_with = OperationalError("x", {}, Exception("timeout"))
    assert_error(playback_client.get(PB.format(PLAYABLE_CLIP)), 503, "db_unavailable")


def test_playback_authorize_called_before_sign(playback_client, monkeypatch, playback_configured):
    import routes.players as authz
    seen = []
    orig = authz.authorize_clip
    monkeypatch.setattr(authz, "authorize_clip", lambda u, c: (seen.append(len(playback_configured.calls)), orig(u, c))[1])
    playback_client.get(PB.format(PLAYABLE_CLIP))
    assert seen == [0]


def test_allow_fallback_off_in_production_by_default(monkeypatch):
    from services.playback import _allow_fallback
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("PLAYBACK_ALLOW_FALLBACK", raising=False)
    assert _allow_fallback() is False
    monkeypatch.setenv("PLAYBACK_ALLOW_FALLBACK", "true")
    assert _allow_fallback() is True


def test_playback_botocore_error_is_502(monkeypatch, fake_db, playback_configured):
    from botocore.exceptions import BotoCoreError
    playback_configured.sign_exc = BotoCoreError()
    from tests.conftest import _make_app
    r = _make_app().test_client().get(PB.format(PLAYABLE_CLIP))
    assert_error(r, 502, "storage_error")


def test_clip_mode_mime_follows_mp4_key(playback_client, playback_configured):
    r = playback_client.get(PB.format(PLAYABLE_CLIP))
    assert r.status_code == 200
    assert r.get_json()["mime"] == "video/mp4"
    assert playback_configured.calls[-1][1]["ResponseContentType"] == "video/mp4"


# ================= integration =================
@pytest.mark.integration
def test_int_clip_paging_real_db(integration_client):
    pl = integration_client.get("/api/players?limit=1").get_json()["items"]
    if not pl:
        pytest.skip("no seed data on branch")
    url = URL.format(pl[0]["id"]) + "?limit=5&status=all"
    flat = [x for p in walk_pages(integration_client, url, play_ids, max_pages=500) for x in p]
    assert len(flat) == len(set(flat))


@pytest.mark.integration
def test_int_playback_unconfigured_503(integration_client):
    any_clip = integration_client.get("/api/players?limit=1").get_json()["items"]
    if not any_clip:
        pytest.skip("no seed data")
    games = integration_client.get(URL.format(any_clip[0]["id"]) + "?status=all&limit=1").get_json()["games"]
    if not games:
        pytest.skip("no clips")
    r = integration_client.get(PB.format(games[0]["plays"][0]["id"]))
    assert r.status_code in (503, 409)
