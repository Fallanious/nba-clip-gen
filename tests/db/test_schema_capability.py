"""Isolated Postgres: migrate, VIEW, constraints, seed. Never Neon."""

from __future__ import annotations

import os
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
        pytest.skip("local PostgreSQL 16 binaries are not installed")
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


def test_policy_doc_present():
    text = (SQL_ROOT / "POLICY.md").read_text(encoding="utf-8")
    assert "CLIP_MAX_DURATION_S = 120" in text
    assert "VIDEO_SECONDS_ABS_MAX = 1e9" in text
    assert "matcher_attested" in text
    assert "Rollback" in text or "rollback" in text
    assert "game_clock" in text.lower()
