"""Isolated local persist: original-event rows, shared sequence timing."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from match_play_timestamps import load_pbp_source, stamp_original_play_indexes
from persist_matcher_clips import PersistRefused, persist_matched_output

pytestmark = pytest.mark.postgres

def _postgres_bin() -> str:
    for candidate in ("/usr/lib/postgresql/16/bin", "/usr/local/bin", "/opt/homebrew/bin"):
        if os.path.isfile(os.path.join(candidate, "initdb")):
            return candidate
    pytest.fail("PostgreSQL initdb is required for designated DB tests")


BIN = _postgres_bin()
SCHEMA = Path(__file__).with_name("schema_dev_clips.sql")
GAME = "202601010FIC"


def _stored(inserted, noop, linked=0, skipped=0):
    return {
        "inserted": inserted,
        "noop": noop,
        "players_linked": linked,
        "players_skipped": skipped,
    }


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(autouse=True)
def _persist_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    for name in (
        "PGHOST",
        "PGHOSTADDR",
        "PGPORT",
        "PGDATABASE",
        "PGUSER",
        "PGSERVICE",
        "PGSERVICEFILE",
        "PGOPTIONS",
        "PGSYSCONFDIR",
        "PGTARGETSESSIONATTRS",
        "PGLOADBALANCEHOSTS",
        "PGREQUIRESSL",
        "PGSSLMODE",
    ):
        monkeypatch.delenv(name, raising=False)


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
    server_env = os.environ.copy()
    # Homebrew Postgres on macOS aborts if it becomes multithreaded during startup
    # without a valid locale. The child server is the only process that needs this.
    server_env["LC_ALL"] = "en_US.UTF-8"
    server_env["LANG"] = "en_US.UTF-8"
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
        env=server_env,
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
    import psycopg

    psql = os.path.join(BIN, "psql")
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


def _pad_slot(index: int) -> dict[str, Any]:
    remaining = 12 * 60 - index * 10
    quarter = 1
    while remaining < 0:
        quarter += 1
        remaining += 12 * 60
    minutes, sec = divmod(remaining, 60)
    return {
        "quarter": quarter,
        "time": f"{minutes}:{sec:02d}.0",
        "team": "FIC",
        "description": f"pad event {index}",
    }


def _frozen(members):
    max_index = max(m["play_index"] for m in members)
    by_index = {m["play_index"]: m for m in members}
    plays = []
    for index in range(max_index + 1):
        item = by_index.get(index)
        if item is None:
            plays.append(_pad_slot(index))
        else:
            plays.append(
                {
                    "quarter": item["quarter"],
                    "time": item["time"],
                    "team": item["team"],
                    "description": item["description"],
                }
            )
    data = json.dumps(plays).encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()
    stamped = stamp_original_play_indexes(json.loads(data.decode("utf-8")))
    return digest, stamped


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


def _persist(url, matched, digest, stamped, game=GAME):
    return persist_matched_output(
        matched,
        database_url=url,
        game_bbref_id=game,
        pbp_hash=digest,
        original_plays=stamped,
    )


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


def test_compound_inserts_one_row_per_original_event_shared_window(postgres_url):
    import psycopg
    from psycopg.rows import dict_row

    _reset(postgres_url)
    digest, stamped = _frozen(MEMBERS)
    report = _persist(postgres_url, [_group(MEMBERS)], digest, stamped)
    assert report == _stored(2, 0, skipped=1)
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
        assert rows[0]["attributes"]["pbp_sha256"] == digest
        assert "grouping_sha256" in rows[0]["attributes"]
        assert rows[0]["attributes"]["grouping_sha256"] == rows[1]["attributes"]["grouping_sha256"]
        assert rows[0]["team_id"] is None
        assert conn.execute("SELECT count(*) FROM clip_players").fetchone()["count"] == 0


def test_player_filter_persists_all_original_members_not_just_named(postgres_url):
    import psycopg
    from psycopg.rows import dict_row

    _reset(postgres_url)
    digest, stamped = _frozen(MEMBERS)
    report = _persist(postgres_url, [_group(MEMBERS)], digest, stamped)
    assert report["inserted"] == 2
    with psycopg.connect(postgres_url, row_factory=dict_row) as conn:
        descriptions = [
            r["description"]
            for r in conn.execute("SELECT description FROM clips ORDER BY play_index")
        ]
        assert descriptions[0].startswith("D. White misses")
        assert descriptions[1] == "Offensive rebound by Team"
        assert conn.execute("SELECT count(*) FROM clip_players").fetchone()["count"] == 0


def test_exact_rerun_is_noop(postgres_url):
    _reset(postgres_url)
    digest, stamped = _frozen(MEMBERS)
    _persist(postgres_url, [_group(MEMBERS)], digest, stamped)
    report = _persist(postgres_url, [_group(MEMBERS)], digest, stamped)
    assert report == _stored(0, 2, skipped=1)


def test_occupied_mismatch_rolls_back_entire_transaction(postgres_url):
    import psycopg

    _reset(postgres_url)
    other = {
        "play_index": 9,
        "quarter": 2,
        "time": "1:00.0",
        "team": "76ers",
        "description": "T. Maxey makes 3-pt jump shot",
    }
    digest, stamped = _frozen(MEMBERS + [other])
    _persist(postgres_url, [_group(MEMBERS)], digest, stamped)
    with pytest.raises(PersistRefused, match="occupied play_index"):
        _persist(
            postgres_url,
            [_group(MEMBERS, start=40, end=46), _group([other], start=20, end=26)],
            digest,
            stamped,
        )
    with psycopg.connect(postgres_url) as conn:
        indexes = [r[0] for r in conn.execute("SELECT play_index FROM clips ORDER BY play_index")]
        assert indexes == [2, 3]


def test_missing_parent_game_refuses(postgres_url):
    _reset(postgres_url)
    digest, stamped = _frozen(MEMBERS)
    with pytest.raises(PersistRefused, match="parent game is missing"):
        _persist(
            postgres_url,
            [_group(MEMBERS)],
            digest,
            stamped,
            game="202601010ZZZ",
        )


def test_changed_pbp_overlapping_keys_refuses(postgres_url):
    import psycopg

    _reset(postgres_url)
    digest, stamped = _frozen(MEMBERS)
    _persist(postgres_url, [_group(MEMBERS)], digest, stamped)
    revised = [dict(m) for m in MEMBERS]
    revised[0] = dict(revised[0], description=revised[0]["description"] + " revised")
    other_digest, other_stamped = _frozen(revised)
    assert other_digest != digest
    with pytest.raises(PersistRefused, match="saved PBP does not match"):
        _persist(postgres_url, [_group(MEMBERS)], other_digest, stamped)
    with psycopg.connect(postgres_url) as conn:
        indexes = [r[0] for r in conn.execute("SELECT play_index FROM clips ORDER BY play_index")]
        assert indexes == [2, 3]


def test_changed_pbp_disjoint_index0_then_index5_refuses_zero_inserts(postgres_url):
    import psycopg

    _reset(postgres_url)
    v1 = {
        "play_index": 0,
        "quarter": 1,
        "time": "11:00.0",
        "team": "FIC",
        "description": "V1 event at index 0",
    }
    v1_digest, v1_stamped = _frozen([v1])
    report = _persist(postgres_url, [_group([v1])], v1_digest, v1_stamped)
    assert report == _stored(1, 0)

    v2 = {
        "play_index": 5,
        "quarter": 2,
        "time": "5:00.0",
        "team": "QAF",
        "description": "V2 event at index 5",
    }
    v2_digest, v2_stamped = _frozen([v2])
    assert v2_digest != v1_digest
    with pytest.raises(PersistRefused, match="saved PBP does not match"):
        _persist(postgres_url, [_group([v2], start=20, end=26)], v2_digest, v2_stamped)
    with psycopg.connect(postgres_url) as conn:
        rows = conn.execute("SELECT play_index, attributes FROM clips ORDER BY play_index").fetchall()
        assert [r[0] for r in rows] == [0]
        assert rows[0][1]["pbp_sha256"] == v1_digest


def test_same_source_same_grouping_incremental_disjoint_selection_inserts(postgres_url):
    """Player-filter selected subset is not group identity; extra canonical groups may insert."""
    import psycopg

    _reset(postgres_url)
    later = {
        "play_index": 9,
        "quarter": 2,
        "time": "1:00.0",
        "team": "76ers",
        "description": "T. Maxey makes 3-pt jump shot",
    }
    digest, stamped = _frozen(MEMBERS + [later])
    assert _persist(postgres_url, [_group(MEMBERS)], digest, stamped) == _stored(2, 0, skipped=1)
    assert _persist(postgres_url, [_group([later], start=20, end=26)], digest, stamped) == _stored(
        1, 0, skipped=1
    )
    with psycopg.connect(postgres_url) as conn:
        indexes = [r[0] for r in conn.execute("SELECT play_index FROM clips ORDER BY play_index")]
        hashes = {r[0]["pbp_sha256"] for r in conn.execute("SELECT attributes FROM clips")}
        groups = {r[0]["grouping_sha256"] for r in conn.execute("SELECT attributes FROM clips")}
        assert indexes == [2, 3, 9]
        assert hashes == {digest}
        assert len(groups) == 1


def test_changed_full_grouping_disjoint_keys_refuses(postgres_url):
    import psycopg

    _reset(postgres_url)
    later = {
        "play_index": 9,
        "quarter": 2,
        "time": "1:00.0",
        "team": "76ers",
        "description": "T. Maxey makes 3-pt jump shot",
    }
    digest, stamped = _frozen(MEMBERS + [later])
    assert _persist(postgres_url, [_group(MEMBERS)], digest, stamped) == _stored(2, 0, skipped=1)
    regrouped = [dict(play) for play in stamped]
    regrouped[3] = dict(regrouped[3], time="1:11.0")
    with pytest.raises(PersistRefused, match="canonical grouping"):
        _persist(postgres_url, [_group([later], start=20, end=26)], digest, regrouped)
    with psycopg.connect(postgres_url) as conn:
        indexes = [r[0] for r in conn.execute("SELECT play_index FROM clips ORDER BY play_index")]
        assert indexes == [2, 3]


def test_selected_singleton_refuses_when_canonical_group_is_compound(postgres_url):
    _reset(postgres_url)
    digest, stamped = _frozen(MEMBERS)
    with pytest.raises(PersistRefused, match="canonical full-PBP grouping"):
        _persist(postgres_url, [_group([MEMBERS[0]])], digest, stamped)


def test_existing_clip_missing_hash_refuses_disjoint_insert(postgres_url):
    import psycopg

    _reset(postgres_url)
    other = {
        "play_index": 9,
        "quarter": 2,
        "time": "1:00.0",
        "team": "76ers",
        "description": "T. Maxey makes 3-pt jump shot",
    }
    digest, stamped = _frozen(MEMBERS + [other])
    _persist(postgres_url, [_group(MEMBERS)], digest, stamped)
    with psycopg.connect(postgres_url, autocommit=True) as conn:
        conn.execute("UPDATE clips SET attributes = '{}'::jsonb")
    with pytest.raises(PersistRefused, match="saved PBP does not match"):
        _persist(postgres_url, [_group([other], start=20, end=26)], digest, stamped)
    with psycopg.connect(postgres_url) as conn:
        indexes = [r[0] for r in conn.execute("SELECT play_index FROM clips ORDER BY play_index")]
        assert indexes == [2, 3]


def test_file_mutation_after_load_persists_frozen_hash(postgres_url, tmp_path):
    import psycopg
    from psycopg.rows import dict_row

    _reset(postgres_url)
    path = tmp_path / "202601010FIC.json"
    raw = [
        {"quarter": 1, "time": "11:00.0", "team": "FIC", "description": "loaded event"},
    ]
    path.write_bytes(json.dumps(raw).encode("utf-8"))
    digest, stamped = load_pbp_source(str(path))
    path.write_bytes(
        json.dumps(
            raw + [{"quarter": 1, "time": "10:00.0", "team": "FIC", "description": "mutated after load"}]
        ).encode("utf-8")
    )
    assert digest != hashlib.sha256(path.read_bytes()).hexdigest()
    report = _persist(postgres_url, [_group([stamped[0]])], digest, stamped)
    assert report == _stored(1, 0)
    with psycopg.connect(postgres_url, row_factory=dict_row) as conn:
        row = conn.execute("SELECT attributes, description FROM clips").fetchone()
        assert row["attributes"]["pbp_sha256"] == digest
        assert row["description"] == "loaded event"


def test_concurrent_changed_grouping_serialized(postgres_url):
    import psycopg

    _reset(postgres_url)
    first = {
        "play_index": 0,
        "quarter": 1,
        "time": "11:00.0",
        "team": "FIC",
        "description": "concurrent grouping index 0",
    }
    later = {
        "play_index": 5,
        "quarter": 2,
        "time": "5:00.0",
        "team": "QAF",
        "description": "concurrent grouping index 5",
    }
    digest, plays_a = _frozen([first, later])
    plays_b = [dict(play) for play in plays_a]
    plays_b[1] = dict(plays_b[1], time="10:58.0")
    start = threading.Barrier(2)
    outcomes = []
    lock = threading.Lock()

    def run(group_members, plays, start_s):
        start.wait(timeout=10)
        try:
            report = _persist(
                postgres_url,
                [_group(group_members, start=start_s, end=start_s + 6)],
                digest,
                plays,
            )
            with lock:
                outcomes.append(("ok", report, plays))
        except PersistRefused as exc:
            with lock:
                outcomes.append(("refuse", str(exc), plays))
        except Exception as exc:
            with lock:
                outcomes.append(("error", str(exc), plays))

    t1 = threading.Thread(target=run, args=([first], plays_a, 10))
    t2 = threading.Thread(target=run, args=([later], plays_b, 20))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)
    assert not t1.is_alive() and not t2.is_alive()
    statuses = [item[0] for item in outcomes]
    assert statuses.count("ok") == 1
    assert statuses.count("refuse") == 1
    loser = [item for item in outcomes if item[0] == "refuse"][0]
    assert "canonical grouping" in loser[1]
    with psycopg.connect(postgres_url) as conn:
        rows = conn.execute("SELECT play_index, attributes FROM clips ORDER BY play_index").fetchall()
        assert len(rows) == 1
        groupings = {row[1]["grouping_sha256"] for row in rows}
        assert len(groupings) == 1


def test_concurrent_disjoint_v1_v2_serialized(postgres_url):
    import psycopg

    _reset(postgres_url)
    v1 = {
        "play_index": 0,
        "quarter": 1,
        "time": "11:00.0",
        "team": "FIC",
        "description": "concurrent V1 index 0",
    }
    v2 = {
        "play_index": 5,
        "quarter": 2,
        "time": "5:00.0",
        "team": "QAF",
        "description": "concurrent V2 index 5",
    }
    v1_digest, v1_stamped = _frozen([v1])
    v2_digest, v2_stamped = _frozen([v2])
    assert v1_digest != v2_digest
    start = threading.Barrier(2)
    outcomes = []
    lock = threading.Lock()

    def run(group, digest, stamped):
        start.wait(timeout=10)
        try:
            report = _persist(postgres_url, [_group([group], start=10, end=16)], digest, stamped)
            with lock:
                outcomes.append(("ok", report, digest))
        except PersistRefused as exc:
            with lock:
                outcomes.append(("refuse", str(exc), digest))

    t1 = threading.Thread(target=run, args=(v1, v1_digest, v1_stamped))
    t2 = threading.Thread(target=run, args=(v2, v2_digest, v2_stamped))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)
    assert not t1.is_alive() and not t2.is_alive()
    statuses = [item[0] for item in outcomes]
    assert statuses.count("ok") == 1
    assert statuses.count("refuse") == 1
    winner = [item for item in outcomes if item[0] == "ok"][0]
    loser = [item for item in outcomes if item[0] == "refuse"][0]
    assert "saved PBP does not match" in loser[1]
    with psycopg.connect(postgres_url) as conn:
        rows = conn.execute("SELECT play_index, attributes FROM clips ORDER BY play_index").fetchall()
        assert len(rows) == 1
        assert rows[0][1]["pbp_sha256"] == winner[2]
        assert rows[0][0] in {0, 5}


def test_main_stubbed_vision_persists_matcher_output_shape(postgres_url, tmp_path, monkeypatch):
    import psycopg
    from psycopg.rows import dict_row

    from match_play_timestamps import main

    _reset(postgres_url)
    monkeypatch.chdir(tmp_path)
    video = tmp_path / "game.mkv"
    video.write_bytes(b"fake")
    pbp = tmp_path / "202601010FIC.json"
    pbp.write_text(
        json.dumps(
            [
                {
                    "quarter": 1,
                    "time": "10:34.0",
                    "score": "2-4",
                    "team": "Celtics",
                    "description": "D. White misses 3-pt jump shot from 25 ft",
                },
                {
                    "quarter": 1,
                    "time": "10:32.0",
                    "score": "2-4",
                    "team": "Celtics",
                    "description": "Offensive rebound by Team",
                },
            ]
        )
    )
    monkeypatch.setenv("DATABASE_URL", postgres_url)
    monkeypatch.setenv("MATCHER_GAME_BBREF_ID", GAME)

    class Probe:
        stdout = "5.0\n"
        stderr = ""
        returncode = 0

    def fake_run(cmd, **_kwargs):
        if cmd and cmd[0] == "ffprobe":
            return Probe()
        raise AssertionError(cmd)

    monkeypatch.setattr("match_play_timestamps.subprocess.run", fake_run)
    monkeypatch.setattr("match_play_timestamps.extract_frame_at_time", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "match_play_timestamps.analyze_frame_scoreboard",
        lambda *_a, **_k: '{"quarter": 1, "time": "10:34", "score": "2-4"}',
    )
    monkeypatch.setattr(
        "match_play_timestamps.requests.get",
        lambda *_a, **_k: type("R", (), {"status_code": 200})(),
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "match_play_timestamps.py",
            str(video),
            str(pbp),
            "--max-plays",
            "1",
            "--buffer",
            "5",
            "--start-time",
            "0",
            "--sample-interval",
            "10",
            "--output",
            str(tmp_path / "out.json"),
        ],
    )
    main()
    with psycopg.connect(postgres_url, row_factory=dict_row) as conn:
        rows = list(
            conn.execute(
                "SELECT play_index, description, video_start_s, video_end_s "
                "FROM clips ORDER BY play_index"
            )
        )
        assert conn.execute("SELECT count(*) FROM clip_players").fetchone()["count"] == 0
    assert [row["play_index"] for row in rows] == [0, 1]
    assert rows[0]["video_start_s"] == rows[1]["video_start_s"]
    assert rows[0]["video_end_s"] == rows[1]["video_end_s"]
    assert float(rows[0]["video_start_s"]) == 0
    assert float(rows[0]["video_end_s"]) == 1
    assert "White" in rows[0]["description"]
    assert rows[1]["description"] == "Offensive rebound by Team"
    saved = json.loads((tmp_path / "out.json").read_text())
    assert saved[0]["video_start"] == 0
    assert saved[0]["video_end"] == 1
    assert saved[0]["original_play_indexes"] == [0, 1]


def test_links_players_as_each_clip_is_written_and_fills_them_on_rerun(postgres_url):
    import psycopg
    from psycopg.rows import dict_row

    _reset(postgres_url)
    with psycopg.connect(postgres_url, autocommit=True) as conn:
        conn.execute(
            """
            INSERT INTO players (bbref_id, full_name, first_name, last_name, team_id)
            VALUES
              ('okorois01', 'Isaac Okoro', 'Isaac', 'Okoro', (SELECT id FROM teams WHERE abbr = 'FIC')),
              ('giddejo01', 'Josh Giddey', 'Josh', 'Giddey', (SELECT id FROM teams WHERE abbr = 'FIC')),
              ('brownja02', 'Jaylen Brown', 'Jaylen', 'Brown', (SELECT id FROM teams WHERE abbr = 'QAF')),
              ('edgecvj01', 'VJ Edgecombe', 'VJ', 'Edgecombe', (SELECT id FROM teams WHERE abbr = 'QAF')),
              ('smithja04', 'Jalen Smith', 'Jalen', 'Smith', (SELECT id FROM teams WHERE abbr = 'FIC')),
              ('smithja05', 'Jabari Smith Jr.', 'Jabari', 'Smith', (SELECT id FROM teams WHERE abbr = 'QAF'))
            """
        )
    members = [
        {
            "play_index": 0,
            "quarter": 1,
            "time": "12:00.0",
            "team": "FIC",
            "description": "I. Okoro makes 2-pt layup from 1 ft (assist by J. Giddey)",
        },
        {
            "play_index": 1,
            "quarter": 1,
            "time": "11:00.0",
            "team": "FIC",
            "description": "Offensive rebound by I. Okoro",
        },
        {
            "play_index": 2,
            "quarter": 1,
            "time": "10:00.0",
            "team": "QAF",
            "description": "Shooting foul by J. Brown (drawn by I. Okoro)",
        },
        {
            "play_index": 3,
            "quarter": 1,
            "time": "9:00.0",
            "team": "FIC",
            "description": "J. Smith misses 3-pt jump shot from 26 ft",
        },
        {
            "play_index": 4,
            "quarter": 1,
            "time": "8:00.0",
            "team": "FIC",
            "description": "Offensive rebound by Team",
        },
        {
            "play_index": 5,
            "quarter": 1,
            "time": "7:00.0",
            "team": "QAF",
            "description": "V. Edgecombe makes 3-pt jump shot from 25 ft",
        },
        {
            "play_index": 6,
            "quarter": 1,
            "time": "6:00.0",
            "team": "FIC",
            "description": "Turnover by J. Giddey (bad pass; steal by J. Brown)",
        },
    ]
    digest, stamped = _frozen(members)
    matched = [_group([member], start=10 + index, end=16 + index) for index, member in enumerate(members)]
    expected = {
        (0, "Okoro", "shot"),
        (0, "Giddey", "assist"),
        (1, "Okoro", "rebound"),
        (2, "Brown", "foul"),
        (2, "Okoro", "fouled"),
        (5, "Edgecombe", "shot"),
        (6, "Giddey", "turnover"),
        (6, "Brown", "steal"),
    }

    def linked_rows():
        with psycopg.connect(postgres_url, row_factory=dict_row) as conn:
            return {
                (row["play_index"], row["last_name"], row["role"])
                for row in conn.execute(
                    """
                    SELECT c.play_index, p.last_name, cp.role::text AS role
                    FROM clip_players cp
                    JOIN clips c ON c.id = cp.clip_id
                    JOIN players p ON p.id = cp.player_id
                    """
                )
            }

    report = _persist(postgres_url, matched, digest, stamped)
    assert report == _stored(7, 0, linked=8, skipped=1)
    assert linked_rows() == expected
    with psycopg.connect(postgres_url, autocommit=True) as conn:
        conn.execute("DELETE FROM clip_players")
    again = _persist(postgres_url, matched, digest, stamped)
    assert again == _stored(0, 7, linked=8, skipped=1)
    assert linked_rows() == expected
    third = _persist(postgres_url, matched, digest, stamped)
    assert third == _stored(0, 7, linked=0, skipped=1)
    assert linked_rows() == expected


def test_other_neon_host_is_refused_before_connect():
    digest, stamped = _frozen(MEMBERS)
    with pytest.raises(PersistRefused, match="allowlisted"):
        persist_matched_output(
            [_group(MEMBERS)],
            database_url="postgresql://u@ep-odd-smoke-b465tvf8.us-east-2.aws.neon.tech/neondb",
            game_bbref_id=GAME,
            pbp_hash=digest,
            original_plays=stamped,
        )
