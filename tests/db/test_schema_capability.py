"""Isolated Postgres: migrate, VIEW, constraints, seed. Never Neon."""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import tempfile
import time

import pytest

pytestmark = pytest.mark.postgres

from db.capability import public_capability
from db.metadata_allowlist import assert_browse_row, assert_capability_row
from db.migrate import apply_migrations, apply_seed
from db.sql_loader import load_query_sql, SQL_ROOT

BIN = "/usr/lib/postgresql/16/bin"


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="session")
def postgres_url():
    if not os.path.isfile(os.path.join(BIN, "initdb")):
        pytest.fail("PostgreSQL 16 initdb is required for designated DB tests")
    import psycopg

    tmp = tempfile.mkdtemp(prefix="nba-clip-pg-")
    data = os.path.join(tmp, "data")
    run = os.path.join(tmp, "run")
    os.makedirs(run)
    subprocess.check_call(
        [os.path.join(BIN, "initdb"), "-D", data, "-A", "trust", "-U", "nba", "--no-instructions"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    port = _free_port()
    proc = subprocess.Popen(
        [
            os.path.join(BIN, "postgres"),
            "-D", data,
            "-p", str(port),
            "-k", run,
            "-c", "listen_addresses=127.0.0.1",
            "-c", f"unix_socket_directories={run}",
            "-c", "fsync=off",
            "-c", "synchronous_commit=off",
            "-c", "full_page_writes=off",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    admin = f"postgresql://nba@127.0.0.1:{port}/postgres"
    last_err = None
    for _ in range(80):
        try:
            with psycopg.connect(admin, connect_timeout=1) as conn:
                conn.execute("SELECT 1")
            last_err = None
            break
        except Exception as exc:
            last_err = exc
            time.sleep(0.1)
    if last_err is not None:
        proc.kill()
        raise RuntimeError(f"local postgres failed to start: {last_err}")
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute("CREATE DATABASE nba_clip_gen_test")
    url = f"postgresql://nba@127.0.0.1:{port}/nba_clip_gen_test"
    os.environ.setdefault("APP_ENV", "test")
    yield url
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(scope="session")
def migrated_url(postgres_url):
    os.environ["APP_ENV"] = "test"
    applied = apply_migrations(postgres_url)
    assert "0001_init" in applied
    assert "0003_on_demand" in applied
    apply_seed(postgres_url)
    return postgres_url


@pytest.fixture
def conn(migrated_url):
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(migrated_url, row_factory=dict_row) as c:
        yield c
        c.rollback()


def test_seed_rows_browsable_including_unmatched_legacy_null(conn):
    rows = conn.execute(load_query_sql("plays/browse_metadata")).fetchall()
    assert len(rows) >= 8
    descriptions = {r["description"] for r in rows}
    assert any("Unmatched" in d for d in descriptions)
    assert any("Legacy" in d for d in descriptions)
    assert any("No source" in d for d in descriptions)
    for row in rows:
        assert_browse_row(dict(row))
        assert row["can_create"] in (True, False)


def test_browse_select_does_not_enqueue_or_write(conn):
    before = conn.execute("SELECT count(*) AS n FROM clips").fetchone()["n"]
    conn.execute(load_query_sql("plays/browse_metadata")).fetchall()
    after = conn.execute("SELECT count(*) AS n FROM clips").fetchone()["n"]
    assert after == before
    missing = conn.execute(
        "SELECT to_regclass('public.clip_jobs') AS t"
    ).fetchone()["t"]
    assert missing is None


def test_truth_table_view_matches_python(conn):
    joined = conn.execute(
        """
        SELECT c.id, cap.can_create, cap.reason,
               g.s3_key, g.source_kind, g.source_version, g.duration_s,
               g.is_fictional,
               c.timing_provenance, c.matched_source_version,
               c.video_start_s, c.video_end_s, c.game_clock
        FROM clips c
        JOIN games g ON g.id = c.game_id
        JOIN play_capability cap ON cap.play_id = c.id
        """
    ).fetchall()
    eligible = 0
    for row in joined:
        py = public_capability(row)
        assert py["can_create"] is bool(row["can_create"])
        assert py["reason"] == row["reason"]
        assert_capability_row({"play_id": row["id"], **py})
        if py["can_create"]:
            eligible += 1
    assert eligible == 2  # zero-start and fractional on game A


def test_game_clock_update_does_not_change_capability(conn):
    row = conn.execute(
        """
        SELECT cap.can_create, cap.reason, c.id
        FROM clips c
        JOIN play_capability cap ON cap.play_id = c.id
        WHERE c.description LIKE '%Pull-up jumper%'
        """
    ).fetchone()
    assert row["can_create"] is True
    conn.execute(
        "UPDATE clips SET game_clock = INTERVAL '99 minutes' WHERE id = %s",
        (row["id"],),
    )
    again = conn.execute(
        "SELECT can_create, reason FROM play_capability WHERE play_id = %s",
        (row["id"],),
    ).fetchone()
    assert again["can_create"] is True
    assert again["reason"] is None


def test_public_and_derived_not_eligible(conn):
    rows = conn.execute(
        """
        SELECT g.source_kind, cap.can_create, cap.reason
        FROM play_capability cap
        JOIN clips c ON c.id = cap.play_id
        JOIN games g ON g.id = c.game_id
        WHERE g.source_kind IN ('public', 'derived')
        """
    ).fetchall()
    assert rows
    for row in rows:
        assert row["can_create"] is False
        assert row["reason"] == "footage_not_matched"


def test_constraint_rejects_reversed_equal_nan(conn):
    game_id = conn.execute(
        "SELECT id FROM games WHERE bbref_id = 'fic000001FIC'"
    ).fetchone()["id"]
    with pytest.raises(Exception):
        conn.execute(
            """
            INSERT INTO clips (game_id, game_date, play_index, period, description,
              timing_provenance, video_start_s, video_end_s)
            VALUES (%s, DATE '2026-01-03', 90, 1, 'FICTIONAL reversed',
              'unvalidated_numeric', 9, 3)
            """,
            (game_id,),
        )
    conn.rollback()
    with pytest.raises(Exception):
        conn.execute(
            """
            INSERT INTO clips (game_id, game_date, play_index, period, description,
              timing_provenance, video_start_s, video_end_s)
            VALUES (%s, DATE '2026-01-03', 91, 1, 'FICTIONAL equal',
              'unvalidated_numeric', 4, 4)
            """,
            (game_id,),
        )
    conn.rollback()
    with pytest.raises(Exception):
        conn.execute(
            """
            INSERT INTO clips (game_id, game_date, play_index, period, description,
              timing_provenance, video_start_s, video_end_s)
            VALUES (%s, DATE '2026-01-03', 92, 1, 'FICTIONAL nan',
              'unvalidated_numeric', 'NaN'::numeric, 4)
            """,
            (game_id,),
        )
    conn.rollback()
    with pytest.raises(Exception):
        conn.execute(
            """
            INSERT INTO clips (game_id, game_date, play_index, period, description,
              timing_provenance, video_start_s, video_end_s)
            VALUES (%s, DATE '2026-01-03', 93, 1, 'FICTIONAL onesided',
              'unvalidated_numeric', 1, NULL)
            """,
            (game_id,),
        )
    conn.rollback()
    with pytest.raises(Exception):
        conn.execute(
            """
            INSERT INTO clips (game_id, game_date, play_index, period, description,
              timing_provenance, video_start_s, video_end_s)
            VALUES (%s, DATE '2026-01-03', 94, 1, 'FICTIONAL inf',
              'unvalidated_numeric', 0, 'Infinity'::numeric)
            """,
            (game_id,),
        )
    conn.rollback()
    with pytest.raises(Exception):
        conn.execute(
            """
            INSERT INTO clips (game_id, game_date, play_index, period, description,
              timing_provenance, video_start_s, video_end_s)
            VALUES (%s, DATE '2026-01-03', 95, 1, 'FICTIONAL end nan independent',
              'unvalidated_numeric', 1, 'NaN'::numeric)
            """,
            (game_id,),
        )
    conn.rollback()
    with pytest.raises(Exception):
        conn.execute(
            """
            INSERT INTO clips (game_id, game_date, play_index, period, description,
              timing_provenance, video_start_s, video_end_s, video_timestamp_s,
              matcher_run_id, matcher_version, matched_source_version)
            VALUES (%s, DATE '2026-01-03', 96, 1, 'FICTIONAL timestamp nan',
              'matcher_attested', 1, 4, 'NaN'::numeric,
              'fic-run-a', 'match_play_timestamps/fictional', 'etag-fic-a-v1')
            """,
            (game_id,),
        )


def test_url_key_rejected_on_games(conn):
    teams = conn.execute("SELECT id FROM teams ORDER BY id LIMIT 2").fetchall()
    with pytest.raises(Exception):
        conn.execute(
            """
            INSERT INTO games (bbref_id, season, game_date, home_team_id, away_team_id,
              s3_key, source_kind, is_fictional)
            VALUES ('ficbadurlFIC', '2025-26', DATE '2026-02-01', %s, %s,
              'https://example.invalid/x.mkv', 'original_private', true)
            """,
            (teams[0]["id"], teams[1]["id"]),
        )


def test_legacy_upgrade_preserves_row_unavailable(postgres_url):
    import psycopg
    from psycopg.rows import dict_row
    from db.migrate import SCHEMA_DIR, _exec_file, apply_migrations

    admin = postgres_url.rsplit("/", 1)[0] + "/postgres"
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute("DROP DATABASE IF EXISTS nba_legacy_up")
        c.execute("CREATE DATABASE nba_legacy_up")
    url = postgres_url.rsplit("/", 1)[0] + "/nba_legacy_up"
    os.environ["APP_ENV"] = "test"
    with psycopg.connect(url, autocommit=True) as conn:
        _exec_file(conn, SCHEMA_DIR / "0001_init.sql")
        conn.execute(
            """
            INSERT INTO teams (abbr, city, name) VALUES ('LEG', 'Legacy', 'Row'), ('OTH', 'Other', 'Side')
            """
        )
        ids = conn.execute("SELECT id, abbr FROM teams").fetchall()
        by = {r[1]: r[0] for r in ids}
        conn.execute(
            """
            INSERT INTO games (bbref_id, season, game_date, home_team_id, away_team_id, s3_key, duration_s)
            VALUES ('ficlegacyFIC', '2025-26', DATE '2026-01-01', %s, %s, 'fixtures/legacy.mkv', 40)
            """,
            (by["LEG"], by["OTH"]),
        )
        gid = conn.execute("SELECT id FROM games").fetchone()[0]
        conn.execute(
            """
            INSERT INTO clips (game_id, game_date, play_index, period, description,
              video_start_s, video_end_s)
            VALUES (%s, DATE '2026-01-01', 1, 1, 'FICTIONAL legacy upgrade', 3, 9)
            """,
            (gid,),
        )
    applied = apply_migrations(url)
    assert "0003_on_demand" in applied
    with psycopg.connect(url, row_factory=dict_row) as conn:
        row = conn.execute(
            """
            SELECT c.timing_provenance, cap.can_create, cap.reason, c.description
            FROM clips c JOIN play_capability cap ON cap.play_id = c.id
            """
        ).fetchone()
        assert row["description"] == "FICTIONAL legacy upgrade"
        assert row["timing_provenance"] == "unvalidated_numeric"
        assert row["can_create"] is False
        # 0001 games have no source_kind/version; precedence yields footage_not_matched.
        assert row["reason"] == "footage_not_matched"


def test_rollback_and_reapply_0003(postgres_url):
    import psycopg
    from db.migrate import SCHEMA_DIR, _exec_file, _split_sql, apply_migrations

    admin = postgres_url.rsplit("/", 1)[0] + "/postgres"
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute("DROP DATABASE IF EXISTS nba_down_up")
        c.execute("CREATE DATABASE nba_down_up")
    url = postgres_url.rsplit("/", 1)[0] + "/nba_down_up"
    os.environ["APP_ENV"] = "test"
    apply_migrations(url)
    down = (SCHEMA_DIR / "down" / "0003_on_demand.down.sql").read_text(encoding="utf-8")
    with psycopg.connect(url, autocommit=True) as conn:
        with conn.cursor() as cur:
            for stmt in _split_sql(down):
                cur.execute(stmt)
        exists = conn.execute(
            "SELECT to_regclass('public.play_capability')"
        ).fetchone()[0]
        assert exists is None
    applied = apply_migrations(url)
    assert "0003_on_demand" in applied
    with psycopg.connect(url) as conn:
        assert conn.execute("SELECT to_regclass('public.play_capability')").fetchone()[0]


def test_stable_identity_unique_for_later_persist(conn):
    """Later matcher persist upserts on these keys. This slice must keep them."""
    from psycopg.errors import UniqueViolation

    game_id = conn.execute(
        "SELECT id FROM games WHERE bbref_id = 'fic000001FIC'"
    ).fetchone()["id"]
    with pytest.raises(UniqueViolation):
        conn.execute(
            """
            INSERT INTO clips (game_id, game_date, play_index, period, description,
              timing_provenance, video_start_s, video_end_s)
            VALUES (%s, DATE '2026-01-03', 1, 1, 'FICTIONAL duplicate play identity',
              'none', NULL, NULL)
            """,
            (game_id,),
        )
    conn.rollback()
    with pytest.raises(UniqueViolation):
        conn.execute(
            """
            INSERT INTO games (bbref_id, season, game_date, home_team_id, away_team_id)
            SELECT 'fic000001FIC', season, game_date, home_team_id, away_team_id
            FROM games WHERE bbref_id = 'fic000001FIC'
            """
        )
    conn.rollback()
    with pytest.raises(UniqueViolation):
        conn.execute(
            """
            INSERT INTO players (bbref_id, full_name, last_name)
            VALUES ('ficcas01', 'Casey Fixture', 'Fixture')
            """
        )
    conn.rollback()


def test_attested_121s_window_rejected_by_storage_check(conn):
    game_id = conn.execute(
        "SELECT id FROM games WHERE bbref_id = 'fic000001FIC'"
    ).fetchone()["id"]
    with pytest.raises(Exception):
        conn.execute(
            """
            INSERT INTO clips (game_id, game_date, play_index, period, description,
              timing_provenance, video_start_s, video_end_s, video_timestamp_s,
              matcher_run_id, matcher_version, matched_source_version)
            VALUES (%s, DATE '2026-01-03', 97, 1, 'FICTIONAL attested 121s',
              'matcher_attested', 0, 121, 1,
              'fic-run-a', 'match_play_timestamps/fictional', 'etag-fic-a-v1')
            """,
            (game_id,),
        )
    conn.rollback()
    conn.execute(
        """
        INSERT INTO clips (game_id, game_date, play_index, period, description,
          timing_provenance, video_start_s, video_end_s, video_timestamp_s,
          matcher_run_id, matcher_version, matched_source_version)
        VALUES (%s, DATE '2026-01-03', 98, 1, 'FICTIONAL attested 120s',
          'matcher_attested', 0, 120, 1,
          'fic-run-a', 'match_play_timestamps/fictional', 'etag-fic-a-v1')
        """,
        (game_id,),
    )


def test_fictional_live_prefix_reason_is_footage_when_check_bypassed(conn):
    """Python and VIEW source-reason parity for legacy pairing even if CHECK is dropped."""
    from db.capability import public_capability

    teams = conn.execute("SELECT id FROM teams ORDER BY id LIMIT 2").fetchall()
    conn.execute("ALTER TABLE games DROP CONSTRAINT IF EXISTS games_fictional_key_prefix")
    conn.execute(
        """
        INSERT INTO games (
          bbref_id, season, game_date, home_team_id, away_team_id,
          s3_key, duration_s, source_kind, source_version, is_fictional
        )
        VALUES (
          'ficbypass01FIC', '2025-26', DATE '2026-01-03', %s, %s,
          'games/ficbypass01FIC.mkv', 48, 'original_private', 'etag-bypass-v1', true
        )
        """,
        (teams[0]["id"], teams[1]["id"]),
    )
    game = conn.execute(
        "SELECT id, s3_key, source_kind, source_version, duration_s, is_fictional FROM games WHERE bbref_id = 'ficbypass01FIC'"
    ).fetchone()
    conn.execute(
        """
        INSERT INTO clips (game_id, game_date, play_index, period, description,
          timing_provenance, video_start_s, video_end_s, video_timestamp_s,
          matcher_run_id, matcher_version, matched_source_version)
        VALUES (%s, DATE '2026-01-03', 1, 1, 'FICTIONAL bypass pairing',
          'matcher_attested', 0, 8, 1,
          'fic-run-a', 'match_play_timestamps/fictional', %s)
        """,
        (game["id"], game["source_version"]),
    )
    cap = conn.execute(
        "SELECT can_create, reason FROM play_capability WHERE play_id = "
        "(SELECT id FROM clips WHERE game_id = %s AND play_index = 1)",
        (game["id"],),
    ).fetchone()
    py = public_capability({
        **dict(game),
        "timing_provenance": "matcher_attested",
        "matched_source_version": game["source_version"],
        "video_start_s": 0,
        "video_end_s": 8,
    })
    assert cap["can_create"] is False
    assert cap["reason"] == "footage_not_matched"
    assert py == {"can_create": False, "reason": "footage_not_matched"}


def test_tiny_positive_gap_eligible_in_view(conn):
    from db.capability import live_media_access_permitted

    game = conn.execute(
        """
        SELECT id, s3_key, source_kind, source_version, duration_s, is_fictional
        FROM games WHERE bbref_id = 'fic000001FIC'
        """
    ).fetchone()
    conn.execute(
        """
        INSERT INTO clips (game_id, game_date, play_index, period, description,
          timing_provenance, video_start_s, video_end_s, video_timestamp_s,
          matcher_run_id, matcher_version, matched_source_version)
        VALUES (%s, DATE '2026-01-03', 80, 1, 'FICTIONAL tiny positive gap',
          'matcher_attested', 0, 0.1, 0.05,
          'fic-run-a', 'match_play_timestamps/fictional', %s)
        """,
        (game["id"], game["source_version"]),
    )
    cap = conn.execute(
        "SELECT can_create, reason FROM play_capability WHERE play_id = "
        "(SELECT id FROM clips WHERE game_id = %s AND play_index = 80)",
        (game["id"],),
    ).fetchone()
    assert cap["can_create"] is True
    assert cap["reason"] is None
    assert live_media_access_permitted(dict(game)) is False


def test_fictional_prefix_cannot_be_live(conn):
    teams = conn.execute("SELECT id FROM teams ORDER BY id LIMIT 2").fetchall()
    with pytest.raises(Exception):
        conn.execute(
            """
            INSERT INTO games (bbref_id, season, game_date, home_team_id, away_team_id,
              s3_key, source_kind, source_version, is_fictional)
            VALUES ('ficlivefixFIC', '2025-26', DATE '2026-03-01', %s, %s,
              'fixtures/nope.mkv', 'original_private', 'etag-x', false)
            """,
            (teams[0]["id"], teams[1]["id"]),
        )
    conn.rollback()
    with pytest.raises(Exception):
        conn.execute(
            """
            INSERT INTO games (bbref_id, season, game_date, home_team_id, away_team_id,
              s3_key, source_kind, source_version, is_fictional)
            VALUES ('ficficliveFIC', '2025-26', DATE '2026-03-02', %s, %s,
              'games/fic000001FIC.mkv', 'original_private', 'etag-x', true)
            """,
            (teams[0]["id"], teams[1]["id"]),
        )


def test_policy_doc_present():
    text = (SQL_ROOT / "POLICY.md").read_text(encoding="utf-8")
    assert "CLIP_MAX_DURATION_S = 120" in text
    assert "CLIP_MIN_DURATION_S" not in text
    assert "VIDEO_SECONDS_ABS_MAX" not in text
    assert "no-op" in text
    assert "matcher_attested" in text
    assert "Rollback" in text or "rollback" in text
    assert "game_clock" in text.lower()
    assert "UNIQUE (clips.game_id, clips.play_index)" in text
    assert "Later persistence" in text
    assert "never stamps 0001 merely because" in text
    assert "anonymous shared catalog" in text.lower()
    assert "user- or team-ownership filters" in text
    assert "owner_id" in text
    assert "not a hard block" in text or "Identity / sign-in is **not** a hard block" in text
    assert "generated-clip-only" in text or "generated output only" in text
    assert "client keys" in text
    assert "per-client" in text
    assert "LocalUser" in text
    assert "no-op authorize" in text
    assert "uniform unavailable" in text
    assert "Fail closed" in text
    assert "X-Forwarded-For" in text
    assert "fenced" in text
    assert "atomic" in text
    assert "bearer" in text
    assert "bandwidth/cost ceiling" in text
    assert "quota storage" in text.lower() or "quota-storage" in text
    assert "no job/limiter implementation" in text.lower() or (
        "no job/limiter" in text.lower()
    )
    assert "equals itself" in text
    assert "NaN = NaN" in text
    assert "Do **not** use `x = x`" in text or "Do not use `x = x`" in text
    assert "nba_numeric_is_finite" in text
    assert "Published / deleted predicates are missing" in text
    assert "unwired" in text.lower()
    assert "explicit" in text.lower() and "dev" in text and "test" in text
    assert "fullmatch" in text
    assert "pg_advisory_xact_lock" in text
    assert "incomplete canary" in text.lower() or "not** secrecy" in text.lower()


def test_schema_has_no_owner_or_user_acl_columns(conn):
    rows = conn.execute(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name IN ('teams', 'players', 'games', 'clips', 'clip_players')
          AND column_name IN (
            'owner_id', 'user_id', 'created_by', 'org_id', 'acl', 'owner'
          )
        """
    ).fetchall()
    assert rows == []


def _fresh_db(postgres_url, name):
    import psycopg

    admin = postgres_url.rsplit("/", 1)[0] + "/postgres"
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {name}")
        c.execute(f"CREATE DATABASE {name}")
    return postgres_url.rsplit("/", 1)[0] + f"/{name}"


def test_empty_db_applies_0001_and_0003(postgres_url):
    from db.migrate import apply_migrations

    os.environ["APP_ENV"] = "test"
    url = _fresh_db(postgres_url, "nba_empty_base")
    applied = apply_migrations(url)
    assert applied == ["0001_init", "0003_on_demand"]
    import psycopg
    with psycopg.connect(url) as conn:
        assert conn.execute("SELECT version FROM schema_migrations ORDER BY 1").fetchall() == [
            ("0001_init",),
            ("0003_on_demand",),
        ]
        assert conn.execute("SELECT to_regclass('public.play_capability')").fetchone()[0]


def test_expected_complete_0001_stamps_without_recreate(postgres_url):
    import psycopg
    from db.migrate import SCHEMA_DIR, _exec_file, apply_migrations

    os.environ["APP_ENV"] = "test"
    url = _fresh_db(postgres_url, "nba_expected_base")
    with psycopg.connect(url, autocommit=True) as conn:
        _exec_file(conn, SCHEMA_DIR / "0001_init.sql")
        before = conn.execute("SELECT to_regclass('public.teams')").fetchone()[0]
        assert before
    applied = apply_migrations(url)
    assert "0001_init" in applied
    assert "0003_on_demand" in applied
    with psycopg.connect(url) as conn:
        versions = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
        assert versions == {"0001_init", "0003_on_demand"}
        assert conn.execute("SELECT to_regclass('public.play_capability')").fetchone()[0]


def test_partial_teams_only_refuses_stamp(postgres_url):
    import psycopg
    from db.migrate import MigrationRefused, apply_migrations

    os.environ["APP_ENV"] = "test"
    url = _fresh_db(postgres_url, "nba_partial_base")
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("CREATE TABLE teams (id int PRIMARY KEY, abbr text)")
    with pytest.raises(MigrationRefused, match="partial or mismatched 0001"):
        apply_migrations(url)
    with psycopg.connect(url) as conn:
        stamped = conn.execute(
            "SELECT to_regclass('public.schema_migrations')"
        ).fetchone()[0]
        if stamped:
            rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
            assert rows == []
        assert conn.execute("SELECT to_regclass('public.clips')").fetchone()[0] is None


def test_wrong_baseline_missing_unique_refuses_stamp(postgres_url):
    import psycopg
    from db.migrate import MigrationRefused, apply_migrations

    os.environ["APP_ENV"] = "test"
    url = _fresh_db(postgres_url, "nba_wrong_base")
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("CREATE TABLE teams (id int PRIMARY KEY, abbr text UNIQUE, city text, name text)")
        conn.execute(
            "CREATE TABLE players (id int PRIMARY KEY, bbref_id text UNIQUE, full_name text, last_name text)"
        )
        conn.execute(
            """
            CREATE TABLE games (
              id int PRIMARY KEY, bbref_id text UNIQUE, season text,
              game_date date, home_team_id int, away_team_id int,
              s3_key text, duration_s numeric
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE clips (
              id int PRIMARY KEY, game_id int, game_date date, play_index int,
              period int, description text, action_type text, shot_subtype text,
              outcome text, video_start_s numeric, video_end_s numeric
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE clip_players (
              clip_id int, player_id int, role text, game_date date,
              PRIMARY KEY (player_id, clip_id, role)
            )
            """
        )
    with pytest.raises(MigrationRefused, match="missing UNIQUE"):
        apply_migrations(url)
    with psycopg.connect(url) as conn:
        assert conn.execute("SELECT to_regclass('public.play_capability')").fetchone()[0] is None


def test_stamped_0001_without_matching_schema_refuses(postgres_url):
    import psycopg
    from db.migrate import MigrationRefused, apply_migrations

    os.environ["APP_ENV"] = "test"
    url = _fresh_db(postgres_url, "nba_stale_stamp")
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            """
            CREATE TABLE schema_migrations (
              version text PRIMARY KEY,
              applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute("INSERT INTO schema_migrations (version) VALUES ('0001_init')")
        conn.execute("CREATE TABLE teams (id int PRIMARY KEY, abbr text)")
    with pytest.raises(MigrationRefused, match="0001 schema mismatch"):
        apply_migrations(url)


def _install_dirty_0001_like(conn, *, duration_sql: str = "-1"):
    conn.execute(
        "CREATE TABLE teams (id int PRIMARY KEY, abbr text UNIQUE, city text, name text)"
    )
    conn.execute(
        "CREATE TABLE players (id int PRIMARY KEY, bbref_id text UNIQUE, full_name text, last_name text)"
    )
    conn.execute(
        """
        CREATE TABLE games (
          id int PRIMARY KEY, bbref_id text UNIQUE, season text,
          game_date date, home_team_id int, away_team_id int,
          s3_key text, duration_s numeric
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE clips (
          id int PRIMARY KEY, game_id int, game_date date, play_index int,
          period int, description text, action_type text, shot_subtype text,
          outcome text, video_start_s numeric, video_end_s numeric,
          UNIQUE (game_id, play_index)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE clip_players (
          clip_id int, player_id int, role text, game_date date,
          PRIMARY KEY (player_id, clip_id, role)
        )
        """
    )
    conn.execute("INSERT INTO teams VALUES (1, 'AAA', 'A', 'A'), (2, 'BBB', 'B', 'B')")
    conn.execute(
        f"""
        INSERT INTO games (id, bbref_id, season, game_date, home_team_id, away_team_id,
          s3_key, duration_s)
        VALUES (1, 'ficdirtyFIC', '2025-26', DATE '2026-01-01', 1, 2,
          'fixtures/dirty.mkv', {duration_sql})
        """
    )
    conn.execute(
        """
        INSERT INTO clips (id, game_id, game_date, play_index, period, description,
          action_type, shot_subtype, outcome, video_start_s, video_end_s)
        VALUES (1, 1, DATE '2026-01-01', 1, 1, 'FICTIONAL dirty',
          NULL, NULL, NULL, 1, 4)
        """
    )


def test_0003_failure_rolls_back_transaction(postgres_url):
    """Actual ADD CHECK failure after 0003 DDL, not a pre-SQL monkeypatch."""
    import psycopg
    from db.migrate import apply_migrations

    os.environ["APP_ENV"] = "test"
    url = _fresh_db(postgres_url, "nba_txn_fail")
    with psycopg.connect(url, autocommit=True) as conn:
        _install_dirty_0001_like(conn, duration_sql="-1")
    with pytest.raises(Exception):
        apply_migrations(url)
    with psycopg.connect(url) as conn:
        assert conn.execute("SELECT to_regclass('public.teams')").fetchone()[0]
        assert conn.execute("SELECT duration_s FROM games").fetchone()[0] == -1
        cols = {
            r[0]
            for r in conn.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'games'
                """
            )
        }
        assert "source_kind" not in cols
        assert conn.execute("SELECT to_regclass('public.play_capability')").fetchone()[0] is None
        assert conn.execute(
            "SELECT to_regprocedure('public.nba_numeric_is_finite(numeric)')"
        ).fetchone()[0] is None
        stamped = conn.execute("SELECT to_regclass('public.schema_migrations')").fetchone()[0]
        if stamped:
            assert conn.execute("SELECT version FROM schema_migrations").fetchall() == []


def test_dirty_nan_duration_baseline_refused_and_preserved(postgres_url):
    import psycopg
    from db.migrate import apply_migrations

    os.environ["APP_ENV"] = "test"
    url = _fresh_db(postgres_url, "nba_nan_dur")
    with psycopg.connect(url, autocommit=True) as conn:
        _install_dirty_0001_like(conn, duration_sql="'NaN'::numeric")
    with pytest.raises(Exception):
        apply_migrations(url)
    with psycopg.connect(url) as conn:
        nan_eq = conn.execute("SELECT duration_s = 'NaN'::numeric FROM games").fetchone()[0]
        assert nan_eq is True
        assert conn.execute("SELECT to_regclass('public.play_capability')").fetchone()[0] is None
        stamped = conn.execute("SELECT to_regclass('public.schema_migrations')").fetchone()[0]
        if stamped:
            assert conn.execute("SELECT version FROM schema_migrations").fetchall() == []


def test_pg_numeric_nan_equals_itself_helper_rejects(conn):
    nan_eq = conn.execute("SELECT 'NaN'::numeric = 'NaN'::numeric AS v").fetchone()["v"]
    assert nan_eq is True
    nan_gt = conn.execute("SELECT 'NaN'::numeric > 1e18 AS v").fetchone()["v"]
    assert nan_gt is True
    old_idiom = conn.execute(
        "SELECT ('NaN'::numeric = 'NaN'::numeric AND 'NaN'::numeric > 0) AS v"
    ).fetchone()["v"]
    assert old_idiom is True
    helper = conn.execute("SELECT nba_numeric_is_finite('NaN'::numeric) AS v").fetchone()["v"]
    assert helper is False
    helper_pos = conn.execute(
        "SELECT (nba_numeric_is_finite('NaN'::numeric) AND 'NaN'::numeric > 0) AS v"
    ).fetchone()["v"]
    assert helper_pos is False
    assert conn.execute("SELECT nba_numeric_is_finite(48::numeric) AS v").fetchone()["v"] is True


def test_duration_nan_inf_zero_rejected_on_games(conn):
    gid = conn.execute(
        "SELECT id FROM games WHERE bbref_id = 'fic000001FIC'"
    ).fetchone()["id"]
    for value in ("'NaN'::numeric", "'Infinity'::numeric", "0", "-1"):
        with pytest.raises(Exception):
            conn.execute(f"UPDATE games SET duration_s = {value} WHERE id = %s", (gid,))
        conn.rollback()


def test_preexisting_columns_permissive_view_missing_constraints(postgres_url):
    import psycopg
    from db.migrate import SCHEMA_DIR, _exec_file, apply_migrations, _constraint_names, _play_capability_viewdef

    os.environ["APP_ENV"] = "test"
    url = _fresh_db(postgres_url, "nba_perm_view")
    with psycopg.connect(url, autocommit=True) as conn:
        _exec_file(conn, SCHEMA_DIR / "0001_init.sql")
        conn.execute(
            """
            DO $$ BEGIN
              CREATE TYPE timing_provenance AS ENUM (
                'none', 'game_clock_derived', 'unvalidated_numeric', 'matcher_attested'
              );
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$
            """
        )
        conn.execute(
            """
            DO $$ BEGIN
              CREATE TYPE source_kind AS ENUM ('original_private', 'public', 'derived');
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$
            """
        )
        conn.execute(
            """
            ALTER TABLE games
              ADD COLUMN source_kind source_kind,
              ADD COLUMN source_version text,
              ADD COLUMN is_fictional boolean NOT NULL DEFAULT false
            """
        )
        conn.execute(
            """
            ALTER TABLE clips
              ADD COLUMN timing_provenance timing_provenance NOT NULL DEFAULT 'none',
              ADD COLUMN matcher_run_id text,
              ADD COLUMN matcher_version text,
              ADD COLUMN video_timestamp_s numeric,
              ADD COLUMN matched_source_version text
            """
        )
        conn.execute(
            """
            CREATE VIEW play_capability AS
            SELECT id AS play_id, true AS can_create, NULL::text AS reason
            FROM clips
            """
        )
        assert "clips_video_finite_window" not in _constraint_names(conn, "clips")
        viewdef = _play_capability_viewdef(conn)
        assert "nba_numeric_is_finite" not in viewdef
    applied = apply_migrations(url)
    assert "0003_on_demand" in applied
    with psycopg.connect(url) as conn:
        names = _constraint_names(conn, "clips")
        assert "clips_video_finite_window" in names
        assert "clips_none_requires_null_offsets" in names
        assert "clips_attested_requires_binding" in names
        games_names = _constraint_names(conn, "games")
        assert "games_duration_finite_positive" in games_names
        viewdef = _play_capability_viewdef(conn)
        assert "nba_numeric_is_finite" in viewdef
        assert "true AS can_create" not in viewdef.lower()


def test_stamped_0003_altered_bodies_are_reapplied(postgres_url):
    """Same-name CHECK (true), permissive function, and marker-literal view are not enough."""
    import psycopg
    from db.migrate import apply_migrations, _constraintdef, _functiondef, _play_capability_viewdef

    os.environ["APP_ENV"] = "test"
    url = _fresh_db(postgres_url, "nba_drift_body")
    apply_migrations(url)
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("ALTER TABLE clips DROP CONSTRAINT clips_video_finite_window")
        conn.execute(
            "ALTER TABLE clips ADD CONSTRAINT clips_video_finite_window CHECK (true)"
        )
        conn.execute(
            """
            CREATE OR REPLACE FUNCTION nba_numeric_is_finite(x numeric) RETURNS boolean
            LANGUAGE sql IMMUTABLE AS $$ SELECT true $$
            """
        )
        conn.execute("DROP VIEW play_capability")
        conn.execute(
            """
            CREATE VIEW play_capability AS
            SELECT id AS play_id,
                   true AS can_create,
                   'nba_numeric_is_finite duration_s matcher_attested fixtures/% is_fictional'::text AS reason
            FROM clips
            """
        )
        assert " ".join(_constraintdef(conn, "clips", "clips_video_finite_window").lower().split()) == "check (true)"
        assert "NaN" not in _functiondef(conn)
        assert "true AS can_create" in _play_capability_viewdef(conn)
    apply_migrations(url)
    with psycopg.connect(url) as conn:
        window = _constraintdef(conn, "clips", "clips_video_finite_window")
        assert "nba_numeric_is_finite" in window
        assert "check (true)" not in " ".join(window.lower().split())
        fn = _functiondef(conn)
        assert "NaN" in fn and "Infinity" in fn
        assert conn.execute("SELECT nba_numeric_is_finite('NaN'::numeric)").fetchone()[0] is False
        viewdef = _play_capability_viewdef(conn)
        assert "nba_numeric_is_finite(" in " ".join(viewdef.lower().split()).replace(" (", "(")
        assert not re.search(r"(?<!is )true\s+as\s+can_create\b", viewdef, re.I)
        versions = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
        assert "0003_on_demand" in versions
        assert "(video_end_s - video_start_s) <= 120" in " ".join(window.lower().split()).replace(" (", "(")


def test_verify_denies_malicious_comment_helper_and_missing_view_bound(postgres_url):
    """(a) Standalone _verify_0003 refuses token-comment helper and missing VIEW
    <=120 without reapply. Full apply_migrations then restores canonical bodies.
    These are distinct: reapply overwrites from 0003 SQL; verify must still deny
    a live tampered body if called alone.
    """
    import psycopg
    from db.migrate import (
        MigrationRefused,
        _0003_definition_mismatch_reasons,
        _functiondef,
        _verify_0003,
        apply_migrations,
        _play_capability_viewdef,
    )

    os.environ["APP_ENV"] = "test"
    url = _fresh_db(postgres_url, "nba_verify_semantics")
    apply_migrations(url)
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            """
            CREATE OR REPLACE FUNCTION nba_numeric_is_finite(x numeric) RETURNS boolean
            LANGUAGE sql IMMUTABLE STRICT AS $$
              SELECT true -- NaN Infinity -Infinity
            $$
            """
        )
        reasons = _0003_definition_mismatch_reasons(conn)
        assert any("nba_numeric_is_finite" in r for r in reasons)
        assert conn.execute("SELECT nba_numeric_is_finite('NaN'::numeric)").fetchone()[0] is True
        with pytest.raises(MigrationRefused, match="nba_numeric_is_finite"):
            _verify_0003(conn)

        apply_migrations(url)
        assert conn.execute("SELECT nba_numeric_is_finite('NaN'::numeric)").fetchone()[0] is False
        restored_fn = _functiondef(conn)
        assert "NaN" in restored_fn and "Infinity" in restored_fn
        assert "SELECT true -- NaN Infinity -Infinity" not in restored_fn

        conn.execute("DROP VIEW play_capability")
        conn.execute(
            """
            CREATE VIEW play_capability AS
            SELECT
              c.id AS play_id,
              (
                g.source_kind = 'original_private'
                AND c.timing_provenance = 'matcher_attested'
                AND nba_numeric_is_finite(c.video_start_s) IS TRUE
                AND nba_numeric_is_finite(c.video_end_s) IS TRUE
                AND nba_numeric_is_finite(g.duration_s) IS TRUE
                AND g.duration_s > 0
                AND c.video_start_s < g.duration_s
                AND c.video_end_s <= g.duration_s
                AND g.is_fictional IS TRUE
                AND g.s3_key LIKE 'fixtures/%'
              ) IS TRUE AS can_create,
              NULL::text AS reason
            FROM clips c
            JOIN games g ON g.id = c.game_id
            """
        )
        viewdef = _play_capability_viewdef(conn)
        assert "(c.video_end_s - c.video_start_s) <= 120" not in " ".join(viewdef.lower().split())
        reasons = _0003_definition_mismatch_reasons(conn)
        assert any("<= 120" in r for r in reasons)
        with pytest.raises(MigrationRefused, match="<= 120"):
            _verify_0003(conn)

        apply_migrations(url)
        restored_view = " ".join(_play_capability_viewdef(conn).lower().split())
        assert "(c.video_end_s - c.video_start_s) <= 120" in restored_view.replace(" (", "(")
        _verify_0003(conn)


def test_down_preflight_failure_preserves_view_constraints_stamp(postgres_url):
    import psycopg
    from db.migrate import SCHEMA_DIR, _constraint_names, _split_sql, apply_migrations, apply_seed

    os.environ["APP_ENV"] = "test"
    url = _fresh_db(postgres_url, "nba_down_fail")
    apply_migrations(url)
    apply_seed(url)
    down = (SCHEMA_DIR / "down" / "0003_on_demand.down.sql").read_text(encoding="utf-8")
    with psycopg.connect(url, autocommit=True) as conn:
        failed = False
        try:
            with conn.cursor() as cur:
                for stmt in _split_sql(down):
                    cur.execute(stmt)
        except Exception:
            failed = True
        assert failed
    with psycopg.connect(url) as conn:
        assert conn.execute("SELECT to_regclass('public.play_capability')").fetchone()[0]
        versions = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
        assert "0003_on_demand" in versions
        assert "clips_video_finite_window" in _constraint_names(conn, "clips")
        assert "games_duration_finite_positive" in _constraint_names(conn, "games")
        assert conn.execute("SELECT count(*) FROM clips").fetchone()[0] >= 8


def test_advisory_lock_serializes_apply_migrations(postgres_url):
    import threading
    import psycopg
    from db.migrate import MIGRATION_ADVISORY_LOCK_KEY, apply_migrations

    os.environ["APP_ENV"] = "test"
    url = _fresh_db(postgres_url, "nba_lock")
    apply_migrations(url)
    holder = psycopg.connect(url)
    holder.autocommit = False
    holder.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_ADVISORY_LOCK_KEY,))
    finished = threading.Event()
    errors: list[BaseException] = []

    def other():
        try:
            apply_migrations(url)
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()

    thread = threading.Thread(target=other)
    thread.start()
    thread.join(0.4)
    assert not finished.is_set()
    holder.rollback()
    holder.close()
    thread.join(10)
    assert finished.is_set()
    assert errors == []


def test_0001_rejects_nonpositive_or_nan_duration(postgres_url):
    import psycopg
    from db.migrate import SCHEMA_DIR, _exec_file

    os.environ["APP_ENV"] = "test"
    url = _fresh_db(postgres_url, "nba_0001_dur")
    with psycopg.connect(url, autocommit=True) as conn:
        _exec_file(conn, SCHEMA_DIR / "0001_init.sql")
        conn.execute(
            "INSERT INTO teams (abbr, city, name) VALUES ('AAA', 'A', 'A'), ('BBB', 'B', 'B')"
        )
        ids = [r[0] for r in conn.execute("SELECT id FROM teams ORDER BY id")]
        for i, duration in enumerate(("'NaN'::numeric", "0", "-2", "'Infinity'::numeric")):
            with pytest.raises(Exception):
                conn.execute(
                    f"""
                    INSERT INTO games (bbref_id, season, game_date, home_team_id, away_team_id,
                      duration_s)
                    VALUES ('ficdur{i}FIC', '2025-26', DATE '2026-01-01', %s, %s, {duration})
                    """,
                    (ids[0], ids[1]),
                )
