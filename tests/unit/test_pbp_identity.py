"""Condensed matcher index is never stored play_index. Explicit PBP identity only."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "src")

from db.capability import CapabilityTypeError
from db.pbp_identity import matcher_offsets, normalize_pbp, resolve_matcher_pbp_index
from db.matcher_persist import PersistConfig, PersistRefused, maybe_persist_after_match, persist_pbp_and_matcher

FIXTURES = Path("tests/fixtures/plays")


def _cfg(**overrides):
    values = dict(
        database_url="postgresql://nba@127.0.0.1:1/unused",
        game_bbref_id="ficpersist01",
        matcher_run_id="fic-run",
        analyzed_source_key="fixtures/fictional-persist.mkv",
        analyzed_source_identity="sha256:" + ("a" * 64),
        analyzed_identity_kind="content_sha256",
        commit=False,
    )
    values.update(overrides)
    return PersistConfig(**values)


def test_condensed_play_index_is_not_pbp_identity():
    pbp = json.loads((FIXTURES / "persist_pbp_fictional.json").read_text())
    matcher = json.loads((FIXTURES / "persist_matcher_condensed_fictional.json").read_text())
    rows = normalize_pbp(pbp)
    mapped = [resolve_matcher_pbp_index(m, rows) for m in matcher]
    assert mapped[0] == 2
    assert mapped[0] != matcher[0]["play_index"]
    assert matcher_offsets(matcher[1]) is None


def test_missing_explicit_pbp_index_is_not_identity():
    pbp = normalize_pbp([
        {"play_index": 0, "quarter": 1, "time": "9:00.0", "description": "FICTIONAL a"},
        {"play_index": 1, "quarter": 1, "time": "8:00.0", "description": "FICTIONAL b"},
    ])
    matcher = {"play_index": 0, "quarter": 1, "game_time": "8:00.0", "description": "FICTIONAL b"}
    assert resolve_matcher_pbp_index(matcher, pbp) is None


def test_compound_description_fails_closed():
    pbp = normalize_pbp([
        {"play_index": 2, "quarter": 1, "time": "11:08.0", "description": "FICTIONAL a + b"},
    ])
    matcher = {
        "pbp_play_index": 2,
        "is_compound": True,
        "description": "FICTIONAL a + b",
        "video_timestamp": 1,
        "video_start": 0,
        "video_end": 2,
    }
    assert resolve_matcher_pbp_index(matcher, pbp) is None


def test_normalize_pbp_requires_explicit_int_index():
    with pytest.raises(CapabilityTypeError, match="explicit int"):
        normalize_pbp([{"quarter": 1, "description": "FICTIONAL"}])
    with pytest.raises(CapabilityTypeError):
        normalize_pbp([{"play_index": True, "description": "FICTIONAL"}])


def test_matcher_offsets_reject_over_max_and_negative_timestamp():
    assert matcher_offsets({
        "video_timestamp": 1, "video_start": 0, "video_end": 121,
    }) is None
    assert matcher_offsets({
        "video_timestamp": -1, "video_start": 0, "video_end": 2,
    }) is None


def test_maybe_persist_skips_without_flag(monkeypatch):
    monkeypatch.delenv("MATCHER_PERSIST", raising=False)
    report = maybe_persist_after_match(raw_pbp=[], matcher_plays=[])
    assert report.skipped_reason
    assert report.dry_run is True


def test_dry_run_does_not_need_a_database(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    pbp = json.loads((FIXTURES / "persist_pbp_fictional.json").read_text())
    matcher = json.loads((FIXTURES / "persist_matcher_condensed_fictional.json").read_text())
    report = persist_pbp_and_matcher(pbp, matcher, _cfg())
    assert report.dry_run is True
    assert report.attested == 1
    assert report.unmatched == 4
    assert report.game_id is None


def test_etag_identity_kind_refused(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    pbp = json.loads((FIXTURES / "persist_pbp_fictional.json").read_text())
    with pytest.raises(PersistRefused, match="not immutable"):
        persist_pbp_and_matcher(
            pbp, [],
            _cfg(analyzed_identity_kind="etag", analyzed_source_identity="etag-v1"),
        )


def test_persist_guards_target_before_connect(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    pbp = json.loads((FIXTURES / "persist_pbp_fictional.json").read_text())
    with pytest.raises(PersistRefused, match="production"):
        persist_pbp_and_matcher(pbp, [], _cfg(commit=True))
