"""QA fixtures for the Players/Clips API (spec v1).

Two layers:
  * unit (default): no live DB. The SQL registry/session is replaced by FakeDB,
    which emulates each registry key (players/search, clips/by_player, ...) in
    Python over SEED data. Keyed on the registry key, so it doesn't care what
    the repository functions are named.
  * integration (@pytest.mark.integration): real Postgres via DATABASE_URL
    (Neon *dev branch* or a per-CI branch only). Skipped when unset. Refuses
    any URL that looks like production.

ADAPT HERE: the ONLY places that know DEV's module names are the constants
below and `_make_app()`.
"""
import base64, json, os, re, unicodedata, datetime as dt
from types import SimpleNamespace
from contextlib import contextmanager
import pytest

# ---- ADAPT: module/attr names (confirm with DEV) --------------------------
import sys as _sys
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in _sys.path:
    _sys.path.insert(0, _SRC)   # app imports top-level modules: config, db.*, routes.*, services.*
APP_FACTORY = "app:create_app"          # app factory import path
SESSION_TARGET = "routes.players.get_db"  # routes call next(get_db())
REGISTRY_TARGET = None  # real registry is used; FakeDB maps TextClause -> key
S3_CLIENT_TARGET = "services.playback._s3_client"  # returns boto3 client
PLAYBACK_ENV = ["S3_BUCKET", "AWS_REGION", "PLAYBACK_ALLOW_FALLBACK",
                "PLAYBACK_VERIFY_OBJECT", "PLAYBACK_URL_TTL", "PLAYBACK_FALLBACK_TTL",
                "S3_CLIPS_PREFIX", "S3_GAMES_PREFIX"]
TEST_CURSOR_SECRET = "qa-test-cursor-secret-not-real"


def _make_app():
    """Create a fresh app instance (config now reads env vars dynamically)."""
    import importlib
    mod, attr = APP_FACTORY.split(":")
    return getattr(importlib.import_module(mod), attr)()


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: needs DATABASE_URL (Neon dev/CI branch)")


def pytest_collection_modifyitems(config, items):
    url = os.environ.get("DATABASE_URL")
    skip = pytest.mark.skip(reason="DATABASE_URL not set (integration tests need a Neon dev/CI branch)")
    for it in items:
        if "integration" in it.keywords and not url:
            it.add_marker(skip)


# ---- seed data --------------------------------------------------------------
TEAMS = {20: ("PHI", "Philadelphia", "76ers"), 7: ("DEN", "Denver", "Nuggets"),
         18: ("NYK", "New York", "Knicks"), 13: ("LAL", "Los Angeles", "Lakers")}

def _p(i, full, first, last, team, active=True, pos="G"):
    return dict(id=i, bbref_id=f"p{i}", full_name=full, first_name=first, last_name=last,
                position=pos, team_id=team, headshot_url=f"https://example.test/{i}.jpg",
                is_active=active)

PLAYERS = [
    _p(201, "Joel Embiid", "Joel", "Embiid", 20, pos="C"),
    _p(202, "Nikola Jokić", "Nikola", "Jokić", 7, pos="C"),
    _p(203, "Luka Dončić", "Luka", "Dončić", 13),
    _p(204, "Bogdan Bogdanović", "Bogdan", "Bogdanović", None),     # team null
    _p(205, "Jalen Brunson", "Jalen", "Brunson", 18),
    _p(206, "Jalen Williams", "Jalen", "Williams", 18),
    _p(207, "Jalen Smith", "Jalen", "Smith", 18),                    # same-score ties
    _p(208, "Vince Carter", "Vince", "Carter", None, active=False),
    _p(209, "Tyrese Maxey", "Tyrese", "Maxey", 20),
    _p(210, "Kelly Oubre Jr.", "Kelly", "Oubre", 20),
    _p(211, "Jalen Brunson", "Jalen", "Brunson", 18, active=False),  # dup name, tie on (score,last_name)
]
for n in range(12):  # bulk browse rows sharing a last_name -> exercises id tiebreak
    PLAYERS.append(_p(300 + n, f"Test Zed{n}", "Test", "Zz", 20 if n % 2 else 7))

GAMES = {
    77: dict(id=77, bbref_id="202604240PHI", game_date=dt.date(2026, 4, 24), season="2025-26", home=20, away=18, s3_key="games/202604240PHI.mp4"),
    61: dict(id=61, bbref_id="202601030NYK", game_date=dt.date(2026, 1, 3), season="2025-26", home=18, away=20, s3_key=None),
    50: dict(id=50, bbref_id="202504010PHI", game_date=dt.date(2025, 4, 1), season="2024-25", home=20, away=7, s3_key="games/202504010PHI.mp4"),
    51: dict(id=51, bbref_id="202604240DEN", game_date=dt.date(2026, 4, 24), season="2025-26", home=7, away=13, s3_key=None),  # same date as 77
}

CLIPS = {}
CLIP_PLAYERS = []  # (player_id, clip_id, role)

def _clip(cid, gid, idx, period, at, sub, out, status="uploaded", s3=True, roles=(("201", "shot"),)):
    g = GAMES[gid]
    CLIPS[cid] = dict(id=cid, game_id=gid, game_date=g["game_date"], play_index=idx, period=period,
                      game_clock=dt.timedelta(minutes=4, seconds=12), team_id=20,
                      description=f"play {cid}", action_type=at, shot_subtype=sub, outcome=out,
                      attributes={}, video_start_s=100.0 + idx, video_end_s=106.0 + idx, duration_s=6.0,
                      s3_key=(f"clips/{g['bbref_id']}/{cid}.mp4" if s3 else None), status=status)
    for pid, role in roles:
        CLIP_PLAYERS.append((int(pid), cid, role))

# Clip ids intentionally NOT in play order (spec: ids don't follow play order).
_clip(90977, 77, 38, 1, "shot", "dunk", "made")
_clip(90412, 77, 212, 3, "shot", "midrange", "made", roles=(("201", "shot"), ("209", "assist")))
_clip(90500, 77, 100, 2, "rebound", None, None, roles=(("201", "rebound"), ("201", "foul")))  # multi-role
_clip(90600, 77, 5, 1, "block", None, None, roles=(("201", "block"),))
_clip(88102, 61, 145, 2, "rebound", None, None, roles=(("201", "rebound"),))
_clip(88103, 61, 10, 1, "shot", "three_pointer", "missed")
_clip(88104, 61, 11, 1, "turnover", None, None, status="pending", s3=False, roles=(("201", "turnover"),))
_clip(88105, 61, 12, 1, "shot", "layup", "made", status="failed", s3=False)
_clip(70001, 50, 1, 1, "shot", "hook", "made")
_clip(70002, 50, 2, 4, "steal", None, None, roles=(("201", "steal"),))
_clip(70003, 50, 3, 4, "shot", "floater", "missed", status="rendered", s3=False)
_clip(95000, 51, 7, 1, "shot", "dunk", "made", roles=(("202", "shot"),))
for i in range(130):  # bulk for player 209 -> multi-page paging; spread across games
    gid = [77, 61, 50][i % 3]
    _clip(60000 + i, gid, 300 + i, 1 + i % 4, "shot", "layup", "made" if i % 2 else "missed",
          roles=(("209", "shot"),))

NOT_READY = {"pending": 88104, "failed": 88105, "rendered": 70003}
PLAYABLE_CLIP = 90412          # uploaded, game has s3_key
NOT_READY_WITH_GAME_KEY = 70003  # rendered, game 50 has s3_key -> fallback possible
NOT_READY_NO_GAME_KEY = 88104    # pending, game 61 no s3_key -> always 409


# ---- trigram similarity emulating pg_trgm on lower(unaccent(x)) -------------
def norm(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower()

def _trgms(s):
    out = set()
    for w in re.findall(r"[a-z0-9]+", s):
        w = "  " + w + " "
        out |= {w[i:i + 3] for i in range(len(w) - 2)}
    return out

def similarity(a, b):
    ta, tb = _trgms(norm(a)), _trgms(norm(b))
    if not ta or not tb:
        return 0.0
    return float(f"{len(ta & tb) / len(ta | tb):.6g}")  # real precision


class FakeDB:
    """Emulates session.execute(registry[key], params).mappings().all()/first()."""
    def __init__(self):
        self.calls = []
        self.fail_with = None  # set to an exception to simulate Neon outage

    def _team(self, tid):
        if tid is None:
            return dict(team_id=None, abbr=None, city=None, name=None)
        a, c, n = TEAMS[tid]
        return dict(team_id=tid, abbr=a, city=c, name=n)

    def run(self, key, p):
        self.calls.append((key, dict(p)))
        if self.fail_with:
            raise self.fail_with
        return getattr(self, "q_" + key.replace("/", "_"))(p)

    def q_players_search_browse(self, p):
        rows = [x for x in PLAYERS
                if (p.get("team_id") is None or x["team_id"] == p["team_id"])
                and (p.get("active") is None or x["is_active"] == p["active"])
                and (p.get("c_id") is None or (x["last_name"], x["id"]) > (p["c_last_name"], p["c_id"]))]
        rows.sort(key=lambda x: (x["last_name"], x["id"]))
        return [{**x, **self._team(x["team_id"])} for x in rows[: p["limit"] + 1]]

    def q_players_search(self, p):
        q = norm(p["q"])
        out = []
        for x in PLAYERS:
            s = similarity(x["full_name"], p["q"])
            if not (s >= 0.2 or q in norm(x["full_name"])):
                continue
            if p.get("team_id") is not None and x["team_id"] != p["team_id"]:
                continue
            if p.get("active") is not None and x["is_active"] != p["active"]:
                continue
            if p.get("c_score") is not None and not (
                    s < p["c_score"] or (s == p["c_score"] and (x["last_name"], x["id"]) > (p["c_last_name"], p["c_id"]))):
                continue
            out.append({**x, **self._team(x["team_id"]), "score": s})
        out.sort(key=lambda r: (-r["score"], r["last_name"], r["id"]))
        return out[: p["limit"] + 1]

    def q_players_get(self, p):
        x = next((x for x in PLAYERS if x["id"] == int(p["id"])), None)
        if not x:
            return []
        cps = [c for c in CLIP_PLAYERS if c[0] == x["id"]]
        dates = [CLIPS[c[1]]["game_date"] for c in cps]
        return [{**x, **self._team(x["team_id"]), "clip_count": len({c[1] for c in cps}),
                 "first_clip_date": min(dates) if dates else None, "last_clip_date": max(dates) if dates else None}]

    def q_players_role_counts(self, p):
        cnt = {}
        for pid, _, role in CLIP_PLAYERS:
            if pid == int(p["id"]):
                cnt[role] = cnt.get(role, 0) + 1
        return [dict(role=r, n=n) for r, n in sorted(cnt.items())]

    def q_players_exists(self, p):  # whatever key DEV uses for SELECT 1 (if any)
        return [{"?column?": 1}] if any(x["id"] == int(p.get("id", p.get("pid"))) for x in PLAYERS) else []

    def q_teams_by_abbr(self, p):
        """Look up team by abbreviation."""
        abbr = p["abbr"].upper()
        for tid, (tabbr, city, name) in TEAMS.items():
            if tabbr == abbr:
                return [{"id": tid}]
        return []

    def q_clips_by_player(self, p):
        grouped = {}
        for pid, cid, role in CLIP_PLAYERS:
            if pid != int(p["pid"]):
                continue
            c, g = CLIPS[cid], GAMES[CLIPS[cid]["game_id"]]
            ok = ((not p.get("roles") or role in p["roles"])
                  and (p.get("date_from") is None or c["game_date"] >= p["date_from"])
                  and (p.get("date_to") is None or c["game_date"] <= p["date_to"])
                  and (p.get("season") is None or g["season"] == p["season"])
                  and (p.get("game_id") is None or c["game_id"] == p["game_id"])
                  and (not p.get("action_types") or c["action_type"] in p["action_types"])
                  and (not p.get("subtypes") or c["shot_subtype"] in p["subtypes"])
                  and (p.get("outcome") is None or c["outcome"] == p["outcome"])
                  and (p.get("status_all") or (c["status"] == "uploaded" and c["s3_key"]))
                  and (p.get("c_date") is None or (c["game_date"], cid) < (p["c_date"], p["c_id"])))
            if ok:
                grouped.setdefault(cid, []).append(role)
        keys = sorted(grouped, key=lambda cid: (CLIPS[cid]["game_date"], cid), reverse=True)[: p["limit"] + 1]
        out = []
        for cid in keys:
            c, g = CLIPS[cid], GAMES[CLIPS[cid]["game_id"]]
            out.append(dict(clip_id=cid, game_date=c["game_date"], roles=sorted(grouped[cid]),
                            game_id=c["game_id"], play_index=c["play_index"], period=c["period"],
                            game_clock=c["game_clock"], description=c["description"],
                            action_type=c["action_type"], shot_subtype=c["shot_subtype"], outcome=c["outcome"],
                            duration_s=c["duration_s"], status=c["status"], team_id=c["team_id"],
                            has_object=c["s3_key"] is not None, bbref_id=g["bbref_id"], season=g["season"],
                            home_abbr=TEAMS[g["home"]][0], away_abbr=TEAMS[g["away"]][0]))
        return out

    def q_clips_get(self, p):
        c = CLIPS.get(int(p["id"]))
        if not c:
            return []
        g = GAMES[c["game_id"]]
        players = [dict(player_id=str(pid), full_name=next(x["full_name"] for x in PLAYERS if x["id"] == pid), role=r)
                   for pid, cid, r in CLIP_PLAYERS if cid == c["id"]]
        return [{**c, "bbref_id": g["bbref_id"], "season": g["season"], "has_source": g["s3_key"] is not None,
                 "home_abbr": TEAMS[g["home"]][0], "away_abbr": TEAMS[g["away"]][0],
                 "team_abbr": TEAMS[c["team_id"]][0] if c["team_id"] else None, "players": players}]

    def q_clips_playback_target(self, p):
        c = CLIPS.get(int(p["id"]))
        if not c:
            return []
        return [dict(id=c["id"], game_id=c["game_id"], status=c["status"], s3_key=c["s3_key"],
                     video_start_s=c["video_start_s"], video_end_s=c["video_end_s"],
                     game_key=GAMES[c["game_id"]]["s3_key"])]


class _Result:
    def __init__(self, rows): self.rows = rows
    def mappings(self): return self
    def all(self): return self.rows
    def first(self): return self.rows[0] if self.rows else None
    def one_or_none(self): return self.first()
    def scalar(self): return next(iter(self.rows[0].values())) if self.rows else None
    def __iter__(self): return iter(self.rows)


class _Clause:
    def __init__(self, key): self.key = key


class _Session:
    def __init__(self, db): self.db = db
    def execute(self, clause, params=None):
        if isinstance(clause, _Clause):
            return _Result(self.db.run(clause.key, params or {}))
        # SET statements (statement_timeout, pg_trgm.similarity_threshold) -> no-op
        return _Result([])
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def close(self): pass


class _Registry(dict):
    def __missing__(self, key):
        return _Clause(key)


# ---- fixtures -----------------------------------------------------------------
@pytest.fixture
def base_env(monkeypatch):
    """Safe env for every test: no real DB, no AWS creds, deterministic cursor secret."""
    monkeypatch.setenv("CURSOR_SECRET", TEST_CURSOR_SECRET)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:5173")
    for k in PLAYBACK_ENV + ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE"]:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", "/dev/null")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/dev/null")


@pytest.fixture
def fake_db(monkeypatch, base_env):
    db = FakeDB()
    # DATABASE_URL unset triggers test mode in session.py (no engine creation)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    
    # Patch BEFORE any app imports happen
    # Patch the registry dict itself (used by repositories as `registry["key"]`)
    # Try both with and without src prefix
    monkeypatch.setattr("src.db.sql_loader.registry", _Registry(), raising=False)
    monkeypatch.setattr("db.sql_loader.registry", _Registry(), raising=False)
    
    # Patch get_db at the definition site BEFORE routes import it
    def fake_get_db():
        yield _Session(db)
    
    monkeypatch.setattr("src.db.session.get_db", fake_get_db, raising=False)
    monkeypatch.setattr("db.session.get_db", fake_get_db, raising=False)
    
    return db


@pytest.fixture
def app(fake_db, monkeypatch):
    # Ensure patches are in place before create_app imports anything
    # The fake_db fixture has already patched everything we need
    a = _make_app()
    a.config.update(TESTING=True)
    return a


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def playback_configured(monkeypatch):
    """Configure playback with fake values and a stub S3 client (no network, no creds)."""
    monkeypatch.setenv("S3_BUCKET", "qa-fake-bucket")
    monkeypatch.setenv("AWS_REGION", "us-east-2")
    monkeypatch.setenv("PLAYBACK_ALLOW_FALLBACK", "true")
    monkeypatch.setenv("PLAYBACK_VERIFY_OBJECT", "false")
    stub = SimpleNamespace(calls=[], head_calls=[], head_exc=None, sign_exc=None)

    def generate_presigned_url(op, Params, ExpiresIn):
        stub.calls.append((op, Params, ExpiresIn))
        if stub.sign_exc:
            raise stub.sign_exc
        return f"https://{Params['Bucket']}.s3.us-east-2.amazonaws.com/{Params['Key']}?X-Amz-Signature=fake"

    def head_object(Bucket, Key):
        stub.head_calls.append((Bucket, Key))
        if stub.head_exc:
            raise stub.head_exc
        return {"ContentLength": 1}

    stub.generate_presigned_url = generate_presigned_url
    stub.head_object = head_object
    monkeypatch.setattr(S3_CLIENT_TARGET, lambda *a, **k: stub, raising=False)
    return stub


@pytest.fixture
def playback_client(fake_db, playback_configured):
    """Client with playback configured - env vars set before app creation."""
    a = _make_app()
    a.config.update(TESTING=True)
    return a.test_client()


@pytest.fixture
def integration_client(base_env, monkeypatch):
    url = os.environ.get("DATABASE_URL", "")
    if re.search(r"prod|main|primary", url, re.I):
        pytest.skip("Refusing to run against what looks like a production DATABASE_URL")
    monkeypatch.setenv("DATABASE_URL", url)
    a = _make_app()
    a.config.update(TESTING=True)
    return a.test_client()


# ---- helpers --------------------------------------------------------------------
def assert_error(resp, status, code, param=None):
    assert resp.status_code == status, resp.get_data(as_text=True)
    body = resp.get_json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    assert "Traceback" not in resp.get_data(as_text=True)
    assert resp.headers.get("X-Request-Id")
    if param:
        assert body["error"].get("details", {}).get("param") == param
    return body["error"]


def b64url(obj):
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")


def tamper_cursor(cursor):
    """Flip the payload but keep the old signature -> HMAC must fail."""
    payload, _, sig = cursor.partition(".")
    raw = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    raw["k"][-1] = str(int(raw["k"][-1]) + 1) if str(raw["k"][-1]).isdigit() else raw["k"][-1]
    raw["x"] = 1
    return b64url(raw) + "." + sig


def walk_pages(client, url, item_fn, max_pages=500):
    """Follow next_cursor; returns list of pages' items."""
    pages, cursor = [], None
    sep = "&" if "?" in url else "?"
    seen_cursors = set()
    for page_num in range(max_pages):
        r = client.get(url + (f"{sep}cursor={cursor}" if cursor else ""))
        assert r.status_code == 200, r.get_data(as_text=True)
        body = r.get_json()
        pages.append(item_fn(body))
        cursor = body["next_cursor"]
        if not cursor:
            return pages
        # Detect cursor loop
        if cursor in seen_cursors:
            # Debug: print recent pages to see pattern
            last_3_pages = [str(p)[:100] for p in pages[-3:]]
            raise AssertionError(f"cursor loop detected at page {page_num + 1}. Last 3 pages: {last_3_pages}")
        seen_cursors.add(cursor)
    raise AssertionError(f"paging did not terminate after {max_pages} pages")
