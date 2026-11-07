"""Stamp source PBP identity at scrape/load. Array position is not persist identity."""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Optional

from db.pbp_identity import CLIP_ROLES

BBREF_PLAYER_HREF = re.compile(r"/players/[a-z]/([a-z][a-z0-9]+)\.html", re.I)
_PAREN = re.compile(r"\(([^)]*)\)")


class SourcePbpError(ValueError):
    pass


def player_ids_from_hrefs(hrefs: Iterable[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for href in hrefs:
        if not isinstance(href, str):
            continue
        match = BBREF_PLAYER_HREF.search(href)
        if match is None:
            continue
        bbref = match.group(1)
        if bbref in seen:
            continue
        seen.add(bbref)
        out.append(bbref)
    return out


def _paren_role(inner: str) -> Optional[str]:
    text = inner.lower().strip()
    if "drawn by" in text:
        return "fouled"
    if "assist" in text:
        return "assist"
    if "block" in text:
        return "block"
    if "steal" in text:
        return "steal"
    return None


def _paren_roles(description: str) -> Optional[list[str]]:
    """Recognized parenthetical roles, or None if any clause is unsupported."""
    roles: list[str] = []
    for inner in _PAREN.findall(description):
        role = _paren_role(inner)
        if role is None:
            return None
        roles.append(role)
    return roles


def _primary_role(description: str) -> Optional[str]:
    text = _PAREN.sub(" ", description.lower())
    if "foul" in text:
        return "foul"
    if re.search(r"\b(makes|misses|made|missed)\b", text) or any(
        token in text for token in ("jumper", "layup", "dunk", "free throw", "3-pt", "shot")
    ):
        return "shot"
    if "rebound" in text:
        return "rebound"
    if "turnover" in text:
        return "turnover"
    if "steal" in text:
        return "steal"
    return None


def _preset_participants(raw: list[Any]) -> Optional[list[dict[str, str]]]:
    """Return canonical participants, or None if any row is invalid."""
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            return None
        bbref = item.get("bbref_id")
        role = item.get("role")
        if not isinstance(bbref, str) or not bbref.strip() or bbref != bbref.strip():
            return None
        if not isinstance(role, str) or role not in CLIP_ROLES:
            return None
        key = (bbref, role)
        if key in seen:
            return None
        seen.add(key)
        out.append({"bbref_id": bbref, "role": role})
    return out


def source_play_participants(play: Mapping[str, Any]) -> tuple[list[dict[str, str]], bool]:
    """Return (participants, unresolved). Unresolved plays get [] and no guessed roles."""
    existing = play.get("participants")
    if isinstance(existing, list):
        parts = _preset_participants(existing)
        if parts is None:
            return [], True
        return parts, False
    hrefs = play.get("player_links") or play.get("player_hrefs") or []
    if not isinstance(hrefs, list):
        return [], True
    ids = player_ids_from_hrefs(hrefs)
    if not ids:
        return [], False
    description = str(play.get("description") or "")
    paren_roles = _paren_roles(description)
    if paren_roles is None:
        return [], True
    primary = _primary_role(description)
    expected = (1 if primary is not None else 0) + len(paren_roles)
    if len(ids) != expected:
        return [], True
    primary_count = 1 if primary is not None else 0
    out: list[dict[str, str]] = []
    for index, bbref in enumerate(ids):
        if index < primary_count:
            out.append({"bbref_id": bbref, "role": primary})
        else:
            out.append({"bbref_id": bbref, "role": paren_roles[index - primary_count]})
    return out, False


def participants_from_source_play(play: Mapping[str, Any]) -> list[dict[str, str]]:
    parts, _unresolved = source_play_participants(play)
    return parts


def stamp_source_pbp_identity(plays: Any) -> list[dict[str, Any]]:
    """Write source event identity into PBP rows. Persist never enumerates."""
    if not isinstance(plays, list):
        raise SourcePbpError("PBP plays must be a list")
    rows: list[dict[str, Any]] = []
    for play in plays:
        if not isinstance(play, Mapping):
            raise SourcePbpError("PBP play must be an object")
        rows.append(dict(play))
    indexes = [play.get("play_index") for play in rows]
    present = [index for index in indexes if index is not None]
    if present and len(present) != len(rows):
        raise SourcePbpError("partial play_index; source identity must be complete")
    if not present:
        for index, play in enumerate(rows):
            play["play_index"] = index
    for play in rows:
        parts, unresolved = source_play_participants(play)
        play["participants"] = parts
        if unresolved:
            play["participants_unresolved"] = True
        else:
            play.pop("participants_unresolved", None)
        play.pop("player_links", None)
        play.pop("player_hrefs", None)
    return rows
