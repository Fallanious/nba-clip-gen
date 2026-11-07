"""Condensed matcher index is never stored play_index."""

import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

from db.pbp_identity import matcher_offsets, normalize_pbp, resolve_matcher_pbp_index, _unique_natural_key
from db.matcher_persist import PersistConfig, maybe_persist_after_match, persist_pbp_and_matcher

FIXTURES = Path("tests/fixtures/plays")


def test_condensed_play_index_is_not_pbp_identity():
    pbp = json.loads((FIXTURES / "persist_pbp_fictional.json").read_text())
    matcher = json.loads((FIXTURES / "persist_matcher_condensed_fictional.json").read_text())
    rows = normalize_pbp(pbp)
    natural = _unique_natural_key(rows)
    mapped = [resolve_matcher_pbp_index(m, rows, natural_keys=natural) for m in matcher]
    assert mapped[0] == 2
    assert mapped[0] != matcher[0]["play_index"]
    assert matcher_offsets(matcher[1]) is None


def test_natural_key_used_when_pbp_play_index_missing():
    pbp = normalize_pbp([
        {"quarter": 1, "time": "9:00.0", "description": "FICTIONAL a"},
        {"quarter": 1, "time": "8:00.0", "description": "FICTIONAL b"},
    ])
    natural = _unique_natural_key(pbp)
    matcher = {"play_index": 0, "quarter": 1, "game_time": "8:00.0", "description": "FICTIONAL b"}
    assert resolve_matcher_pbp_index(matcher, pbp, natural_keys=natural) == 1
    assert matcher["play_index"] != 1


def test_maybe_persist_skips_without_flag(monkeypatch):
    monkeypatch.delenv("MATCHER_PERSIST", raising=False)
    report = maybe_persist_after_match(raw_pbp=[], matcher_plays=[])
    assert report.skipped_reason
    assert report.dry_run is True


def test_dry_run_does_not_need_a_database():
    pbp = json.loads((FIXTURES / "persist_pbp_fictional.json").read_text())
    matcher = json.loads((FIXTURES / "persist_matcher_condensed_fictional.json").read_text())
    report = persist_pbp_and_matcher(
        pbp,
        matcher,
        PersistConfig(
            database_url="postgresql://nba@127.0.0.1:1/unused",
            game_bbref_id="ficpersist01",
            matcher_run_id="fic-run",
            commit=False,
        ),
    )
    assert report.dry_run is True
    assert report.attested == 1
    assert report.unmatched == 4
    assert report.game_id is None
