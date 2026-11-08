"""Original full-game indexes, grouping-before-filter, persist expansion."""

from match_play_timestamps import (
    cluster_play_groups,
    condense_plays,
    original_members,
    stamp_original_play_indexes,
)
from persist_matcher_clips import PersistRefused, canonical_group_indexes, expand_matched_groups


def _raw():
    return [
        {"quarter": 1, "time": "11:38.0", "score": "0-2", "team": "76ers", "description": "A. Drummond makes 2-pt dunk"},
        {"quarter": 1, "time": "11:20.0", "score": "2-2", "team": "Celtics", "description": "D. White makes 2-pt jump shot"},
        {"quarter": 1, "time": "10:34.0", "score": "2-4", "team": "Celtics", "description": "D. White misses 3-pt jump shot from 25 ft"},
        {"quarter": 1, "time": "10:32.0", "score": "2-4", "team": "Celtics", "description": "Offensive rebound by Team"},
        {"quarter": 1, "time": "9:16.0", "score": "4-7", "team": "Celtics", "description": "J. Brown makes free throw 1 of 2"},
    ]


def test_stamp_is_full_game_order_before_group():
    stamped = stamp_original_play_indexes(_raw())
    assert [p["play_index"] for p in stamped] == [0, 1, 2, 3, 4]


def test_group_then_filter_does_not_change_membership_or_indexes():
    stamped = stamp_original_play_indexes(_raw())
    grouped = condense_plays(stamped, time_window=5)
    white_miss = [g for g in grouped if g.get("is_compound") and "misses 3-pt" in g["description"]][0]
    assert white_miss["original_play_indexes"] == [2, 3]
    filtered = [g for g in grouped if "White" in g["description"]]
    still = [g for g in filtered if g.get("is_compound") and "misses 3-pt" in g["description"]][0]
    assert still["original_play_indexes"] == [2, 3]
    assert [m["play_index"] for m in original_members(still)] == [2, 3]
    assert still["original_plays"][0]["description"].startswith("D. White misses")
    assert still["original_plays"][1]["description"] == "Offensive rebound by Team"
    rows = expand_matched_groups(
        [{**still, "video_start": 12.5, "video_end": 18.5}]
    )
    assert [r["play_index"] for r in rows] == [2, 3]
    assert rows[1]["description"] == "Offensive rebound by Team"
    assert "White" not in rows[1]["description"]


def test_expand_compound_shares_group_window_keeps_event_metadata():
    stamped = stamp_original_play_indexes(_raw())
    grouped = condense_plays(stamped, time_window=5)
    compound = [g for g in grouped if g.get("original_play_indexes") == [2, 3]][0]
    compound["video_start"] = 12.5
    compound["video_end"] = 18.5
    rows = expand_matched_groups([compound])
    assert [r["play_index"] for r in rows] == [2, 3]
    assert rows[0]["video_start_s"] == rows[1]["video_start_s"]
    assert rows[0]["video_end_s"] == rows[1]["video_end_s"]
    assert rows[0]["description"].startswith("D. White misses")
    assert rows[1]["description"] == "Offensive rebound by Team"
    assert "→" not in rows[0]["description"]
    assert rows[0]["shared_sequence_indexes"] == [2, 3]
    assert rows[1]["shared_sequence_indexes"] == [2, 3]


def test_persist_grouping_uses_shared_cluster_partition():
    stamped = stamp_original_play_indexes(_raw())
    condensed = condense_plays(stamped)
    clustered = tuple(tuple(item["play_index"] for item in group) for group in cluster_play_groups(stamped))
    assert canonical_group_indexes(stamped) == clustered
    assert canonical_group_indexes(stamped) == tuple(
        tuple(group["original_play_indexes"]) for group in condensed
    )


def test_expand_rejects_duplicate_original_indexes():
    group = {
        "video_start": 1,
        "video_end": 2,
        "original_plays": [
            {"play_index": 4, "quarter": 1, "time": "1:00.0", "team": "76ers", "description": "a"},
            {"play_index": 4, "quarter": 1, "time": "0:58.0", "team": "76ers", "description": "b"},
        ],
        "original_play_indexes": [4, 4],
    }
    try:
        expand_matched_groups([group])
    except PersistRefused as exc:
        assert "duplicate" in str(exc)
    else:
        raise AssertionError("expected duplicate refusal")
