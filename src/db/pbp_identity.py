"""Map matcher rows onto original PBP play_index. Condensed matcher index is never DB identity."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Iterable, Mapping, Optional

from db.capability import CapabilityTypeError, parse_optional_seconds

MAX_PBP_PLAYS = 500


def is_int_index(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def normalize_pbp(pbp_plays: Iterable[Mapping[str, Any]], *, max_plays: int = MAX_PBP_PLAYS) -> list[dict[str, Any]]:
    """Stable identity is original PBP order, or an explicit integer play_index on those rows."""
    rows = list(pbp_plays)
    if len(rows) > max_plays:
        raise CapabilityTypeError(f"PBP exceeds bound of {max_plays} plays")
    used: set[int] = set()
    out: list[dict[str, Any]] = []
    for i, play in enumerate(rows):
        raw = play.get("play_index")
        if raw is None:
            idx = i
        elif not is_int_index(raw):
            raise CapabilityTypeError("PBP play_index must be an int")
        else:
            idx = raw
        if idx in used:
            raise CapabilityTypeError(f"PBP identity collision at play_index={idx}")
        used.add(idx)
        item = dict(play)
        item["play_index"] = idx
        out.append(item)
    return out


def pbp_clock_timedelta(value: Any) -> Optional[timedelta]:
    """Display-only game clock. Not video seconds. Never used for eligibility."""
    if value is None:
        return None
    if isinstance(value, timedelta):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        if ":" not in text:
            return None
        minutes_s, rest = text.split(":", 1)
        minutes = int(minutes_s)
        seconds = float(rest)
        if minutes < 0 or seconds < 0:
            return None
        return timedelta(minutes=minutes, seconds=seconds)
    except (TypeError, ValueError):
        return None


def _pbp_lookup(pbp: list[Mapping[str, Any]]) -> dict[int, Mapping[str, Any]]:
    return {int(p["play_index"]): p for p in pbp}


def _unique_natural_key(pbp: list[Mapping[str, Any]]) -> dict[tuple[Any, Any, Any], int]:
    keys: dict[tuple[Any, Any, Any], int] = {}
    collisions: set[tuple[Any, Any, Any]] = set()
    for play in pbp:
        key = (play.get("quarter") or play.get("period"), play.get("time") or play.get("game_clock"), play.get("description"))
        if key in keys:
            collisions.add(key)
        else:
            keys[key] = int(play["play_index"])
    for key in collisions:
        keys.pop(key, None)
    return keys


def resolve_matcher_pbp_index(
    matcher_row: Mapping[str, Any],
    pbp: list[Mapping[str, Any]],
    *,
    natural_keys: Mapping[tuple[Any, Any, Any], int],
) -> Optional[int]:
    """Return original PBP play_index. Ignores condensed matcher play_index."""
    explicit = matcher_row.get("pbp_play_index")
    if explicit is None:
        explicit = matcher_row.get("original_play_index")
    if is_int_index(explicit):
        return explicit
    key = (
        matcher_row.get("quarter") or matcher_row.get("period"),
        matcher_row.get("game_time") or matcher_row.get("time") or matcher_row.get("game_clock"),
        matcher_row.get("description"),
    )
    return natural_keys.get(key)


def matcher_offsets(matcher_row: Mapping[str, Any]) -> Optional[tuple[Any, Any, Any]]:
    """Validated VIDEO-relative seconds, or None if unmatched/invalid. No game-clock conversion."""
    try:
        ts = parse_optional_seconds(matcher_row.get("video_timestamp"), field="video_timestamp")
        start = parse_optional_seconds(matcher_row.get("video_start"), field="video_start")
        end = parse_optional_seconds(matcher_row.get("video_end"), field="video_end")
    except CapabilityTypeError:
        return None
    if ts is None or start is None or end is None:
        return None
    if start < 0 or end <= start:
        return None
    return start, end, ts


def original_pbp_index_from_condensed_play(play: Mapping[str, Any]) -> Optional[int]:
    for key in ("pbp_play_index", "play_index"):
        value = play.get(key)
        if key == "play_index" and play.get("pbp_play_index") is None:
            # Condensed matcher lists reuse play_index for array position. Only
            # trust play_index on the *original* PBP object, not matcher output.
            continue
        if is_int_index(value):
            return value
    originals = play.get("original_plays") or []
    if originals:
        idx = originals[0].get("play_index")
        if is_int_index(idx):
            return idx
    return None
