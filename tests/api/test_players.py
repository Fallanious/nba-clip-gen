"""GET /api/players and GET /api/players/:id (spec §1-3). Unit tests use FakeDB; integration marked."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json, pytest
from sqlalchemy.exc import OperationalError
from tests.conftest import assert_error, b64url, tamper_cursor, walk_pages, PLAYERS, CLIPS, NOT_READY

ids = lambda body: [i["id"] for i in body["items"]]


# ---------- shape / headers ----------
def test_list_shape_and_string_ids(client):
    r = client.get("/api/players")
    assert r.status_code == 200
    b = r.get_json()
    assert set(b) >= {"items", "next_cursor", "limit"} and b["limit"] == 25
    for it in b["items"]:
        assert isinstance(it["id"], str)
        assert set(it) >= {"id", "full_name", "first_name", "last_name", "position", "headshot_url", "is_active", "team"}
        if it["team"] is not None:
            assert isinstance(it["team"]["id"], str) and set(it["team"]) == {"id", "abbr", "city", "name"}


def test_team_null_serialized(client):
    b = client.get("/api/players?q=Bogdanovic").get_json()
    assert b["items"][0]["full_name"] == "Bogdan Bogdanović" and b["items"][0]["team"] is None


def test_headers_cache_etag_304(client):
    r = client.get("/api/players")
    assert r.headers["X-Request-Id"] and "Origin" in r.headers.get("Vary", "")
    assert r.headers["Cache-Control"] == "private, max-age=60"
    etag = r.headers["ETag"]
    assert etag.startswith('W/"')
    r2 = client.get("/api/players", headers={"If-None-Match": etag})
    assert r2.status_code == 304


def test_players_etag_changes_when_row_updated_at_changes(client):
    r1 = client.get("/api/players?limit=5")
    etag = r1.headers["ETag"]
    original = PLAYERS[0]["updated_at"]
    try:
        PLAYERS[0]["updated_at"] = original.replace(year=2099)
        r2 = client.get("/api/players?limit=5", headers={"If-None-Match": etag})
        assert r2.status_code == 200
        assert r2.headers["ETag"] != etag
    finally:
        PLAYERS[0]["updated_at"] = original


def test_unique_request_ids(client):
    assert client.get("/api/players").headers["X-Request-Id"] != client.get("/api/players").headers["X-Request-Id"]


# ---------- browse ordering & paging ----------
def test_browse_default_active_only_ordered(client):
    items = client.get("/api/players?limit=100").get_json()["items"]
    assert all(i["is_active"] for i in items)
    keys = [(i["last_name"], int(i["id"])) for i in items]
    assert keys == sorted(keys)


@pytest.mark.parametrize("limit", [1, 2, 5, 7])
def test_browse_paging_no_dupes_no_gaps(client, limit):
    full = [i["id"] for i in client.get("/api/players?limit=100&active=all").get_json()["items"]]
    pages = walk_pages(client, f"/api/players?limit={limit}&active=all", ids)
    flat = [x for p in pages for x in p]
    assert flat == full and len(set(flat)) == len(flat)
    assert all(len(p) == limit for p in pages[:-1]) and 1 <= len(pages[-1]) <= limit


def test_last_page_has_null_cursor_exact_multiple(client):
    n = len(client.get("/api/players?limit=100&team=PHI").get_json()["items"])
    b = client.get(f"/api/players?limit={n}&team=PHI").get_json()
    assert b["next_cursor"] is None  # lookahead row: no empty trailing page


def test_cursor_is_stable_and_opaque(client):
    a = client.get("/api/players?limit=3").get_json()["next_cursor"]
    b = client.get("/api/players?limit=3").get_json()["next_cursor"]
    assert a == b and "=" not in a and "." in a


# ---------- search ranking & accents ----------
@pytest.mark.parametrize("q,expected", [
    ("Jokic", "Nikola Jokić"), ("jokic", "Nikola Jokić"), ("JOKIĆ", "Nikola Jokić"),
    ("Doncic", "Luka Dončić"), ("luka doncic", "Luka Dončić"), ("Dončić", "Luka Dončić"),
    ("Embiid", "Joel Embiid"), ("  Embiid  ", "Joel Embiid"),
])
def test_search_accent_case_insensitive_top_hit(client, q, expected):
    b = client.get("/api/players", query_string={"q": q}).get_json()
    assert b["items"] and b["items"][0]["full_name"] == expected


def test_search_substring_path_matches_low_similarity(client):
    # 'Oubr' is a substring; trigram score may be < 0.2 but LIKE path must match
    names = [i["full_name"] for i in client.get("/api/players?q=Oubr").get_json()["items"]]
    assert "Kelly Oubre Jr." in names


def test_search_ranking_exact_beats_partial(client):
    names = [i["full_name"] for i in client.get("/api/players?q=Jalen Brunson&active=all").get_json()["items"]]
    assert names[0] == "Jalen Brunson"
    assert names.index("Jalen Brunson") < names.index("Jalen Smith")


def test_search_ties_broken_by_last_name_then_id(client):
    items = client.get("/api/players?q=Jalen Brunson&active=all").get_json()["items"]
    brunsons = [int(i["id"]) for i in items if i["full_name"] == "Jalen Brunson"]
    assert brunsons == sorted(brunsons) == [205, 211]


def test_search_no_results_is_200_empty(client):
    b = client.get("/api/players?q=Xqzvw").get_json()
    assert b["items"] == [] and b["next_cursor"] is None


def test_search_filters_apply_to_both_match_paths(client):
    # team/active filters must apply to the LIKE path too (parenthesized OR)
    assert client.get("/api/players?q=Oubr&team=DEN").get_json()["items"] == []
    assert client.get("/api/players?q=Carter").get_json()["items"] == []          # inactive excluded by default
    assert client.get("/api/players?q=Carter&active=false").get_json()["items"][0]["full_name"] == "Vince Carter"


@pytest.mark.parametrize("limit", [1, 2])
def test_search_paging_across_equal_scores(client, limit):
    full = ids(client.get("/api/players?q=Jalen&active=all&limit=100").get_json())
    flat = [x for p in walk_pages(client, f"/api/players?q=Jalen&active=all&limit={limit}", ids) for x in p]
    assert flat == full and len(flat) == len(set(flat)) and len(full) >= 4


def test_search_passes_trimmed_q_to_db(client, fake_db):
    client.get("/api/players", query_string={"q": "  Jokic "})
    key, params = [c for c in fake_db.calls if c[0] == "players/search"][-1]
    assert params["q"] == "Jokic"


def test_browse_used_when_q_empty(client, fake_db):
    assert client.get("/api/players?q=").status_code == 200
    assert fake_db.calls[-1][0] == "players/search_browse"


# ---------- q validation ----------
@pytest.mark.parametrize("q", ["a", " b ", "x" * 101])
def test_q_length_invalid(client, q):
    assert_error(client.get("/api/players", query_string={"q": q}), 400, "invalid_param", "q")


def test_q_bounds_ok(client):
    assert client.get("/api/players", query_string={"q": "Jo"}).status_code == 200
    assert client.get("/api/players", query_string={"q": "x" * 100}).status_code == 200


def test_q_sql_metacharacters_safe(client):
    for q in ["%%", "__", "'; DROP TABLE players;--", "O'Neal"]:
        assert client.get("/api/players", query_string={"q": q}).status_code == 200


# ---------- filters ----------
@pytest.mark.parametrize("team", ["PHI", "phi", "Phi"])
def test_team_filter_case_insensitive(client, team):
    items = client.get(f"/api/players?team={team}&limit=100").get_json()["items"]
    assert items and all(i["team"]["abbr"] == "PHI" for i in items)


@pytest.mark.parametrize("team", ["XXX", "PH", "PHIL", "P1I", ""])
def test_team_filter_invalid(client, team):
    assert_error(client.get(f"/api/players?team={team}"), 400, "invalid_param", "team")


@pytest.mark.parametrize("active,check", [
    ("true", lambda s: s == {True}), ("false", lambda s: s == {False}), ("all", lambda s: s == {True, False})])
def test_active_filter(client, active, check):
    items = client.get(f"/api/players?active={active}&limit=100").get_json()["items"]
    assert check({i["is_active"] for i in items})


@pytest.mark.parametrize("active", ["yes", "1", "TRUEISH", ""])
def test_active_invalid(client, active):
    assert_error(client.get(f"/api/players?active={active}"), 400, "invalid_param", "active")


def test_filters_combined(client):
    items = client.get("/api/players?q=Jalen&team=NYK&active=false").get_json()["items"]
    assert [i["id"] for i in items] == ["211"]


# ---------- limit ----------
@pytest.mark.parametrize("limit", ["0", "101", "-1", "abc", "1.5", ""])
def test_limit_out_of_range_no_clamp(client, limit):
    assert_error(client.get(f"/api/players?limit={limit}"), 400, "invalid_param", "limit")


@pytest.mark.parametrize("limit", [1, 100])
def test_limit_bounds_ok(client, limit):
    b = client.get(f"/api/players?limit={limit}&active=all").get_json()
    assert b["limit"] == limit and len(b["items"]) <= limit


# ---------- cursor errors ----------
def _cursor(client, qs="limit=2"):
    return client.get(f"/api/players?{qs}").get_json()["next_cursor"]


@pytest.mark.parametrize("bad", ["garbage", "!!!", b64url({"v": 1}), b64url({"v": 1, "k": ["A", "1"], "f": "x"}) + ".deadbeefdeadbeef", "a.b.c"])
def test_cursor_malformed(client, bad):
    assert_error(client.get(f"/api/players?cursor={bad}"), 400, "invalid_cursor")


def test_cursor_tampered_hmac(client):
    c = _cursor(client)
    assert_error(client.get(f"/api/players?limit=2&cursor={tamper_cursor(c)}"), 400, "invalid_cursor")


def test_cursor_wrong_version(client):
    payload, _, sig = _cursor(client).partition(".")
    import base64
    raw = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    raw["v"] = 2
    assert_error(client.get(f"/api/players?limit=2&cursor={b64url(raw)}.{sig}"), 400, "invalid_cursor")


@pytest.mark.parametrize("other", ["team=PHI", "active=all", "q=Jalen"])
def test_cursor_from_different_filters_rejected(client, other):
    c = _cursor(client, "limit=2")
    assert_error(client.get(f"/api/players?limit=2&{other}&cursor={c}"), 400, "invalid_cursor")


def test_cursor_survives_limit_change(client):
    # ASSUMPTION: limit is not part of the filter hash (Q-4 in plan). Adjust if DEV decides otherwise.
    c = _cursor(client, "limit=2")
    assert client.get(f"/api/players?limit=5&cursor={c}").status_code == 200


def test_cursor_secret_rotation_invalidates(client, app, monkeypatch):
    c = _cursor(client)
    monkeypatch.setenv("CURSOR_SECRET", "rotated-test-secret-32-bytes-ok!")
    from tests.conftest import _make_app
    fresh = _make_app().test_client()
    assert_error(fresh.get(f"/api/players?limit=2&cursor={c}"), 400, "invalid_cursor")


# ---------- detail ----------
def test_player_detail(client):
    r = client.get("/api/players/201")
    assert r.status_code == 200 and r.headers["Cache-Control"] == "private, max-age=300"
    b = r.get_json()
    assert b["id"] == "201" and b["bbref_id"] == "p201" and b["team"]["abbr"] == "PHI"
    assert b["clip_count"] == 11  # distinct clips incl. non-playable (spec: counts from clip_players)
    assert b["first_clip_date"] == "2025-04-01" and b["last_clip_date"] == "2026-04-24"
    assert b["role_counts"]["shot"] == 6 and b["role_counts"]["rebound"] == 2 and b["role_counts"]["foul"] == 1


def test_player_detail_no_clips(client):
    b = client.get("/api/players/208").get_json()
    assert b["clip_count"] == 0 and b["first_clip_date"] is None and b["role_counts"] == {}


@pytest.mark.parametrize("pid", ["999999", "abc", "0", "-1", "1.5", "9223372036854775808", "%20"])
def test_player_detail_404(client, pid):
    err = assert_error(client.get(f"/api/players/{pid}"), 404, "not_found")
    assert "invalid literal" not in err["message"]


# ---------- DB outage / internal ----------
def test_db_unavailable_503_retry_after(client, fake_db):
    fake_db.fail_with = OperationalError("SELECT 1", {}, Exception("connection timed out"))
    for url in ["/api/players", "/api/players/201", "/api/players?q=Jokic"]:
        r = client.get(url)
        assert_error(r, 503, "db_unavailable")
        assert r.headers.get("Retry-After") == "2"


def test_internal_500_no_stack(app, client, fake_db):
    app.config["PROPAGATE_EXCEPTIONS"] = False  # TESTING=True re-raises by default
    fake_db.fail_with = RuntimeError("boom secret-ish detail")
    r = client.get("/api/players")
    err = assert_error(r, 500, "internal")
    assert "boom" not in err["message"]


def test_auth_codes_never_returned_locally(client):
    for url in ["/api/players", "/api/players/201"]:
        assert client.get(url).status_code not in (401, 403)


def test_unknown_team_does_not_echo_input(client):
    r = client.get("/api/players?team=XXX")
    err = assert_error(r, 400, "invalid_param", "team")
    assert "XXX" not in err["message"]


def test_search_equal_score_cursor_is_stable(client):
    """Paging q=Jalen at limit=1 must not skip or repeat same-score ties."""
    url = "/api/players?q=Jalen&active=all&limit=1"
    pages = walk_pages(client, url, ids, max_pages=50)
    flat = [x for p in pages for x in p]
    assert len(flat) == len(set(flat))
    full = ids(client.get("/api/players?q=Jalen&active=all&limit=100").get_json())
    assert flat == full


def test_role_list_capped(client):
    qs = "&".join(["role=shot"] * 21)
    r = client.get(f"/api/players/201/clips?{qs}")
    err = assert_error(r, 400, "invalid_param", "role")
    assert "shot" not in err["message"] or "at most" in err["message"]


def test_invalid_role_does_not_echo_value(client):
    r = client.get("/api/players/201/clips?role=not-a-role")
    err = assert_error(r, 400, "invalid_param", "role")
    assert "not-a-role" not in err["message"]


def test_status_all_requires_admin(fake_db, monkeypatch):
    from auth import User
    from tests.conftest import _make_app
    monkeypatch.setattr("auth.resolve_user", lambda req: User("viewer", set()))
    r = _make_app().test_client().get("/api/players/201/clips?status=all")
    assert_error(r, 403, "forbidden")


def test_role_filter_keeps_all_roles_on_the_clip(client):
    r = client.get("/api/players/201/clips?role=rebound")
    assert r.status_code == 200
    plays = [p for g in r.get_json()["games"] for p in g["plays"]]
    hit = next(p for p in plays if p["id"] == "90500")
    assert hit["roles"] == ["foul", "rebound"]


def test_player_clips_paging_no_dupes(client):
    play_ids = lambda b: [p["id"] for g in b["games"] for p in g["plays"]]
    full = play_ids(client.get("/api/players/209/clips?limit=200").get_json())
    pages = walk_pages(client, "/api/players/209/clips?limit=7", play_ids)
    flat = [x for p in pages for x in p]
    assert flat == full and len(set(flat)) == len(flat)
    assert all(len(p) == 7 for p in pages[:-1])


def test_player_clips_same_date_games_ordered_and_continues(client):
    b = client.get("/api/players/209/clips?limit=200").get_json()
    keys = [(g["game"]["date"], int(g["game"]["id"])) for g in b["games"]]
    assert keys == sorted(keys, reverse=True)
    assert {"51", "77"} <= {g["game"]["id"] for g in b["games"]}
    first = client.get("/api/players/209/clips?limit=1").get_json()
    assert first["next_cursor"]
    assert isinstance(first["games"][0]["continues"], bool)


def test_clips_etag_changes_when_clip_status_changes(client):
    r1 = client.get("/api/players/201/clips")
    etag = r1.headers["ETag"]
    clip = CLIPS[88104]
    original_status, original_updated = clip["status"], clip["updated_at"]
    try:
        clip["status"] = "uploaded"
        clip["s3_key"] = "clips/now-playable.mp4"
        clip["updated_at"] = original_updated.replace(year=2099)
        r2 = client.get("/api/players/201/clips", headers={"If-None-Match": etag})
        assert r2.status_code == 200
        assert r2.headers["ETag"] != etag
    finally:
        clip["status"] = original_status
        clip["s3_key"] = None
        clip["updated_at"] = original_updated


@pytest.mark.parametrize("cid", list(NOT_READY.values()))
def test_unpublished_clip_detail_is_404_for_non_admin(fake_db, monkeypatch, cid):
    from auth import User
    from tests.conftest import _make_app
    monkeypatch.setattr("auth.resolve_user", lambda req: User("viewer", set()))
    r = _make_app().test_client().get(f"/api/clips/{cid}")
    err = assert_error(r, 404, "not_found")
    assert err["message"] == "Clip not found"


def _clip_404_signature(resp):
    """Status, JSON, and headers a non-admin can observe (stable request id)."""
    headers = [(k, v) for k, v in resp.headers.items() if k.lower() != "date"]
    return resp.status_code, resp.get_json(), headers


def test_unpublished_and_missing_clip_look_the_same_to_non_admin(fake_db, monkeypatch):
    from auth import User
    from tests.conftest import _make_app
    monkeypatch.setattr("auth.resolve_user", lambda req: User("viewer", set()))
    client = _make_app().test_client()
    rid = {"X-Request-Id": "qa-uniform-clip-404"}
    detail = [
        client.get("/api/clips/99999999", headers=rid),
        client.get(f"/api/clips/{NOT_READY['pending']}", headers=rid),
        client.get("/api/clips/abc", headers=rid),
        client.get("/api/clips/-1", headers=rid),
    ]
    first = _clip_404_signature(detail[0])
    assert first[0] == 404
    assert first[1] == {"error": {"code": "not_found", "message": "Clip not found"}}
    for resp in detail[1:]:
        assert _clip_404_signature(resp) == first

    playback = [
        client.get("/api/clips/99999999/playback-url", headers=rid),
        client.get(f"/api/clips/{NOT_READY['pending']}/playback-url", headers=rid),
        client.get("/api/clips/abc/playback-url", headers=rid),
        client.get("/api/clips/-1/playback-url", headers=rid),
    ]
    first_pb = _clip_404_signature(playback[0])
    assert first_pb[0] == 404
    assert first_pb[1] == {"error": {"code": "not_found", "message": "Clip not found"}}
    for resp in playback[1:]:
        assert _clip_404_signature(resp) == first_pb


def test_admin_can_get_unpublished_clip_detail(client):
    r = client.get(f"/api/clips/{NOT_READY['pending']}")
    assert r.status_code == 200
    assert r.get_json()["id"] == str(NOT_READY["pending"])


def test_unpublished_playback_is_404_for_non_admin(fake_db, monkeypatch):
    from auth import User
    from tests.conftest import _make_app
    monkeypatch.setattr("auth.resolve_user", lambda req: User("viewer", set()))
    r = _make_app().test_client().get(f"/api/clips/{NOT_READY['pending']}/playback-url")
    err = assert_error(r, 404, "not_found")
    assert err["message"] == "Clip not found"


@pytest.mark.parametrize("path", ["/api/clips/abc", "/api/clips/-1",
                                  "/api/clips/abc/playback-url", "/api/clips/-1/playback-url"])
def test_invalid_clip_id_is_404_clip_not_found(fake_db, monkeypatch, path):
    from auth import User
    from tests.conftest import _make_app
    monkeypatch.setattr("auth.resolve_user", lambda req: User("viewer", set()))
    r = _make_app().test_client().get(path)
    err = assert_error(r, 404, "not_found")
    assert err["message"] == "Clip not found"


def test_unpublished_clip_404s_before_authorize(fake_db, monkeypatch):
    from auth import User
    from errors import ApiError
    from tests.conftest import _make_app

    def forbid(_user, _clip):
        raise ApiError(code="forbidden", message="Administrator access required", status=403)

    monkeypatch.setattr("routes.players.authorize_clip", forbid)
    monkeypatch.setattr("auth.resolve_user", lambda req: User("viewer", set()))
    client = _make_app().test_client()
    for path in (f"/api/clips/{NOT_READY['pending']}",
                 f"/api/clips/{NOT_READY['pending']}/playback-url"):
        err = assert_error(client.get(path), 404, "not_found")
        assert err["message"] == "Clip not found"


def test_integration_client_requires_allow_flag(monkeypatch, request):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@ep-dev.example/db")
    monkeypatch.delenv("ALLOW_INTEGRATION_DB", raising=False)
    with pytest.raises(pytest.skip.Exception, match="ALLOW_INTEGRATION_DB"):
        request.getfixturevalue("integration_client")


def test_integration_client_fails_closed_without_endpoint_id(monkeypatch, request):
    monkeypatch.setenv("ALLOW_INTEGRATION_DB", "1")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://u:secret-pass@ep-dev-abc.us-east-1.aws.neon.tech/db?sslmode=require",
    )
    monkeypatch.delenv("NEON_DEV_ENDPOINT_ID", raising=False)
    with pytest.raises(pytest.fail.Exception, match="NEON_DEV_ENDPOINT_ID") as ei:
        request.getfixturevalue("integration_client")
    assert "secret-pass" not in str(ei.value)


def test_integration_client_fails_closed_on_mismatched_host(monkeypatch, request):
    monkeypatch.setenv("ALLOW_INTEGRATION_DB", "1")
    monkeypatch.setenv("NEON_DEV_ENDPOINT_ID", "ep-dev-abc")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://u:secret-pass@ep-other.us-east-1.aws.neon.tech/db?sslmode=require",
    )
    with pytest.raises(pytest.fail.Exception, match="NEON_DEV_ENDPOINT_ID") as ei:
        request.getfixturevalue("integration_client")
    assert "secret-pass" not in str(ei.value)


def test_integration_client_fails_closed_on_non_neon_host(monkeypatch, request):
    monkeypatch.setenv("ALLOW_INTEGRATION_DB", "1")
    monkeypatch.setenv("NEON_DEV_ENDPOINT_ID", "ep-dev-abc")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://u:secret-pass@evil.example/db?sslmode=require",
    )
    with pytest.raises(pytest.fail.Exception, match="not an accepted Neon host") as ei:
        request.getfixturevalue("integration_client")
    assert "secret-pass" not in str(ei.value)


def test_require_neon_integration_url_accepts_matching_host(monkeypatch):
    from tests.conftest import _require_neon_integration_url
    monkeypatch.setenv("NEON_DEV_ENDPOINT_ID", "ep-dev-abc")
    monkeypatch.delenv("NEON_DEV_REGION", raising=False)
    _require_neon_integration_url(
        "postgresql://u:p@ep-dev-abc.us-east-1.aws.neon.tech/db?sslmode=require"
    )
    _require_neon_integration_url(
        "postgresql://u:p@ep-dev-abc-pooler.us-east-1.aws.neon.tech/db?sslmode=require"
    )


_OK_NEON = "ep-dev-abc.us-east-1.aws.neon.tech"
_PW = "secret-pass"


def _assert_integration_url_rejected(monkeypatch, request, url, match):
    monkeypatch.setenv("ALLOW_INTEGRATION_DB", "1")
    monkeypatch.setenv("NEON_DEV_ENDPOINT_ID", "ep-dev-abc")
    monkeypatch.delenv("NEON_DEV_REGION", raising=False)
    monkeypatch.setenv("DATABASE_URL", url)
    with pytest.raises(pytest.fail.Exception, match=match) as ei:
        request.getfixturevalue("integration_client")
    assert _PW not in str(ei.value)


@pytest.mark.parametrize("url,match", [
    (
        f"postgresql://u:{_PW}@{_OK_NEON}/db?sslmode=require&hostaddr=1.2.3.4",
        "override parameters are not allowed",
    ),
    (
        f"host={_OK_NEON} hostaddr=1.2.3.4 user=u password={_PW} dbname=db sslmode=require",
        r"DATABASE_URL parameters are not allowed",
    ),
    (
        f"postgresql://u:{_PW}@{_OK_NEON}/db?sslmode=require&service=foo",
        "override parameters are not allowed",
    ),
    (
        f"postgresql://u:{_PW}@{_OK_NEON}/db?sslmode=require&options=endpoint%3Dep-prod",
        "override parameters are not allowed",
    ),
    (
        f"postgresql://u:{_PW}@{_OK_NEON}/db?sslmode=require&port=5433",
        "override parameters are not allowed",
    ),
    (
        f"postgresql://u:{_PW}@{_OK_NEON}/db?sslmode=require&user=admin",
        "override parameters are not allowed",
    ),
    (
        f"postgresql://u:{_PW}@{_OK_NEON}/db?sslmode=require&sslmode=disable",
        "query has duplicate keys",
    ),
    (
        f"mysql://u:{_PW}@{_OK_NEON}/db?sslmode=require",
        "scheme must be postgresql",
    ),
    (
        f"http://u:{_PW}@{_OK_NEON}/db?sslmode=require",
        "scheme must be postgresql",
    ),
    (
        f"postgresql://u:{_PW}@{_OK_NEON}/db?sslmode=disable",
        "sslmode must be require or verify-full",
    ),
    (
        f"postgresql://u:{_PW}@{_OK_NEON}/db",
        "sslmode must be require or verify-full",
    ),
])
def test_integration_client_fails_closed_on_guard_bypasses(monkeypatch, request, url, match):
    _assert_integration_url_rejected(monkeypatch, request, url, match)


def test_conftest_inline_guard_rejects_region_mismatch(monkeypatch, request):
    monkeypatch.setenv("ALLOW_INTEGRATION_DB", "1")
    monkeypatch.setenv("NEON_DEV_ENDPOINT_ID", "ep-dev-abc")
    monkeypatch.setenv("NEON_DEV_REGION", "us-east-1")
    monkeypatch.setenv(
        "DATABASE_URL",
        f"postgresql://u:{_PW}@ep-dev-abc.us-west-2.aws.neon.tech/db?sslmode=require",
    )
    with pytest.raises(pytest.fail.Exception, match="region does not match NEON_DEV_REGION") as ei:
        request.getfixturevalue("integration_client")
    assert _PW not in str(ei.value)


def test_conftest_inline_guard_rejects_non_ascii_region_digit(monkeypatch, request):
    """Region us-east-١ must not match ASCII \\d; dropping re.ASCII would accept it."""
    import re as _re
    from tests.conftest import _NEON_HOST

    host = "ep-dev-abc.us-east-\u0661.aws.neon.tech"
    without_ascii = _re.compile(_NEON_HOST.pattern)
    assert without_ascii.fullmatch(host)
    assert _NEON_HOST.fullmatch(host) is None
    _assert_integration_url_rejected(
        monkeypatch,
        request,
        f"postgresql://u:{_PW}@{host}/db?sslmode=require",
        "not an accepted Neon host",
    )


def test_conftest_inline_guard_rejects_non_ascii_cell_digit(monkeypatch, request):
    """Cell tail c-1١ must not match [0-9]; a \\d cell without re.ASCII would accept it."""
    _assert_integration_url_rejected(
        monkeypatch,
        request,
        f"postgresql://u:{_PW}@ep-dev-abc.c-1\u0661.us-east-1.aws.neon.tech/db?sslmode=require",
        "not an accepted Neon host",
    )


def test_require_neon_integration_url_fails_on_guard_timeout(monkeypatch, tmp_path):
    import subprocess
    import tests.conftest as conftest

    fake_guard = tmp_path / "ci_guard_db.py"
    fake_guard.write_text("raise SystemExit('should not run')\n")
    monkeypatch.setattr(conftest, "_CI_GUARD", fake_guard)
    monkeypatch.setenv("NEON_DEV_ENDPOINT_ID", "ep-dev-abc")
    monkeypatch.delenv("NEON_DEV_REGION", raising=False)

    def fake_run(*args, **kwargs):
        assert kwargs.get("timeout") == 30
        raise subprocess.TimeoutExpired(cmd=kwargs.get("args") or args[0], timeout=30)

    monkeypatch.setattr(conftest.subprocess, "run", fake_run)
    with pytest.raises(pytest.fail.Exception, match="ci_guard_db.py timed out"):
        conftest._require_neon_integration_url(
            "postgresql://u:p@ep-dev-abc.us-east-1.aws.neon.tech/db?sslmode=require"
        )


def test_search_bind_types_native(client, fake_db):
    client.get("/api/players?q=Jalen&active=true&limit=5")
    key, p = [c for c in fake_db.calls if c[0] == "players/search"][-1]
    assert p["active"] is True
    assert isinstance(p["limit"], int)


# ---------- integration (Neon dev / CI branch) ----------
@pytest.mark.integration
def test_int_accent_search(integration_client):
    b = integration_client.get("/api/players?q=Jokic").get_json()
    assert any("Joki" in i["full_name"] for i in b["items"][:3])


@pytest.mark.integration
def test_int_browse_paging(integration_client):
    pages = walk_pages(integration_client, "/api/players?limit=50&active=all", ids, max_pages=200)
    flat = [x for p in pages for x in p]
    assert len(flat) == len(set(flat))


@pytest.mark.integration
def test_int_search_paging_float_cursor(integration_client):
    full = ids(integration_client.get("/api/players?q=jo&active=all&limit=100").get_json())
    flat = [x for p in walk_pages(integration_client, "/api/players?q=jo&active=all&limit=3", ids) for x in p]
    assert flat == full[: len(flat)] and len(flat) == len(set(flat))
