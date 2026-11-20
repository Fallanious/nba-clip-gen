"""Fail-closed original PBP play_index. Condensed matcher play_index is never identity."""

from __future__ import annotations

from typing import Any, Mapping, Optional


def is_int_index(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


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
    known = {int(p["play_index"]) for p in pbp if is_int_index(p.get("play_index"))}
    if explicit not in known:
        return None
    return explicit
