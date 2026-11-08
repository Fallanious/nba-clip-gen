"""Isolated local persist: original-event rows, shared sequence timing."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import time
from datetime import timedelta
from pathlib import Path

import pytest

from persist_matcher_clips import PersistRefused, persist_matched_output

pytestmark = pytest.mark.postgres

BIN = "/usr/lib/postgresql/16/bin"
SCHEMA = Path(__file__).with_name("schema_dev_clips.sql")
GAME = "202601010FIC"


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
        conn.execute("CREATE DATABASE nba_clip_persist_test")
    yield f"postgresql://nba@127.0.0.1:{port}/nba_clip_persist_test"
    proc.terminate()
    proc.wait(timeout=10)


def _reset(url):
    import subprocess

    import psycopg

    psql = "/usr/lib/postgresql/16/bin/psql"
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS clip_players CASCADE")
        conn.execute("DROP TABLE IF EXISTS clips CASCADE")
        conn.execute("DROP TABLE IF EXISTS games CASCADE")
        conn.execute("DROP TABLE IF EXISTS players CASCADE")
        conn.execute("DROP TABLE IF EXISTS teams CASCADE")
        conn.execute("DROP TYPE IF EXISTS clip_role CASCADE")
        conn.execute("DROP TYPE IF EXISTS clip_status CASCADE")
    subprocess.check_call(
        [psql, url, "-v", "ON_ERROR_STOP=1", "-f", str(SCHEMA)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO teams (abbr, city, name) VALUES ('FIC', 'Fiction', 'Samples'), ('QAF', 'QA', 'Fixtures')"
        )
        conn.execute(
            """
            INSERT INTO games (bbref_id, season, game_date, home_team_id, away_team_id)
            VALUES (
              %s, '2025-26', DATE '2026-01-01',
              (SELECT id FROM teams WHERE abbr = 'FIC'),
              (SELECT id FROM teams WHERE abbr = 'QAF')
            )
            """,
            (GAME,),
        )


def _pbp(tmp_path, plays):
    path = tmp_path / "202601010FIC.json"
    path.write_text(json.dumps(plays), encoding="utf-8")
    return str(path)


def _group(members, start=10.0, end=16.0):
    return {
        "play_index": members[0]["play_index"],
        "original_play_indexes": [m["play_index"] for m in members],
        "original_plays": members,
        "video_start": start,
        "video_end": end,
        "description": " → ".join(m["description"] for m in members),
        "is_compound": len(members) > 1,
    }


MEMBERS = [
    {
        "play_index": 2,
        "quarter": 1,
        "time": "10:34.0",
        "team": "Celtics",
        "description": "D. White misses 3-pt jump shot from 25 ft",
    },
    {
        "play_index": 3,
        "quarter": 1,
        "time": "10:32.0",
        "team": "Celtics",
        "description": "Offensive rebound by Team",
    },
]


def test_compound_inserts_one_row_per_original_event_shared_window(postgres_url, tmp_path):
    import psycopg
    from psycopg.rows import dict_row

    _reset(postgres_url)
    pbp = _pbp(tmp_path, [{"quarter": 1, "time": "11:00.0", "description": "x"}])
    report = persist_matched_output(
        [_group(MEMBERS)],
        database_url=postgres_url,
        game_bbref_id=GAME,
        pbp_path=pbp,
    )
    assert report == {"inserted": 2, "noop": 0}
    with psycopg.connect(postgres_url, row_factory=dict_row) as conn:
        rows = conn.execute(
            """
            SELECT play_index, period, description, game_clock, video_start_s, video_end_s,
                   duration_s, attributes, team_id
            FROM clips ORDER BY play_index
            """
        ).fetchall()
        assert [r["play_index"] for r in rows] == [2, 3]
        assert rows[0]["video_start_s"] == rows[1]["video_start_s"]
        assert rows[0]["video_end_s"] == rows[1]["video_end_s"]
        assert float(rows[0]["duration_s"]) == float(rows[0]["video_end_s"] - rows[0]["video_start_s"])
        assert rows[0]["description"].startswith("D. White misses")
        assert "→" not in rows[0]["description"]
        assert rows[1]["description"] == "Offensive rebound by Team"
        assert rows[0]["game_clock"] == timedelta(minutes=10, seconds=34)
        assert rows[1]["game_clock"] == timedelta(minutes=10, seconds=32)
        assert rows[0]["attributes"]["shared_sequence_indexes"] == [2, 3]
        assert rows[0]["team_id"] is None
        assert conn.execute("SELECT count(*) FROM clip_players").fetchone()["count"] == 0


def test_exact_rerun_is_noop(postgres_url, tmp_path):
    _reset(postgres_url)
    pbp = _pbp(tmp_path, [{"quarter": 1, "time": "11:00.0", "description": "x"}])
    persist_matched_output(
        [_group(MEMBERS)],
        database_url=postgres_url,
        game_bbref_id=GAME,
        pbp_path=pbp,
    )
    report = persist_matched_output(
        [_group(MEMBERS)],
        database_url=postgres_url,
        game_bbref_id=GAME,
        pbp_path=pbp,
    )
    assert report == {"inserted": 0, "noop": 2}


def test_occupied_mismatch_rolls_back_entire_transaction(postgres_url, tmp_path):
    import psycopg

    _reset(postgres_url)
    pbp = _pbp(tmp_path, [{"quarter": 1, "time": "11:00.0", "description": "x"}])
    persist_matched_output(
        [_group(MEMBERS)],
        database_url=postgres_url,
        game_bbref_id=GAME,
        pbp_path=pbp,
    )
    other = {
        "play_index": 9,
        "quarter": 2,
        "time": "1:00.0",
        "team": "76ers",
        "description": "T. Maxey makes 3-pt jump shot",
    }
    changed = dict(MEMBERS[0])
    changed["description"] = "Different play at the same index"
    with pytest.raises(PersistRefused, match="occupied play_index"):
        persist_matched_output(
            [_group([changed, MEMBERS[1]]), _group([other], start=20, end=26)],
            database_url=postgres_url,
            game_bbref_id=GAME,
            pbp_path=pbp,
        )
    with psycopg.connect(postgres_url) as conn:
        indexes = [r[0] for r in conn.execute("SELECT play_index FROM clips ORDER BY play_index")]
        assert indexes == [2, 3]


def test_missing_parent_game_refuses(postgres_url, tmp_path):
    _reset(postgres_url)
    pbp = _pbp(tmp_path, [])
    with pytest.raises(PersistRefused, match="parent game is missing"):
        persist_matched_output(
            [_group(MEMBERS)],
            database_url=postgres_url,
            game_bbref_id="202601010ZZZ",
            pbp_path=pbp,
        )


def test_changed_pbp_hash_is_occupied_mismatch(postgres_url, tmp_path):
    _reset(postgres_url)
    pbp = _pbp(tmp_path, [{"quarter": 1, "time": "11:00.0", "description": "x"}])
    persist_matched_output(
        [_group(MEMBERS)],
        database_url=postgres_url,
        game_bbref_id=GAME,
        pbp_path=pbp,
    )
    other = _pbp(tmp_path, [{"quarter": 1, "time": "11:00.0", "description": "revised scrape"}])
    with pytest.raises(PersistRefused, match="occupied play_index"):
        persist_matched_output(
            [_group(MEMBERS)],
            database_url=postgres_url,
            game_bbref_id=GAME,
            pbp_path=other,
        )


def test_neon_host_is_refused(tmp_path):
    pbp = _pbp(tmp_path, [])
    with pytest.raises(PersistRefused, match="Neon/live"):
        persist_matched_output(
            [_group(MEMBERS)],
            database_url="postgresql://u@ep-odd-smoke-b465tvf8.us-east-2.aws.neon.tech/neondb",
            game_bbref_id=GAME,
            pbp_path=pbp,
        )
