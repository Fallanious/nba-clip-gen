"""DEV-local persist of matcher output into existing clips.

One clips row per original PBP event. Members of a matched compound group
intentionally share that group's video_start_s/video_end_s (shared sequence
timing, not per-event timing). Each row keeps that event's own play_index,
period, game_clock, and description.

Writes run in one transaction after matcher output is complete.
Exact reruns no-op. Occupied mismatching keys abort the whole transaction.
Never deletes. Never overwrites. No clip_players. No migrations.
duration_s is generated — omit it from INSERT.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any, Mapping
from urllib.parse import urlparse

from match_play_timestamps import extract_action_type, original_members

BBREF_ID_RE = re.compile(r"[0-9]{9}[A-Z]{3}")
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
VIDEO_SCALE = Decimal("0.001")
ATTR_INDEXES = "shared_sequence_indexes"
ATTR_PBP = "pbp_sha256"
ATTR_TIMING = "timing"
TIMING_SHARED = "shared_sequence"


class PersistRefused(ValueError):
    pass


def pbp_sha256_file(path: str) -> str:
    data = open(path, "rb").read()
    return hashlib.sha256(data).hexdigest()


def quantize_video_s(value: Any, *, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise PersistRefused(f"persist refused: {field} is not a video second")
    if isinstance(value, str):
        raise PersistRefused(f"persist refused: {field} is not a video second")
    try:
        quantized = Decimal(str(value)).quantize(VIDEO_SCALE, rounding=ROUND_HALF_EVEN)
    except Exception as exc:
        raise PersistRefused(f"persist refused: {field} is not a video second") from exc
    if not quantized.is_finite() or quantized < 0 or quantized >= Decimal("1000000"):
        raise PersistRefused(f"persist refused: {field} is out of clips.numeric(9,3) range")
    return quantized


def game_clock_interval(time_str: Any) -> timedelta:
    if not isinstance(time_str, str) or not time_str.strip():
        raise PersistRefused("persist refused: original game clock is missing")
    parts = time_str.strip().split(":")
    if len(parts) != 2:
        raise PersistRefused("persist refused: original game clock is not MM:SS")
    try:
        minutes = int(parts[0])
        seconds = float(parts[1])
    except ValueError as exc:
        raise PersistRefused("persist refused: original game clock is not MM:SS") from exc
    if minutes < 0 or seconds < 0:
        raise PersistRefused("persist refused: original game clock is negative")
    return timedelta(minutes=minutes, seconds=seconds)


def period_value(quarter: Any) -> int:
    if type(quarter) is not int or quarter < 1 or quarter > 10:
        raise PersistRefused("persist refused: period must be an int 1-10")
    return quarter


def assert_local_database_url(database_url: str) -> str:
    if not database_url or not database_url.strip():
        raise PersistRefused("persist refused: missing DATABASE_URL")
    parsed = urlparse(database_url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise PersistRefused("persist refused: DATABASE_URL must be postgres")
    host = (parsed.hostname or "").lower()
    if host.endswith("neon.tech") or "neon.tech" in host:
        raise PersistRefused("persist refused: Neon/live targets are not allowed")
    if host not in LOCAL_HOSTS:
        raise PersistRefused("persist refused: local database required")
    return database_url


def expand_matched_groups(matched_plays: Any) -> list[dict[str, Any]]:
    """Turn matched groups into one persist row per original member."""
    if not isinstance(matched_plays, list):
        raise PersistRefused("persist refused: matcher output must be a list")
    expanded: list[dict[str, Any]] = []
    seen: set[int] = set()
    for group in matched_plays:
        if not isinstance(group, Mapping):
            raise PersistRefused("persist refused: matcher row must be an object")
        members = original_members(group)
        if not members:
            raise PersistRefused("persist refused: matched group has no original members")
        start = quantize_video_s(group.get("video_start"), field="video_start")
        end = quantize_video_s(group.get("video_end"), field="video_end")
        if end <= start:
            raise PersistRefused("persist refused: shared sequence window is not ordered")
        indexes = [item["play_index"] for item in members]
        if indexes != group.get("original_play_indexes", indexes):
            listed = group.get("original_play_indexes")
            if listed != indexes:
                raise PersistRefused("persist refused: original_play_indexes does not match members")
        for item in members:
            index = item["play_index"]
            if index in seen:
                raise PersistRefused("persist refused: duplicate original play_index mapping")
            seen.add(index)
            expanded.append(
                {
                    "play_index": index,
                    "period": period_value(item["quarter"]),
                    "game_clock": game_clock_interval(item["time"]),
                    "description": item["description"],
                    "action_type": extract_action_type(item["description"]),
                    "video_start_s": start,
                    "video_end_s": end,
                    "shared_sequence_indexes": indexes,
                }
            )
    return expanded


def _rows_conflict(existing: Mapping[str, Any], incoming: Mapping[str, Any], pbp_hash: str) -> bool:
    attrs = existing["attributes"] or {}
    if isinstance(attrs, str):
        attrs = json.loads(attrs)
    return not (
        int(existing["period"]) == incoming["period"]
        and existing["description"] == incoming["description"]
        and existing["game_clock"] == incoming["game_clock"]
        and Decimal(existing["video_start_s"]) == incoming["video_start_s"]
        and Decimal(existing["video_end_s"]) == incoming["video_end_s"]
        and attrs.get(ATTR_INDEXES) == incoming["shared_sequence_indexes"]
        and attrs.get(ATTR_PBP) == pbp_hash
        and attrs.get(ATTR_TIMING) == TIMING_SHARED
    )


def persist_matched_output(
    matched_plays: Any,
    *,
    database_url: str,
    game_bbref_id: str,
    pbp_path: str,
) -> dict[str, int]:
    """Insert matched original events. One transaction. Local DB only."""
    assert_local_database_url(database_url)
    if not BBREF_ID_RE.fullmatch(game_bbref_id or ""):
        raise PersistRefused("persist refused: game bbref_id is not a Basketball-Reference id")
    pbp_hash = pbp_sha256_file(pbp_path)
    rows = expand_matched_groups(matched_plays)
    report = {"inserted": 0, "noop": 0}

    import psycopg
    from psycopg.types.json import Jsonb

    with psycopg.connect(database_url) as conn:
        conn.execute("BEGIN")
        try:
            game = conn.execute(
                """
                SELECT id, game_date
                FROM games
                WHERE bbref_id = %s
                """,
                (game_bbref_id,),
            ).fetchone()
            if game is None:
                raise PersistRefused("persist refused: parent game is missing")
            game_id, game_date = game

            for row in rows:
                existing = conn.execute(
                    """
                    SELECT period, description, game_clock, video_start_s, video_end_s, attributes
                    FROM clips
                    WHERE game_id = %s AND play_index = %s
                    """,
                    (game_id, row["play_index"]),
                ).fetchone()
                if existing is None:
                    conn.execute(
                        """
                        INSERT INTO clips (
                          game_id, game_date, play_index, period, game_clock,
                          description, action_type, attributes,
                          video_start_s, video_end_s
                        ) VALUES (
                          %s, %s, %s, %s, %s,
                          %s, %s, %s,
                          %s, %s
                        )
                        """,
                        (
                            game_id,
                            game_date,
                            row["play_index"],
                            row["period"],
                            row["game_clock"],
                            row["description"],
                            row["action_type"],
                            Jsonb(
                                {
                                    ATTR_INDEXES: row["shared_sequence_indexes"],
                                    ATTR_PBP: pbp_hash,
                                    ATTR_TIMING: TIMING_SHARED,
                                }
                            ),
                            row["video_start_s"],
                            row["video_end_s"],
                        ),
                    )
                    report["inserted"] += 1
                    continue
                existing_row = {
                    "period": existing[0],
                    "description": existing[1],
                    "game_clock": existing[2],
                    "video_start_s": existing[3],
                    "video_end_s": existing[4],
                    "attributes": existing[5],
                }
                if _rows_conflict(existing_row, row, pbp_hash):
                    raise PersistRefused(
                        "persist refused: occupied play_index does not match this event"
                    )
                report["noop"] += 1
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return report
