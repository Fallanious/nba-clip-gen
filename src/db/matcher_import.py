"""Offline PBP + matcher JSON merge. No database writes, S3, or Ollama.

Persist SQL and match_timestamps Neon wiring are a later slice. This helper
only LEFT JOINs matcher rows onto stored PBP play_index.
"""

from __future__ import annotations

from typing import Any, Iterable

from db.capability import PROVENANCE_ATTESTED, PROVENANCE_NONE, CapabilityTypeError, parse_optional_seconds


def merge_pbp_and_matcher(
    pbp_plays: Iterable[dict[str, Any]],
    matcher_plays: Iterable[dict[str, Any]],
    *,
    matcher_run_id: str,
    matcher_version: str,
    source_version: str,
) -> list[dict[str, Any]]:
    """LEFT JOIN matcher onto PBP by play_index. Unmatched stay with none/NULL offsets."""
    by_index = {}
    for item in matcher_plays:
        idx = item.get("play_index")
        if not isinstance(idx, int) or isinstance(idx, bool):
            raise CapabilityTypeError("play_index must be an int")
        by_index[idx] = item

    rows = []
    for i, play in enumerate(pbp_plays):
        idx = play.get("play_index", i)
        row = {
            "play_index": idx,
            "description": play.get("description", ""),
            "period": play.get("quarter") or play.get("period"),
            "game_clock": play.get("time") or play.get("game_clock"),
            "timing_provenance": PROVENANCE_NONE,
            "video_start_s": None,
            "video_end_s": None,
            "video_timestamp_s": None,
            "matcher_run_id": None,
            "matcher_version": None,
            "matched_source_version": None,
        }
        matched = by_index.get(idx)
        if not matched:
            rows.append(row)
            continue
        timestamp = matched.get("video_timestamp")
        start = matched.get("video_start")
        end = matched.get("video_end")
        try:
            ts = parse_optional_seconds(timestamp, field="video_timestamp")
            start_s = parse_optional_seconds(start, field="video_start")
            end_s = parse_optional_seconds(end, field="video_end")
        except CapabilityTypeError:
            rows.append(row)
            continue
        if ts is None or start_s is None or end_s is None:
            rows.append(row)
            continue
        row.update({
            "timing_provenance": PROVENANCE_ATTESTED,
            "video_start_s": start_s,
            "video_end_s": end_s,
            "video_timestamp_s": ts,
            "matcher_run_id": matcher_run_id,
            "matcher_version": matcher_version,
            "matched_source_version": source_version,
        })
        rows.append(row)
    return rows
