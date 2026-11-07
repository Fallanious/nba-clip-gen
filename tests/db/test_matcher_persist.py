"""Isolated Postgres matcher persist. Fictional fixtures only. Never Neon/S3."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

from db.migrate import apply_migrations
from db.matcher_persist import MATCHER_VERSION, PersistConfig, PersistRefused, persist_pbp_and_matcher
from db.sql_loader import load_query_sql
from test_schema_capability import postgres_url

pytestmark = pytest.mark.postgres

FIXTURES = Path("tests/fixtures/plays")
ANALYZED_KEY = "fixtures/fictional-persist.mkv"


def _sha(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


V1 = _sha("ficpersist01-v1")
V2 = _sha("ficpersist01-v2")


def _url(postgres_url, name):
    import psycopg

    admin = postgres_url.rsplit("/", 1)[0] + "/postgres"
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {name}")
        c.execute(f"CREATE DATABASE {name}")
    return postgres_url.rsplit("/", 1)[0] + f"/{name}"


def _setup_game(url, *, source_version=V1, duration_s=48):
    import psycopg
    from psycopg.rows import dict_row

    os.environ["APP_ENV"] = "test"
    apply_migrations(url)
    with psycopg.connect(url, row_factory=dict_row) as conn:
        conn.execute(
            "INSERT INTO teams (abbr, city, name) VALUES ('FIC', 'Fiction', 'Samples'), ('QAF', 'QA', 'Fixtures')"
        )
        conn.execute(
            """
            INSERT INTO players (bbref_id, full_name, first_name, last_name, team_id)
            SELECT 'ficcas01', 'Casey Fixture', 'Casey', 'Fixture', id FROM teams WHERE abbr = 'FIC'
            """
        )
        conn.execute(
            """
            INSERT INTO players (bbref_id, full_name, first_name, last_name, team_id)
            SELECT 'ficoth01', 'Other Fixture', 'Other', 'Fixture', id FROM teams WHERE abbr = 'QAF'
            """
        )
        conn.execute(
            """
            INSERT INTO games (
              bbref_id, season, game_date, home_team_id, away_team_id,
              s3_key, duration_s, source_kind, source_version, is_fictional
            )
            SELECT
              'ficpersist01', '2025-26', DATE '2026-01-03', h.id, a.id,
              %s, %s, 'original_private', %s, true
            FROM teams h, teams a
            WHERE h.abbr = 'FIC' AND a.abbr = 'QAF'
            """,
            (ANALYZED_KEY, duration_s, source_version),
        )
        conn.commit()
    return url


@pytest.fixture
def persist_url(postgres_url):
    return _setup_game(_url(postgres_url, "nba_persist"))


def _cfg(url, **overrides):
    values = dict(
        database_url=url,
        game_bbref_id="ficpersist01",
        commit=True,
    )
    values.update(overrides)
    return PersistConfig(**values)


def _out(plays, *, identity=V1, key=ANALYZED_KEY, run_id="fic-persist-run", kind="content_sha256"):
    return {
        "analyzed_source": {
            "analyzed_source_key": key,
            "analyzed_identity_kind": kind,
            "analyzed_source_identity": identity,
            "matcher_run_id": run_id,
        },
        "matcher_run_id": run_id,
        "matcher_version": MATCHER_VERSION,
        "plays": plays,
    }


def _payload():
    pbp = json.loads((FIXTURES / "persist_pbp_fictional.json").read_text())
    matcher = json.loads((FIXTURES / "persist_matcher_condensed_fictional.json").read_text())
    return pbp, matcher


def _cap(conn, play_index):
    game_id = conn.execute("SELECT id FROM games WHERE bbref_id = 'ficpersist01'").fetchone()["id"]
    return conn.execute(
        load_query_sql("persist/select_play_capability"),
        {"game_id": game_id, "play_index": play_index},
    ).fetchone()


def _clip_snapshot(conn):
    rows = conn.execute(
        """
        SELECT play_index, description, game_clock, timing_provenance, video_start_s, video_end_s,
               matched_source_version, matcher_run_id
        FROM clips ORDER BY play_index
        """
    ).fetchall()
    players = conn.execute(
        "SELECT clip_id, player_id, role FROM clip_players ORDER BY clip_id, player_id, role"
    ).fetchall()
    return rows, players


def test_persist_maps_pbp_index_not_condensed(persist_url):
    import psycopg
    from psycopg.rows import dict_row

    pbp, matcher = _payload()
    report = persist_pbp_and_matcher(pbp, _out(matcher), _cfg(persist_url))
    assert report.dry_run is False
    assert report.attested == 1
    assert report.unmatched == 4
    assert report.source_version == V1
    with psycopg.connect(persist_url, row_factory=dict_row) as conn:
        zero = conn.execute(
            "SELECT timing_provenance, video_start_s FROM clips WHERE play_index = 0"
        ).fetchone()
        two = conn.execute(
            "SELECT timing_provenance, video_start_s, video_end_s, matched_source_version FROM clips WHERE play_index = 2"
        ).fetchone()
        assert zero["timing_provenance"] == "none"
        assert zero["video_start_s"] is None
        assert two["timing_provenance"] == "matcher_attested"
        assert float(two["video_start_s"]) == 7.0
        assert two["matched_source_version"] == V1
        cap0 = _cap(conn, 0)
        cap2 = _cap(conn, 2)
        assert cap0["can_create"] is False
        assert cap2["can_create"] is True
        linked = conn.execute(
            """
            SELECT p.bbref_id, cp.role, c.play_index
            FROM clip_players cp
            JOIN clips c ON c.id = cp.clip_id
            JOIN players p ON p.id = cp.player_id
            ORDER BY c.play_index
            """
        ).fetchall()
        assert [(r["play_index"], r["bbref_id"], r["role"]) for r in linked] == [
            (2, "ficcas01", "shot")
        ]


def test_rerun_does_not_erase_verified_mapping(persist_url):
    import psycopg
    from psycopg.rows import dict_row

    pbp, matcher = _payload()
    persist_pbp_and_matcher(pbp, _out(matcher), _cfg(persist_url))
    report = persist_pbp_and_matcher(pbp, _out([], run_id="fic-persist-rerun"), _cfg(persist_url))
    assert report.kept_prior == 1
    with psycopg.connect(persist_url, row_factory=dict_row) as conn:
        two = conn.execute(
            "SELECT timing_provenance, matcher_run_id, video_start_s FROM clips WHERE play_index = 2"
        ).fetchone()
        assert two["timing_provenance"] == "matcher_attested"
        assert two["matcher_run_id"] == "fic-persist-run"
        assert float(two["video_start_s"]) == 7.0


def test_replay_after_source_replacement_is_refused(persist_url):
    import psycopg
    from psycopg.rows import dict_row

    pbp, matcher = _payload()
    persist_pbp_and_matcher(pbp, _out(matcher), _cfg(persist_url))
    with psycopg.connect(persist_url, row_factory=dict_row) as conn:
        conn.execute(
            "UPDATE games SET source_version = %s WHERE bbref_id = 'ficpersist01'",
            (V2,),
        )
        conn.commit()
        cap = _cap(conn, 2)
        assert cap["can_create"] is False
        assert cap["reason"] == "video_timing_unavailable"
        before, before_players = _clip_snapshot(conn)
    with pytest.raises(PersistRefused, match="analyzed source does not match locked game"):
        persist_pbp_and_matcher(pbp, _out(matcher, run_id="fic-persist-replay"), _cfg(persist_url))
    with psycopg.connect(persist_url, row_factory=dict_row) as conn:
        after, after_players = _clip_snapshot(conn)
        assert after == before
        assert after_players == before_players
        assert after[2]["matched_source_version"] == V1
    persist_pbp_and_matcher(
        pbp, _out(matcher, identity=V2, run_id="fic-persist-v2"), _cfg(persist_url)
    )
    with psycopg.connect(persist_url, row_factory=dict_row) as conn:
        cap = _cap(conn, 2)
        assert cap["can_create"] is True
        row = conn.execute(
            "SELECT matched_source_version FROM clips WHERE play_index = 2"
        ).fetchone()
        assert row["matched_source_version"] == V2


def test_wrong_analyzed_key_refused(persist_url):
    pbp, matcher = _payload()
    with pytest.raises(PersistRefused, match="fixtures/ keys only"):
        persist_pbp_and_matcher(
            pbp, _out(matcher, key="games/ficpersist01.mkv"), _cfg(persist_url)
        )


def test_duplicate_resolved_pbp_index_zero_mutation(persist_url):
    import psycopg
    from psycopg.rows import dict_row

    pbp, matcher = _payload()
    persist_pbp_and_matcher(pbp, _out(matcher), _cfg(persist_url))
    with psycopg.connect(persist_url, row_factory=dict_row) as conn:
        before, before_players = _clip_snapshot(conn)
    dup = list(matcher)
    dup.append({
        "play_index": 9,
        "pbp_play_index": 2,
        "video_timestamp": 30,
        "video_start": 20,
        "video_end": 28,
        "description": "FICTIONAL collision",
    })
    with pytest.raises(PersistRefused, match="duplicate matcher original play_index"):
        persist_pbp_and_matcher(pbp, _out(dup, run_id="fic-dup"), _cfg(persist_url))
    with psycopg.connect(persist_url, row_factory=dict_row) as conn:
        after, after_players = _clip_snapshot(conn)
        assert after == before
        assert after_players == before_players
        assert float(after[2]["video_start_s"]) == 7.0
    empty_url = persist_url
    with psycopg.connect(empty_url) as conn:
        conn.execute("DELETE FROM clip_players")
        conn.execute("DELETE FROM clips")
        conn.commit()
    with pytest.raises(PersistRefused, match="duplicate matcher original play_index"):
        persist_pbp_and_matcher(pbp, _out(dup, run_id="fic-dup-empty"), _cfg(persist_url))
    with psycopg.connect(persist_url) as conn:
        assert conn.execute("SELECT count(*) FROM clips").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM clip_players").fetchone()[0] == 0


def test_unknown_participant_rolls_back(persist_url):
    import psycopg

    pbp, matcher = _payload()
    pbp[2]["participants"] = [{"bbref_id": "missing01", "role": "shot"}]
    with pytest.raises(PersistRefused, match="participant player not found"):
        persist_pbp_and_matcher(pbp, _out(matcher), _cfg(persist_url))
    with psycopg.connect(persist_url) as conn:
        assert conn.execute("SELECT count(*) FROM clips").fetchone()[0] == 0


def test_invalid_period_rolls_back_partial_batch(persist_url):
    import psycopg

    pbp, matcher = _payload()
    pbp[4]["quarter"] = 99
    with pytest.raises(PersistRefused, match="invalid period"):
        persist_pbp_and_matcher(pbp, _out(matcher), _cfg(persist_url))
    with psycopg.connect(persist_url) as conn:
        assert conn.execute("SELECT count(*) FROM clips").fetchone()[0] == 0


def test_explicit_commit_required(persist_url):
    import psycopg

    pbp, matcher = _payload()
    report = persist_pbp_and_matcher(pbp, _out(matcher), _cfg(persist_url, commit=False))
    assert report.dry_run is True
    with psycopg.connect(persist_url) as conn:
        assert conn.execute("SELECT count(*) FROM clips").fetchone()[0] == 0


def test_entry_point_requires_explicit_env(persist_url, monkeypatch):
    from db.matcher_persist import maybe_persist_after_match

    pbp, matcher = _payload()
    monkeypatch.delenv("MATCHER_PERSIST", raising=False)
    skipped = maybe_persist_after_match(
        raw_pbp=pbp, matcher_output=_out(matcher), game_bbref_id="ficpersist01"
    )
    assert skipped.skipped_reason
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MATCHER_PERSIST", "1")
    monkeypatch.setenv("MATCHER_PERSIST_COMMIT", "1")
    monkeypatch.setenv("DATABASE_URL", persist_url)
    monkeypatch.setenv("MATCHER_ANALYZED_SOURCE_KEY", "fixtures/forged-from-env.mkv")
    monkeypatch.setenv("MATCHER_ANALYZED_SOURCE_IDENTITY", V2)
    monkeypatch.setenv("MATCHER_ANALYZED_IDENTITY_KIND", "content_sha256")
    monkeypatch.setenv("MATCHER_PERSIST_RUN_ID", "forged-env-run")
    report = maybe_persist_after_match(
        raw_pbp=pbp,
        matcher_output=_out(matcher, run_id="fic-env-run"),
        game_bbref_id="ficpersist01",
    )
    assert report.dry_run is False
    assert report.attested == 1
    assert report.source_version == V1


def test_v1_output_forged_v2_env_and_db_is_refused(persist_url, monkeypatch):
    import psycopg
    from psycopg.rows import dict_row
    from db.matcher_persist import maybe_persist_after_match

    pbp, matcher = _payload()
    persist_pbp_and_matcher(pbp, _out(matcher), _cfg(persist_url))
    with psycopg.connect(persist_url, row_factory=dict_row) as conn:
        conn.execute(
            "UPDATE games SET source_version = %s WHERE bbref_id = 'ficpersist01'",
            (V2,),
        )
        conn.commit()
        before, before_players = _clip_snapshot(conn)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MATCHER_PERSIST", "1")
    monkeypatch.setenv("MATCHER_PERSIST_COMMIT", "1")
    monkeypatch.setenv("DATABASE_URL", persist_url)
    monkeypatch.setenv("MATCHER_ANALYZED_SOURCE_KEY", ANALYZED_KEY)
    monkeypatch.setenv("MATCHER_ANALYZED_SOURCE_IDENTITY", V2)
    monkeypatch.setenv("MATCHER_ANALYZED_IDENTITY_KIND", "content_sha256")
    monkeypatch.setenv("MATCHER_PERSIST_RUN_ID", "forged-v2-run")
    with pytest.raises(PersistRefused, match="analyzed source does not match locked game"):
        maybe_persist_after_match(
            raw_pbp=pbp,
            matcher_output=_out(matcher, identity=V1, run_id="stale-v1-output"),
            game_bbref_id="ficpersist01",
        )
    with psycopg.connect(persist_url, row_factory=dict_row) as conn:
        after, after_players = _clip_snapshot(conn)
        assert after == before
        assert after_players == before_players
        assert after[2]["matched_source_version"] == V1


def test_matcher_list_without_envelope_is_refused(persist_url):
    pbp, matcher = _payload()
    with pytest.raises(PersistRefused, match="analyzed_source envelope"):
        persist_pbp_and_matcher(pbp, matcher, _cfg(persist_url))


def test_invalid_121s_window_preserves_verified_mapping(persist_url):
    import psycopg
    from psycopg.rows import dict_row

    pbp, matcher = _payload()
    persist_pbp_and_matcher(pbp, _out(matcher), _cfg(persist_url))
    bad = copy.deepcopy(matcher)
    bad[0]["video_start"] = 0
    bad[0]["video_end"] = 121
    bad[0]["video_timestamp"] = 1
    report = persist_pbp_and_matcher(pbp, _out(bad, run_id="fic-121"), _cfg(persist_url))
    assert report.kept_prior == 1
    assert report.attested == 0
    with psycopg.connect(persist_url, row_factory=dict_row) as conn:
        two = conn.execute(
            "SELECT video_start_s, video_end_s, matcher_run_id FROM clips WHERE play_index = 2"
        ).fetchone()
        assert float(two["video_start_s"]) == 7.0
        assert float(two["video_end_s"]) == 11.0
        assert two["matcher_run_id"] == "fic-persist-run"


def test_past_duration_window_preserves_verified_mapping(persist_url):
    import psycopg
    from psycopg.rows import dict_row

    pbp, matcher = _payload()
    persist_pbp_and_matcher(pbp, _out(matcher), _cfg(persist_url))
    with psycopg.connect(persist_url) as conn:
        conn.execute("UPDATE games SET duration_s = 5 WHERE bbref_id = 'ficpersist01'")
        conn.commit()
    report = persist_pbp_and_matcher(pbp, _out(matcher, run_id="fic-past-dur"), _cfg(persist_url))
    assert report.kept_prior == 1
    assert report.attested == 0
    with psycopg.connect(persist_url, row_factory=dict_row) as conn:
        two = conn.execute(
            "SELECT video_start_s, video_end_s, matcher_run_id FROM clips WHERE play_index = 2"
        ).fetchone()
        assert float(two["video_start_s"]) == 7.0
        assert two["matcher_run_id"] == "fic-persist-run"


def test_null_duration_preserves_verified_mapping(persist_url):
    import psycopg
    from psycopg.rows import dict_row

    pbp, matcher = _payload()
    persist_pbp_and_matcher(pbp, _out(matcher), _cfg(persist_url))
    with psycopg.connect(persist_url) as conn:
        conn.execute("UPDATE games SET duration_s = NULL WHERE bbref_id = 'ficpersist01'")
        conn.commit()
    report = persist_pbp_and_matcher(pbp, _out(matcher, run_id="fic-null-dur"), _cfg(persist_url))
    assert report.kept_prior == 1
    with psycopg.connect(persist_url, row_factory=dict_row) as conn:
        two = conn.execute(
            "SELECT video_start_s, matcher_run_id FROM clips WHERE play_index = 2"
        ).fetchone()
        assert float(two["video_start_s"]) == 7.0
        assert two["matcher_run_id"] == "fic-persist-run"


def test_shifted_pbp_zero_mutation(persist_url):
    import psycopg
    from psycopg.rows import dict_row

    pbp, matcher = _payload()
    persist_pbp_and_matcher(pbp, _out(matcher), _cfg(persist_url))
    with psycopg.connect(persist_url, row_factory=dict_row) as conn:
        before, before_players = _clip_snapshot(conn)
    shifted = copy.deepcopy(pbp)
    descs = [p["description"] for p in shifted]
    for i, play in enumerate(shifted):
        play["description"] = descs[(i + 1) % len(descs)]
        play.pop("participants", None)
    with pytest.raises(PersistRefused, match="play identity mismatch"):
        persist_pbp_and_matcher(shifted, _out(matcher, run_id="fic-shift"), _cfg(persist_url))
    with psycopg.connect(persist_url, row_factory=dict_row) as conn:
        after, after_players = _clip_snapshot(conn)
        assert after == before
        assert after_players == before_players


def test_repeated_description_changed_clock_zero_mutation(persist_url):
    import psycopg
    from psycopg.rows import dict_row

    pbp = [
        {"play_index": 0, "quarter": 1, "time": "11:00.0", "description": "FICTIONAL same wording"},
        {"play_index": 1, "quarter": 1, "time": "10:00.0", "description": "FICTIONAL same wording"},
    ]
    persist_pbp_and_matcher(pbp, _out([]), _cfg(persist_url))
    with psycopg.connect(persist_url, row_factory=dict_row) as conn:
        before, before_players = _clip_snapshot(conn)
        clocks = [row["game_clock"] for row in before]
        assert clocks[0] != clocks[1]
    shifted = copy.deepcopy(pbp)
    shifted[1]["time"] = "9:00.0"
    with pytest.raises(PersistRefused, match="play identity mismatch"):
        persist_pbp_and_matcher(shifted, _out([], run_id="fic-clock-shift"), _cfg(persist_url))
    with psycopg.connect(persist_url, row_factory=dict_row) as conn:
        after, after_players = _clip_snapshot(conn)
        assert after == before
        assert after_players == before_players
        assert [row["game_clock"] for row in after] == clocks


def test_reordered_filtered_pbp_without_indexes_zero_mutation(persist_url):
    import psycopg
    from psycopg.rows import dict_row
    from db.capability import CapabilityTypeError

    pbp, matcher = _payload()
    persist_pbp_and_matcher(pbp, _out(matcher), _cfg(persist_url))
    with psycopg.connect(persist_url, row_factory=dict_row) as conn:
        before, before_players = _clip_snapshot(conn)
    reordered = copy.deepcopy(pbp)
    reordered.reverse()
    for play in reordered:
        play.pop("play_index")
    with pytest.raises(CapabilityTypeError, match="explicit int"):
        persist_pbp_and_matcher(reordered, _out(matcher, run_id="fic-reorder"), _cfg(persist_url))
    filtered = copy.deepcopy(pbp)[1:]
    for play in filtered:
        play.pop("play_index")
    with pytest.raises(CapabilityTypeError, match="explicit int"):
        persist_pbp_and_matcher(filtered, _out(matcher, run_id="fic-filter"), _cfg(persist_url))
    with psycopg.connect(persist_url, row_factory=dict_row) as conn:
        after, after_players = _clip_snapshot(conn)
        assert after == before
        assert after_players == before_players


def test_unrelated_and_other_player_not_default_shot(persist_url):
    import psycopg
    from psycopg.rows import dict_row

    pbp, matcher = _payload()
    pbp[4]["participants"] = [{"bbref_id": "ficoth01", "role": "steal"}]
    persist_pbp_and_matcher(pbp, _out(matcher), _cfg(persist_url))
    with psycopg.connect(persist_url, row_factory=dict_row) as conn:
        rows = conn.execute(
            """
            SELECT c.play_index, p.bbref_id, cp.role
            FROM clip_players cp
            JOIN clips c ON c.id = cp.clip_id
            JOIN players p ON p.id = cp.player_id
            ORDER BY c.play_index, p.bbref_id
            """
        ).fetchall()
        assert [(r["play_index"], r["bbref_id"], r["role"]) for r in rows] == [
            (2, "ficcas01", "shot"),
            (4, "ficoth01", "steal"),
        ]
        none_for_unmatched = conn.execute(
            """
            SELECT count(*) AS n FROM clip_players cp
            JOIN clips c ON c.id = cp.clip_id
            WHERE c.play_index IN (0, 1, 3)
            """
        ).fetchone()["n"]
        assert none_for_unmatched == 0


def test_non_fictional_game_refused(postgres_url):
    import psycopg

    url = _setup_game(_url(postgres_url, "nba_persist_live"))
    with psycopg.connect(url) as conn:
        conn.execute(
            """
            UPDATE games
            SET is_fictional = false, s3_key = 'games/ficpersist01.mkv'
            WHERE bbref_id = 'ficpersist01'
            """
        )
        conn.commit()
    pbp, matcher = _payload()
    with pytest.raises(PersistRefused, match="fixtures/ keys only"):
        persist_pbp_and_matcher(
            pbp, _out(matcher, key="games/ficpersist01.mkv"),
            _cfg(url),
        )


def test_scraped_pbp_byte_envelope_e2e(persist_url, tmp_path):
    import psycopg
    from psycopg.rows import dict_row
    from db.analyzed_source import (
        assert_local_input_unmutated,
        capture_local_analyzed_source,
        matcher_output_document,
    )
    from pbp_source_identity import stamp_source_pbp_identity

    video = tmp_path / "fictional-persist.mkv"
    video.write_bytes(b"FICTIONAL-LOCAL-BYTES-V1")
    captured = capture_local_analyzed_source(
        path=str(video),
        key=ANALYZED_KEY,
        matcher_run_id="fic-scrape-e2e",
    )
    assert_local_input_unmutated(str(video), captured)
    with psycopg.connect(persist_url) as conn:
        conn.execute(
            "UPDATE games SET source_version = %s WHERE bbref_id = 'ficpersist01'",
            (captured["analyzed_source_identity"],),
        )
        conn.commit()

    scraped = [
        {
            "quarter": 1,
            "time": "11:42.0",
            "description": "Jump ball",
            "team": "Fiction",
        },
        {
            "quarter": 1,
            "time": "11:08.0",
            "description": "Casey Fixture makes 2-pt jumper (Other Fixture assists)",
            "team": "Fiction",
            "player_links": [
                "/players/f/ficcas01.html",
                "/players/f/ficoth01.html",
            ],
        },
    ]
    pbp = stamp_source_pbp_identity(scraped)
    assert [play["play_index"] for play in pbp] == [0, 1]
    assert pbp[1]["participants"] == [
        {"bbref_id": "ficcas01", "role": "shot"},
        {"bbref_id": "ficoth01", "role": "assist"},
    ]
    document = matcher_output_document(
        plays=[{
            "play_index": 0,
            "pbp_play_index": 1,
            "quarter": 1,
            "game_time": "11:08.0",
            "description": pbp[1]["description"],
            "video_timestamp": 9,
            "video_start": 7,
            "video_end": 11,
        }],
        analyzed_source=captured,
        matcher_version=MATCHER_VERSION,
    )
    report = persist_pbp_and_matcher(pbp, document, _cfg(persist_url))
    assert report.attested == 1
    assert report.source_version == captured["analyzed_source_identity"]
    with psycopg.connect(persist_url, row_factory=dict_row) as conn:
        row = conn.execute(
            "SELECT play_index, timing_provenance FROM clips WHERE play_index = 1"
        ).fetchone()
        assert row["timing_provenance"] == "matcher_attested"
        linked = conn.execute(
            """
            SELECT p.bbref_id, cp.role
            FROM clip_players cp
            JOIN clips c ON c.id = cp.clip_id
            JOIN players p ON p.id = cp.player_id
            WHERE c.play_index = 1
            ORDER BY cp.role
            """
        ).fetchall()
        assert [(r["bbref_id"], r["role"]) for r in linked] == [
            ("ficcas01", "shot"),
            ("ficoth01", "assist"),
        ]


def test_mixed_unresolved_play_persists_only_proven_participants(persist_url):
    import psycopg
    from psycopg.rows import dict_row
    from pbp_source_identity import stamp_source_pbp_identity

    scraped = [
        {
            "quarter": 1,
            "time": "11:08.0",
            "description": "Casey Fixture makes 2-pt jumper (Other Fixture assists)",
            "player_links": [
                "/players/f/ficcas01.html",
                "/players/f/ficoth01.html",
            ],
        },
        {
            "quarter": 1,
            "time": "10:50.0",
            "description": "Casey Fixture misses 2-pt jumper (from downtown)",
            "player_links": [
                "/players/f/ficcas01.html",
                "/players/f/ficoth01.html",
            ],
        },
    ]
    pbp = stamp_source_pbp_identity(scraped)
    assert pbp[0]["participants"][0]["role"] == "shot"
    assert pbp[1]["participants"] == []
    assert pbp[1]["participants_unresolved"] is True
    report = persist_pbp_and_matcher(
        pbp,
        _out([{
            "play_index": 9,
            "pbp_play_index": 0,
            "quarter": 1,
            "game_time": "11:08.0",
            "description": pbp[0]["description"],
            "video_timestamp": 9,
            "video_start": 7,
            "video_end": 11,
        }]),
        _cfg(persist_url),
    )
    assert report.attested == 1
    with psycopg.connect(persist_url, row_factory=dict_row) as conn:
        linked = conn.execute(
            """
            SELECT c.play_index, p.bbref_id, cp.role
            FROM clip_players cp
            JOIN clips c ON c.id = cp.clip_id
            JOIN players p ON p.id = cp.player_id
            ORDER BY c.play_index, cp.role
            """
        ).fetchall()
        assert [(r["play_index"], r["bbref_id"], r["role"]) for r in linked] == [
            (0, "ficcas01", "shot"),
            (0, "ficoth01", "assist"),
        ]
        unresolved = conn.execute(
            "SELECT count(*) AS n FROM clip_players cp JOIN clips c ON c.id = cp.clip_id WHERE c.play_index = 1"
        ).fetchone()["n"]
        assert unresolved == 0


def test_clip_players_replace_current_set_and_clear(persist_url):
    """P1 current-head rerun: replace the live association set, not leftover
    residue from a prior schema. Fresh persist is shot-only; populated
    shot→assist and empty/unresolved reruns must replace, not accumulate.
    """
    import psycopg
    from psycopg.rows import dict_row
    from pbp_source_identity import stamp_source_pbp_identity

    pbp, matcher = _payload()
    persist_pbp_and_matcher(pbp, _out(matcher), _cfg(persist_url))
    with psycopg.connect(persist_url, row_factory=dict_row) as conn:
        fresh = conn.execute(
            """
            SELECT p.bbref_id, cp.role
            FROM clip_players cp
            JOIN clips c ON c.id = cp.clip_id
            JOIN players p ON p.id = cp.player_id
            WHERE c.play_index = 2
            ORDER BY cp.role
            """
        ).fetchall()
        assert [(r["bbref_id"], r["role"]) for r in fresh] == [("ficcas01", "shot")]
    corrected = copy.deepcopy(pbp)
    corrected[2]["participants"] = [{"bbref_id": "ficcas01", "role": "assist"}]
    persist_pbp_and_matcher(corrected, _out(matcher, run_id="fic-role-fix"), _cfg(persist_url))
    with psycopg.connect(persist_url, row_factory=dict_row) as conn:
        linked = conn.execute(
            """
            SELECT p.bbref_id, cp.role
            FROM clip_players cp
            JOIN clips c ON c.id = cp.clip_id
            JOIN players p ON p.id = cp.player_id
            WHERE c.play_index = 2
            ORDER BY cp.role
            """
        ).fetchall()
        assert [(r["bbref_id"], r["role"]) for r in linked] == [("ficcas01", "assist")]
    emptied = copy.deepcopy(pbp)
    emptied[2]["participants"] = []
    persist_pbp_and_matcher(emptied, _out(matcher, run_id="fic-empty-parts"), _cfg(persist_url))
    with psycopg.connect(persist_url, row_factory=dict_row) as conn:
        n = conn.execute(
            """
            SELECT count(*) AS n FROM clip_players cp
            JOIN clips c ON c.id = cp.clip_id
            WHERE c.play_index = 2
            """
        ).fetchone()["n"]
        assert n == 0
    persist_pbp_and_matcher(pbp, _out(matcher, run_id="fic-restore-shot"), _cfg(persist_url))
    unresolved = copy.deepcopy(pbp)
    unresolved[2]["participants"] = [{"bbref_id": "ficcas01", "role": "coach"}]
    stamped = stamp_source_pbp_identity(unresolved)
    assert stamped[2]["participants"] == []
    assert stamped[2]["participants_unresolved"] is True
    persist_pbp_and_matcher(stamped, _out(matcher, run_id="fic-unresolved"), _cfg(persist_url))
    with psycopg.connect(persist_url, row_factory=dict_row) as conn:
        n = conn.execute(
            """
            SELECT count(*) AS n FROM clip_players cp
            JOIN clips c ON c.id = cp.clip_id
            WHERE c.play_index = 2
            """
        ).fetchone()["n"]
        assert n == 0


def test_replace_clip_players_rolls_back_on_unknown_player(persist_url):
    import psycopg
    from psycopg.rows import dict_row

    pbp, matcher = _payload()
    persist_pbp_and_matcher(pbp, _out(matcher), _cfg(persist_url))
    with psycopg.connect(persist_url, row_factory=dict_row) as conn:
        before, before_players = _clip_snapshot(conn)
    broken = copy.deepcopy(pbp)
    broken[2]["participants"] = [{"bbref_id": "missing01", "role": "assist"}]
    with pytest.raises(PersistRefused, match="participant player not found"):
        persist_pbp_and_matcher(broken, _out(matcher, run_id="fic-replace-fail"), _cfg(persist_url))
    with psycopg.connect(persist_url, row_factory=dict_row) as conn:
        after, after_players = _clip_snapshot(conn)
        assert after == before
        assert after_players == before_players
        linked = conn.execute(
            """
            SELECT p.bbref_id, cp.role FROM clip_players cp
            JOIN clips c ON c.id = cp.clip_id
            JOIN players p ON p.id = cp.player_id
            WHERE c.play_index = 2
            """
        ).fetchone()
        assert linked["role"] == "shot"


def test_v1_attested_v2_unmatched_clears_stale_windows(persist_url):
    """P8 current-head: after V1 attested windows, persist unmatched V2 must
    write timing_provenance none and NULL timestamps. Capability stays denied.
    """
    import psycopg
    from psycopg.rows import dict_row

    pbp, matcher = _payload()
    persist_pbp_and_matcher(pbp, _out(matcher), _cfg(persist_url))
    with psycopg.connect(persist_url, row_factory=dict_row) as conn:
        conn.execute(
            "UPDATE games SET source_version = %s WHERE bbref_id = 'ficpersist01'",
            (V2,),
        )
        conn.commit()
        stored = conn.execute(
            """
            SELECT timing_provenance, video_start_s, video_end_s, matched_source_version
            FROM clips WHERE play_index = 2
            """
        ).fetchone()
        assert stored["timing_provenance"] == "matcher_attested"
        assert float(stored["video_start_s"]) == 7.0
        assert float(stored["video_end_s"]) == 11.0
        assert stored["matched_source_version"] == V1
        cap = _cap(conn, 2)
        assert cap["can_create"] is False
        assert cap["reason"] == "video_timing_unavailable"
    report = persist_pbp_and_matcher(pbp, _out([], identity=V2, run_id="fic-v2-unmatched"), _cfg(persist_url))
    assert report.attested == 0
    assert report.kept_prior == 0
    with psycopg.connect(persist_url, row_factory=dict_row) as conn:
        two = conn.execute(
            """
            SELECT timing_provenance, video_start_s, video_end_s, video_timestamp_s,
                   matcher_run_id, matcher_version, matched_source_version
            FROM clips WHERE play_index = 2
            """
        ).fetchone()
        assert two["timing_provenance"] == "none"
        assert two["video_start_s"] is None
        assert two["video_end_s"] is None
        assert two["video_timestamp_s"] is None
        assert two["matcher_run_id"] is None
        assert two["matcher_version"] is None
        assert two["matched_source_version"] is None
        cap = _cap(conn, 2)
        assert cap["can_create"] is False
        assert cap["reason"] == "footage_not_matched"


def test_noncanonical_matcher_version_is_refused(persist_url):
    import psycopg

    pbp, matcher = _payload()
    empty = _out(matcher)
    empty["matcher_version"] = ""
    with pytest.raises(PersistRefused, match="matcher_version"):
        persist_pbp_and_matcher(pbp, empty, _cfg(persist_url))
    missing = _out(matcher)
    del missing["matcher_version"]
    with pytest.raises(PersistRefused, match="matcher_version"):
        persist_pbp_and_matcher(pbp, missing, _cfg(persist_url))
    whitespace = _out(matcher)
    whitespace["matcher_version"] = "  "
    with pytest.raises(PersistRefused, match="matcher_version"):
        persist_pbp_and_matcher(pbp, whitespace, _cfg(persist_url))
    nonstring = _out(matcher)
    nonstring["matcher_version"] = ["match_play_timestamps/persist"]
    with pytest.raises(PersistRefused, match="matcher_version"):
        persist_pbp_and_matcher(pbp, nonstring, _cfg(persist_url))
    with psycopg.connect(persist_url) as conn:
        assert conn.execute("SELECT count(*) FROM clips").fetchone()[0] == 0


def test_preset_coach_unresolved_neighbor_survives_persist(persist_url):
    import psycopg
    from psycopg.rows import dict_row
    from pbp_source_identity import stamp_source_pbp_identity

    pbp, matcher = _payload()
    pbp[2]["participants"] = [{"bbref_id": "ficcas01", "role": "coach"}]
    pbp[4]["participants"] = [{"bbref_id": "ficoth01", "role": "steal"}]
    stamped = stamp_source_pbp_identity(pbp)
    assert stamped[2]["participants"] == []
    assert stamped[2]["participants_unresolved"] is True
    assert stamped[4]["participants"] == [{"bbref_id": "ficoth01", "role": "steal"}]
    persist_pbp_and_matcher(stamped, _out(matcher), _cfg(persist_url))
    with psycopg.connect(persist_url, row_factory=dict_row) as conn:
        rows = conn.execute(
            """
            SELECT c.play_index, p.bbref_id, cp.role
            FROM clip_players cp
            JOIN clips c ON c.id = cp.clip_id
            JOIN players p ON p.id = cp.player_id
            ORDER BY c.play_index, p.bbref_id
            """
        ).fetchall()
        assert [(r["play_index"], r["bbref_id"], r["role"]) for r in rows] == [
            (4, "ficoth01", "steal"),
        ]
        two = conn.execute(
            "SELECT timing_provenance FROM clips WHERE play_index = 2"
        ).fetchone()
        assert two["timing_provenance"] == "matcher_attested"


def test_persist_module_has_no_s3_or_http_client():
    import inspect
    from db import matcher_persist as mod
    source = inspect.getsource(mod)
    assert "boto3" not in source
    assert "presign" not in source
    assert "enqueue" not in source
    assert "config_from_env" in source
    assert "MATCHER_ANALYZED_SOURCE_IDENTITY" not in source
