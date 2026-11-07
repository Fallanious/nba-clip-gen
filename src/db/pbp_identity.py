"""Map matcher rows onto original PBP play_index. Condensed matcher index is never DB identity."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Iterable, Mapping, Optional

from db.bounds import CLIP_MAX_DURATION_S
from db.capability import CapabilityTypeError, parse_optional_seconds

MAX_PBP_PLAYS = 500
CLIP_ROLES = frozenset({
    "shot", "assist", "block", "steal", "rebound", "foul", "fouled", "turnover",
})


def is_int_index(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def normalize_pbp(pbp_plays: Iterable[Mapping[str, Any]], *, max_plays: int = MAX_PBP_PLAYS) -> list[dict[str, Any]]:
    """Stable identity is an explicit integer play_index on each original PBP row.

    Array position is never identity. Missing, bool, non-integer, and duplicate
    indexes are refused.
    """
    rows = list(pbp_plays)
    if len(rows) > max_plays:
        raise CapabilityTypeError(f"PBP exceeds bound of {max_plays} plays")
    used: set[int] = set()
    out: list[dict[str, Any]] = []
    for play in rows:
        raw = play.get("play_index")
        if not is_int_index(raw):
            raise CapabilityTypeError("PBP play_index must be an explicit int")
        if raw in used:
            raise CapabilityTypeError(f"PBP identity collision at play_index={raw}")
        used.add(raw)
        item = dict(play)
        item["play_index"] = raw
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


def play_participants(play: Mapping[str, Any]) -> list[dict[str, str]]:
    """Independently established participants only. No default shot role."""
    raw = play.get("participants")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise CapabilityTypeError("participants must be a list")
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise CapabilityTypeError("participant must be an object")
        bbref = item.get("bbref_id")
        role = item.get("role")
        if not isinstance(bbref, str) or not bbref.strip() or bbref != bbref.strip():
            raise CapabilityTypeError("participant bbref_id must be a canonical token")
        if not isinstance(role, str) or role not in CLIP_ROLES:
            raise CapabilityTypeError("participant role is not an established clip_role")
        key = (bbref, role)
        if key in seen:
            raise CapabilityTypeError("duplicate participant role on play")
        seen.add(key)
        out.append({"bbref_id": bbref, "role": role})
    return out


def resolve_matcher_pbp_index(
    matcher_row: Mapping[str, Any],
    pbp: list[Mapping[str, Any]],
) -> Optional[int]:
    """Return original PBP play_index. Ignores condensed matcher play_index.

    Compound descriptions fail closed: they are not identity. There is no
    quarter/time/description natural-key fallback.
    """
    if matcher_row.get("is_compound") is True:
        return None
    explicit = matcher_row.get("pbp_play_index")
    if explicit is None:
        explicit = matcher_row.get("original_play_index")
    if not is_int_index(explicit):
        return None
    known = {int(p["play_index"]) for p in pbp}
    if explicit not in known:
        return None
    return explicit


def matcher_offsets(matcher_row: Mapping[str, Any]) -> Optional[tuple[Any, Any, Any]]:
    """Validated VIDEO-relative seconds, or None if unmatched/invalid. No game-clock conversion.

    Duration-within-file is enforced at persist against the locked game row.
    """
    try:
        ts = parse_optional_seconds(matcher_row.get("video_timestamp"), field="video_timestamp")
        start = parse_optional_seconds(matcher_row.get("video_start"), field="video_start")
        end = parse_optional_seconds(matcher_row.get("video_end"), field="video_end")
    except CapabilityTypeError:
        return None
    if ts is None or start is None or end is None:
        return None
    if ts < 0 or start < 0 or end <= start:
        return None
    if (end - start) > CLIP_MAX_DURATION_S:
        return None
    return start, end, ts


def original_pbp_index_from_condensed_play(play: Mapping[str, Any]) -> Optional[int]:
    for key in ("pbp_play_index", "original_play_index"):
        value = play.get(key)
        if is_int_index(value):
            return value
    originals = play.get("original_plays") or []
    if originals:
        idx = originals[0].get("play_index")
        if is_int_index(idx):
            return idx
    return None
