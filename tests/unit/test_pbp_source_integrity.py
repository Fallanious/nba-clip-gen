"""Frozen PBP load bytes and member metadata checks. No remote DB."""

import hashlib
import json

import pytest

from match_play_timestamps import condense_plays, load_pbp_source, stamp_original_play_indexes
from persist_matcher_clips import (
    PersistRefused,
    canonical_group_indexes,
    grouping_sha256,
    persist_matched_output,
)


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


def test_load_hashes_the_same_bytes_it_parses(tmp_path):
    raw = [
        {"quarter": 1, "time": "11:00.0", "team": "FIC", "description": "event zero"},
        {"quarter": 1, "time": "10:50.0", "team": "FIC", "description": "event one"},
    ]
    data = json.dumps(raw).encode("utf-8")
    path = tmp_path / "202601010FIC.json"
    path.write_bytes(data)
    digest, plays = load_pbp_source(str(path))
    assert digest == hashlib.sha256(data).hexdigest()
    assert [p["play_index"] for p in plays] == [0, 1]
    assert plays[0]["description"] == "event zero"


def test_file_mutation_after_load_does_not_change_frozen_hash(tmp_path):
    original = [{"quarter": 1, "time": "11:00.0", "team": "FIC", "description": "event zero"}]
    data = json.dumps(original).encode("utf-8")
    path = tmp_path / "202601010FIC.json"
    path.write_bytes(data)
    digest, plays = load_pbp_source(str(path))
    mutated = json.dumps(
        original + [{"quarter": 1, "time": "10:00.0", "team": "FIC", "description": "later scrape"}]
    ).encode("utf-8")
    path.write_bytes(mutated)
    assert digest == hashlib.sha256(data).hexdigest()
    assert digest != hashlib.sha256(path.read_bytes()).hexdigest()
    assert plays[0]["description"] == "event zero"
    assert len(plays) == 1


def test_forged_member_metadata_refused_before_connect(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    raw = [
        {"quarter": 1, "time": "11:00.0", "team": "FIC", "description": "real event zero"},
        {"quarter": 1, "time": "10:50.0", "team": "FIC", "description": "real event one"},
    ]
    path = tmp_path / "pbp.json"
    path.write_bytes(json.dumps(raw).encode("utf-8"))
    digest, plays = load_pbp_source(str(path))
    forged = dict(plays[0])
    forged["description"] = "forged description"
    with pytest.raises(PersistRefused, match="frozen PBP source"):
        persist_matched_output(
            [_group([forged])],
            database_url="postgresql://nba@127.0.0.1:1/does_not_connect",
            game_bbref_id="202601010FIC",
            pbp_hash=digest,
            original_plays=plays,
        )


def test_stamped_source_required_for_identity():
    raw = [{"quarter": 1, "time": "1:00.0", "team": "FIC", "description": "a"}]
    stamped = stamp_original_play_indexes(raw)
    assert stamped[0]["play_index"] == 0


def test_canonical_grouping_is_full_pbp_not_selected_subset():
    raw = [
        {"quarter": 1, "time": "11:38.0", "score": "0-2", "team": "76ers", "description": "A. Drummond makes 2-pt dunk"},
        {"quarter": 1, "time": "11:20.0", "score": "2-2", "team": "Celtics", "description": "D. White makes 2-pt jump shot"},
        {"quarter": 1, "time": "10:34.0", "score": "2-4", "team": "Celtics", "description": "D. White misses 3-pt jump shot from 25 ft"},
        {"quarter": 1, "time": "10:32.0", "score": "2-4", "team": "Celtics", "description": "Offensive rebound by Team"},
        {"quarter": 1, "time": "9:16.0", "score": "4-7", "team": "Celtics", "description": "J. Brown makes free throw 1 of 2"},
    ]
    stamped = stamp_original_play_indexes(raw)
    full = canonical_group_indexes(stamped)
    filtered = [g for g in condense_plays(stamped, time_window=5) if "White" in g["description"]]
    selected = tuple(tuple(g["original_play_indexes"]) for g in filtered)
    assert (2, 3) in full
    assert selected != full
    assert grouping_sha256(full) != grouping_sha256(selected)
