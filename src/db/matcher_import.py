"""Offline PBP + matcher JSON merge. No database writes, S3, or Ollama.

Persist SQL and match_timestamps Neon wiring are a later slice. This helper
only LEFT JOINs matcher rows onto stored PBP play_index.
"""

from __future__ import annotations

from typing import Any, Iterable

from db.bounds import CLIP_MAX_DURATION_S
from db.capability import (
    PROVENANCE_ATTESTED,
    PROVENANCE_NONE,
    CapabilityTypeError,
    parse_optional_seconds,
)
from db.pbp_index import resolve_matcher_pbp_index


def _canonical_token(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise CapabilityTypeError(f"{field} must be a nonempty canonical token")
    return value


def _require_play_index(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CapabilityTypeError(f"{field} must be an int")
    return value


def merge_pbp_and_matcher(
    pbp_plays: Iterable[dict[str, Any]],
    matcher_plays: Iterable[dict[str, Any]],
    *,
    matcher_run_id: str,
    matcher_version: str,
    source_version: str,
) -> list[dict[str, Any]]:
    """LEFT JOIN matcher onto PBP by original play_index. No condensed fallback.

    Duplicate, missing, non-integer, or bool PBP play_index values are refused.
    Matcher rows resolve only through resolve_matcher_pbp_index. Invalid windows
    (reversed, negative, longer than CLIP_MAX_DURATION_S) stay none/NULL, not
    attested. Duplicate resolved original indexes are refused.
    """
    run_id = _canonical_token(matcher_run_id, field="matcher_run_id")
    version = _canonical_token(matcher_version, field="matcher_version")
    matched_version = _canonical_token(source_version, field="source_version")

    pbp_list = list(pbp_plays)
    for play in pbp_list:
        _require_play_index(play.get("play_index"), field="PBP play_index")

    resolved: dict[int, dict[str, Any]] = {}
    for item in matcher_plays:
        orig = resolve_matcher_pbp_index(item, pbp_list)
        if orig is None:
            continue
        if orig in resolved:
            raise CapabilityTypeError("duplicate matcher original play_index")
        resolved[orig] = item

    rows = []
    for play in pbp_list:
        idx = _require_play_index(play.get("play_index"), field="PBP play_index")
        unmatched = {
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
        matched = resolved.get(idx)
        if not matched:
            rows.append(unmatched)
            continue
        try:
            ts = parse_optional_seconds(matched.get("video_timestamp"), field="video_timestamp")
            start_s = parse_optional_seconds(matched.get("video_start"), field="video_start")
            end_s = parse_optional_seconds(matched.get("video_end"), field="video_end")
        except CapabilityTypeError:
            rows.append(unmatched)
            continue
        if ts is None or start_s is None or end_s is None:
            rows.append(unmatched)
            continue
        if ts < 0 or start_s < 0 or end_s <= start_s or (end_s - start_s) > CLIP_MAX_DURATION_S:
            rows.append(unmatched)
            continue
        row = dict(unmatched)
        row.update({
            "timing_provenance": PROVENANCE_ATTESTED,
            "video_start_s": start_s,
            "video_end_s": end_s,
            "video_timestamp_s": ts,
            "matcher_run_id": run_id,
            "matcher_version": version,
            "matched_source_version": matched_version,
        })
        rows.append(row)
    return rows
