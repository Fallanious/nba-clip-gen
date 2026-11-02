"""GET /api/players and GET /api/players/:id (spec §1-3). Unit tests use FakeDB; integration marked."""
import json, pytest
from sqlalchemy.exc import OperationalError
from conftest import assert_error, b64url, tamper_cursor, walk_pages, PLAYERS

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
    monkeypatch.setenv("CURSOR_SECRET", "rotated-test-secret")
    from conftest import _make_app
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


def test_internal_500_no_stack(client, fake_db):
    fake_db.fail_with = RuntimeError("boom secret-ish detail")
    r = client.get("/api/players")
    err = assert_error(r, 500, "internal")
    assert "boom" not in err["message"]


def test_auth_codes_never_returned_locally(client):
    for url in ["/api/players", "/api/players/201"]:
        assert client.get(url).status_code not in (401, 403)


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
