"""Isolated Postgres matcher persist. Fictional fixtures only. Never Neon/S3."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from db.migrate import apply_migrations
from db.matcher_persist import PersistConfig, PersistRefused, persist_pbp_and_matcher
from db.sql_loader import load_query_sql
from test_schema_capability import postgres_url

pytestmark = pytest.mark.postgres

FIXTURES = Path("tests/fixtures/plays")


def _url(postgres_url, name):
    import psycopg

    admin = postgres_url.rsplit("/", 1)[0] + "/postgres"
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {name}")
        c.execute(f"CREATE DATABASE {name}")
    return postgres_url.rsplit("/", 1)[0] + f"/{name}"


def _setup_game(url, *, source_version="etag-persist-v1"):
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
            INSERT INTO games (
              bbref_id, season, game_date, home_team_id, away_team_id,
              s3_key, duration_s, source_kind, source_version, is_fictional
            )
            SELECT
              'ficpersist01', '2025-26', DATE '2026-01-03', h.id, a.id,
              'fixtures/fictional-persist.mkv', 48, 'original_private', %s, true
            FROM teams h, teams a
            WHERE h.abbr = 'FIC' AND a.abbr = 'QAF'
            """,
            (source_version,),
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
        matcher_run_id="fic-persist-run",
        player_bbref_id="ficcas01",
        commit=True,
    )
    values.update(overrides)
    return PersistConfig(**values)


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


def test_persist_maps_pbp_index_not_condensed(persist_url):
    import psycopg
    from psycopg.rows import dict_row

    pbp, matcher = _payload()
    report = persist_pbp_and_matcher(pbp, matcher, _cfg(persist_url))
    assert report.dry_run is False
    assert report.attested == 1
    assert report.unmatched == 4
    with psycopg.connect(persist_url, row_factory=dict_row) as conn:
        zero = conn.execute(
            "SELECT timing_provenance, video_start_s FROM clips WHERE play_index = 0"
        ).fetchone()
        two = conn.execute(
            "SELECT timing_provenance, video_start_s, video_end_s FROM clips WHERE play_index = 2"
        ).fetchone()
        assert zero["timing_provenance"] == "none"
        assert zero["video_start_s"] is None
        assert two["timing_provenance"] == "matcher_attested"
        assert float(two["video_start_s"]) == 7.0
        cap0 = _cap(conn, 0)
        cap2 = _cap(conn, 2)
        assert cap0["can_create"] is False
        assert cap2["can_create"] is True
        linked = conn.execute("SELECT count(*) AS n FROM clip_players").fetchone()["n"]
        assert linked == 5


def test_rerun_does_not_erase_verified_mapping(persist_url):
    import psycopg
    from psycopg.rows import dict_row

    pbp, matcher = _payload()
    persist_pbp_and_matcher(pbp, matcher, _cfg(persist_url))
    report = persist_pbp_and_matcher(pbp, [], _cfg(persist_url, matcher_run_id="fic-persist-rerun"))
    assert report.kept_prior == 1
    with psycopg.connect(persist_url, row_factory=dict_row) as conn:
        two = conn.execute(
            "SELECT timing_provenance, matcher_run_id, video_start_s FROM clips WHERE play_index = 2"
        ).fetchone()
        assert two["timing_provenance"] == "matcher_attested"
        assert two["matcher_run_id"] == "fic-persist-run"
        assert float(two["video_start_s"]) == 7.0


def test_stale_source_invalidates_until_reattest(persist_url):
    import psycopg
    from psycopg.rows import dict_row

    pbp, matcher = _payload()
    persist_pbp_and_matcher(pbp, matcher, _cfg(persist_url))
    with psycopg.connect(persist_url, row_factory=dict_row) as conn:
        conn.execute("UPDATE games SET source_version = 'etag-persist-v2' WHERE bbref_id = 'ficpersist01'")
        conn.commit()
        cap = _cap(conn, 2)
        assert cap["can_create"] is False
        assert cap["reason"] == "video_timing_unavailable"
    persist_pbp_and_matcher(pbp, matcher, _cfg(persist_url, matcher_run_id="fic-persist-v2"))
    with psycopg.connect(persist_url, row_factory=dict_row) as conn:
        cap = _cap(conn, 2)
        assert cap["can_create"] is True
        row = conn.execute(
            "SELECT matched_source_version FROM clips WHERE play_index = 2"
        ).fetchone()
        assert row["matched_source_version"] == "etag-persist-v2"


def test_identity_collision_keeps_first_mapping(persist_url):
    import psycopg
    from psycopg.rows import dict_row

    pbp, matcher = _payload()
    matcher = list(matcher)
    matcher.append({
        "play_index": 9,
        "pbp_play_index": 2,
        "video_timestamp": 30,
        "video_start": 20,
        "video_end": 28,
        "description": "FICTIONAL collision",
    })
    persist_pbp_and_matcher(pbp, matcher, _cfg(persist_url))
    with psycopg.connect(persist_url, row_factory=dict_row) as conn:
        two = conn.execute("SELECT video_start_s FROM clips WHERE play_index = 2").fetchone()
        assert float(two["video_start_s"]) == 7.0


def test_missing_player_rolls_back(persist_url):
    import psycopg

    pbp, matcher = _payload()
    with pytest.raises(PersistRefused, match="player not found"):
        persist_pbp_and_matcher(pbp, matcher, _cfg(persist_url, player_bbref_id="missing01"))
    with psycopg.connect(persist_url) as conn:
        assert conn.execute("SELECT count(*) FROM clips").fetchone()[0] == 0


def test_invalid_period_rolls_back_partial_batch(persist_url):
    import psycopg

    pbp, matcher = _payload()
    pbp[4]["quarter"] = 99
    with pytest.raises(PersistRefused, match="invalid period"):
        persist_pbp_and_matcher(pbp, matcher, _cfg(persist_url))
    with psycopg.connect(persist_url) as conn:
        assert conn.execute("SELECT count(*) FROM clips").fetchone()[0] == 0


def test_explicit_commit_required(persist_url):
    import psycopg

    pbp, matcher = _payload()
    report = persist_pbp_and_matcher(pbp, matcher, _cfg(persist_url, commit=False))
    assert report.dry_run is True
    with psycopg.connect(persist_url) as conn:
        assert conn.execute("SELECT count(*) FROM clips").fetchone()[0] == 0


def test_entry_point_requires_explicit_env(persist_url, monkeypatch):
    from db.matcher_persist import maybe_persist_after_match

    pbp, matcher = _payload()
    monkeypatch.delenv("MATCHER_PERSIST", raising=False)
    skipped = maybe_persist_after_match(
        raw_pbp=pbp, matcher_plays=matcher, game_bbref_id="ficpersist01", matcher_run_id="x"
    )
    assert skipped.skipped_reason
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MATCHER_PERSIST", "1")
    monkeypatch.setenv("MATCHER_PERSIST_COMMIT", "1")
    monkeypatch.setenv("DATABASE_URL", persist_url)
    report = maybe_persist_after_match(
        raw_pbp=pbp,
        matcher_plays=matcher,
        game_bbref_id="ficpersist01",
        matcher_run_id="fic-env-run",
        player_bbref_id="ficcas01",
    )
    assert report.dry_run is False
    assert report.attested == 1


def test_persist_module_has_no_s3_or_http_client():
    import inspect
    from db import matcher_persist as mod
    source = inspect.getsource(mod)
    assert "boto3" not in source
    assert "presign" not in source
    assert "enqueue" not in source
