"""Persist matcher output into existing clips (local fixtures or one Neon DEV endpoint).

One clips row per original PBP event. Members of a matched compound group
intentionally share that group's video_start_s/video_end_s (shared sequence
timing, not per-event timing). Each row keeps that event's own play_index,
period, game_clock, and description.

Writes run in one transaction after matcher output is complete.
Exact reruns no-op. Occupied mismatching keys abort the whole transaction.
Changed PBP source or changed canonical full-PBP grouping aborts the whole
transaction, including when incoming play_index keys are disjoint.
Identical source plus identical full grouping may insert additional selected
groups whose indexes do not overlap stored rows. Group identity is that full
canonical grouping, not the player-filter selected subset.
Never deletes. Never overwrites. No clip_players. No migrations.
duration_s is generated — omit it from INSERT.

Connection targets are checked from the URI and process environment before
connect. URI query keys use a narrow allowlist (sslmode only). Neon DEV
requires explicit sslmode=require or verify-full. libpq routing/service
environment variables are refused so they cannot fill hostaddr/service.
Connect uses explicit parameters, not the raw URI. Production is never
allowlisted. The Neon DEV hostname is the sanctioned metadata host only;
pooled or other endpoints are refused.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any, Mapping
from urllib.parse import parse_qsl, unquote, urlparse

from match_play_timestamps import (
    GROUP_WINDOW_S,
    cluster_play_groups,
    extract_action_type,
    original_members,
)

BBREF_ID_RE = re.compile(r"[0-9]{9}[A-Z]{3}")
SHA256_HEX_RE = re.compile(r"[0-9a-f]{64}")
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
PERSIST_APP_ENVS = frozenset({"dev", "test"})
ALLOWED_URI_QUERY_KEYS = frozenset({"sslmode"})
DEV_SSLMODES = frozenset({"require", "verify-full"})
# libpq uses env vars as defaults for unspecified parameters, including
# hostaddr alongside a URI host (PGHOSTADDR). Refuse routing/service vars
# before connect; do not rely on a post-connect check.
LIBPQ_ROUTING_ENV = frozenset({
    "PGHOST",
    "PGHOSTADDR",
    "PGPORT",
    "PGDATABASE",
    "PGUSER",
    "PGSERVICE",
    "PGSERVICEFILE",
    "PGOPTIONS",
    "PGSYSCONFDIR",
    "PGTARGETSESSIONATTRS",
    "PGLOADBALANCEHOSTS",
    "PGREQUIRESSL",
    "PGSSLMODE",
})
# Sanctioned Neon METADATA-only lookup, 2026-11-08. Not a DSN. No pooled host.
DEV_ENDPOINT_ID = "ep-rapid-smoke-b4t1v549"
DEV_ENDPOINT_HOST = "ep-rapid-smoke-b4t1v549.c-6.us-east-2.aws.neon.tech"
DEV_REGION_ID = "aws-us-east-2"
DEV_PROJECT_ID = "morning-mud-60538825"
DEV_BRANCH_ID = "br-odd-smoke-b465tvf8"
DEV_DATABASE = "neondb"
VIDEO_SCALE = Decimal("0.001")
ATTR_INDEXES = "shared_sequence_indexes"
ATTR_PBP = "pbp_sha256"
ATTR_GROUPING = "grouping_sha256"
ATTR_TIMING = "timing"
TIMING_SHARED = "shared_sequence"


class PersistRefused(ValueError):
    pass


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


def canonical_group_indexes(original_plays: Any) -> tuple[tuple[int, ...], ...]:
    """Full-PBP grouping membership. Independent of which groups were selected.

    Uses the same cluster_play_groups partition as matcher condense_plays.
    Only the index partition is used as group identity.
    """
    if not isinstance(original_plays, list):
        raise PersistRefused("persist refused: frozen PBP source must be a list")
    if not original_plays:
        return ()
    for play in original_plays:
        if not isinstance(play, Mapping):
            raise PersistRefused("persist refused: frozen PBP source is not indexed events")
        if type(play.get("play_index")) is not int:
            raise PersistRefused("persist refused: frozen PBP indexes are not 0..n-1")
    try:
        groups = cluster_play_groups(original_plays, time_window=GROUP_WINDOW_S)
    except (KeyError, TypeError) as exc:
        raise PersistRefused("persist refused: frozen PBP source is not indexed events") from exc
    return tuple(tuple(item["play_index"] for item in group) for group in groups)


def grouping_sha256(membership: tuple[tuple[int, ...], ...]) -> str:
    payload = json.dumps([list(group) for group in membership], separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def assert_persist_app_env() -> str:
    """Persist is allowed only when APP_ENV is exactly dev or test."""
    app_env = os.environ.get("APP_ENV")
    if app_env not in PERSIST_APP_ENVS:
        raise PersistRefused("persist refused: APP_ENV must be exactly dev or test")
    return app_env


def _database_name(path: str) -> str:
    name = (path or "").lstrip("/")
    if not name:
        return ""
    name = name.rstrip("/")
    if not name or "/" in name:
        return ""
    return name


def _sslmode_query(query: str) -> str | None:
    pairs = [(key.lower(), value) for key, value in parse_qsl(query or "", keep_blank_values=True)]
    keys = [key for key, _value in pairs]
    if len(keys) != len(set(keys)):
        raise PersistRefused("persist refused: database URI query has duplicate keys")
    disallowed = [key for key in keys if key not in ALLOWED_URI_QUERY_KEYS]
    if disallowed:
        raise PersistRefused("persist refused: database URI query is not allowlisted")
    if not pairs:
        return None
    return pairs[0][1]


def assert_libpq_routing_env() -> None:
    """Refuse libpq env vars that can route or service-select before connect."""
    if any(name in os.environ for name in LIBPQ_ROUTING_ENV):
        raise PersistRefused("persist refused: libpq routing environment overrides the target")


def persist_connect_params(database_url: str) -> tuple[str, dict[str, Any]]:
    """Parse DATABASE_URL into explicit connect kwargs. Does not log userinfo."""
    if not database_url or not database_url.strip():
        raise PersistRefused("persist refused: missing DATABASE_URL")
    parsed = urlparse(database_url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise PersistRefused("persist refused: DATABASE_URL must be postgres")
    netloc = parsed.netloc or ""
    if "," in netloc:
        raise PersistRefused("persist refused: database target is not allowlisted")
    sslmode = _sslmode_query(parsed.query)
    try:
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError as exc:
        raise PersistRefused("persist refused: database target is not allowlisted") from exc
    if not host:
        raise PersistRefused("persist refused: database target is not allowlisted")
    if "," in host:
        raise PersistRefused("persist refused: database target is not allowlisted")
    dbname = _database_name(parsed.path)
    if not dbname:
        raise PersistRefused("persist refused: database target is not allowlisted")
    user = unquote(parsed.username) if parsed.username else ""
    if not user:
        raise PersistRefused("persist refused: database target is not allowlisted")
    password = unquote(parsed.password) if parsed.password is not None else ""
    params: dict[str, Any] = {
        "host": host,
        "port": port if port is not None else 5432,
        "dbname": dbname,
        "user": user,
        "password": password,
    }

    if host in LOCAL_HOSTS:
        if sslmode is not None:
            params["sslmode"] = sslmode
        return "local", params

    if (
        host == DEV_ENDPOINT_HOST
        and host.startswith(DEV_ENDPOINT_ID + ".")
        and ".us-east-2.aws.neon.tech" in host
        and dbname == DEV_DATABASE
        and params["port"] == 5432
    ):
        if sslmode not in DEV_SSLMODES:
            raise PersistRefused(
                "persist refused: Neon DEV requires sslmode=require or verify-full"
            )
        params["sslmode"] = sslmode
        return "dev", params

    raise PersistRefused("persist refused: database target is not allowlisted")


def assert_persist_database_url(database_url: str) -> str:
    """Allow isolated local fixtures or the single verified Neon DEV endpoint."""
    kind, _params = persist_connect_params(database_url)
    return kind


def assert_persist_ready(database_url: str) -> dict[str, Any]:
    """App env, libpq routing env, and URI allowlist. Call before connect."""
    assert_persist_app_env()
    assert_libpq_routing_env()
    _kind, params = persist_connect_params(database_url)
    return params


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
        listed = group.get("original_play_indexes", indexes)
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


def _parse_stored_attributes(attrs: Any) -> Mapping[str, Any] | None:
    if isinstance(attrs, str):
        try:
            attrs = json.loads(attrs)
        except json.JSONDecodeError:
            return None
    if not isinstance(attrs, Mapping):
        return None
    return attrs


def game_source_conflict(existing_attributes: list[Any], pbp_hash: str) -> bool:
    """True when any stored clip is missing pbp_sha256 or disagrees with this source."""
    for attrs in existing_attributes:
        parsed = _parse_stored_attributes(attrs)
        if parsed is None or parsed.get(ATTR_PBP) != pbp_hash:
            return True
    return False


def game_grouping_conflict(existing_attributes: list[Any], grouping_hash: str) -> bool:
    """True when any stored clip is missing grouping_sha256 or disagrees with full grouping."""
    for attrs in existing_attributes:
        parsed = _parse_stored_attributes(attrs)
        if parsed is None or parsed.get(ATTR_GROUPING) != grouping_hash:
            return True
    return False


def assert_rows_match_frozen_source(
    rows: list[Mapping[str, Any]],
    original_plays: Any,
    pbp_hash: str,
) -> None:
    if not isinstance(pbp_hash, str) or not SHA256_HEX_RE.fullmatch(pbp_hash):
        raise PersistRefused("persist refused: pbp hash is not a sha256 hex digest")
    if not isinstance(original_plays, list):
        raise PersistRefused("persist refused: frozen PBP source must be a list")
    source_by_index: dict[int, Mapping[str, Any]] = {}
    for index, play in enumerate(original_plays):
        if not isinstance(play, Mapping):
            raise PersistRefused("persist refused: frozen PBP source is not indexed events")
        if play.get("play_index") != index:
            raise PersistRefused("persist refused: frozen PBP indexes are not 0..n-1")
        source_by_index[index] = play
    for row in rows:
        source = source_by_index.get(row["play_index"])
        if source is None:
            raise PersistRefused("persist refused: matched play_index is not in frozen PBP source")
        if period_value(source.get("quarter")) != row["period"]:
            raise PersistRefused("persist refused: matched event does not match frozen PBP source")
        if game_clock_interval(source.get("time")) != row["game_clock"]:
            raise PersistRefused("persist refused: matched event does not match frozen PBP source")
        if source.get("description") != row["description"]:
            raise PersistRefused("persist refused: matched event does not match frozen PBP source")


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
        and attrs.get(ATTR_GROUPING) == incoming["grouping_sha256"]
        and attrs.get(ATTR_TIMING) == TIMING_SHARED
    )


def persist_matched_output(
    matched_plays: Any,
    *,
    database_url: str,
    game_bbref_id: str,
    pbp_hash: str,
    original_plays: Any,
) -> dict[str, int]:
    """Insert matched original events. One transaction. Guard before connect."""
    conn_params = assert_persist_ready(database_url)
    if not BBREF_ID_RE.fullmatch(game_bbref_id or ""):
        raise PersistRefused("persist refused: game bbref_id is not a Basketball-Reference id")
    rows = expand_matched_groups(matched_plays)
    assert_rows_match_frozen_source(rows, original_plays, pbp_hash)
    canonical = canonical_group_indexes(original_plays)
    grouping_hash = grouping_sha256(canonical)
    canonical_set = set(canonical)
    for row in rows:
        if tuple(row["shared_sequence_indexes"]) not in canonical_set:
            raise PersistRefused(
                "persist refused: matched group is not in the canonical full-PBP grouping"
            )
        row["grouping_sha256"] = grouping_hash
    report = {"inserted": 0, "noop": 0}

    import psycopg
    from psycopg.errors import UniqueViolation
    from psycopg.types.json import Jsonb

    with psycopg.connect(**conn_params) as conn:
        conn.autocommit = True
        try:
            with conn.transaction():
                game = conn.execute(
                    """
                    SELECT id, game_date
                    FROM games
                    WHERE bbref_id = %s
                    FOR UPDATE
                    """,
                    (game_bbref_id,),
                ).fetchone()
                if game is None:
                    raise PersistRefused("persist refused: parent game is missing")
                game_id, game_date = game

                existing_rows = conn.execute(
                    """
                    SELECT play_index, period, description, game_clock,
                           video_start_s, video_end_s, attributes
                    FROM clips
                    WHERE game_id = %s
                    FOR UPDATE
                    """,
                    (game_id,),
                ).fetchall()
                occupied: dict[int, dict[str, Any]] = {}
                existing_attrs: list[Any] = []
                for rec in existing_rows:
                    occupied[int(rec[0])] = {
                        "period": rec[1],
                        "description": rec[2],
                        "game_clock": rec[3],
                        "video_start_s": rec[4],
                        "video_end_s": rec[5],
                        "attributes": rec[6],
                    }
                    existing_attrs.append(rec[6])
                if game_source_conflict(existing_attrs, pbp_hash):
                    raise PersistRefused(
                        "persist refused: saved PBP does not match clips already stored for this game"
                    )
                if game_grouping_conflict(existing_attrs, grouping_hash):
                    raise PersistRefused(
                        "persist refused: canonical grouping does not match clips already stored for this game"
                    )

                for row in rows:
                    existing = occupied.get(row["play_index"])
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
                                        ATTR_GROUPING: grouping_hash,
                                        ATTR_TIMING: TIMING_SHARED,
                                    }
                                ),
                                row["video_start_s"],
                                row["video_end_s"],
                            ),
                        )
                        report["inserted"] += 1
                        continue
                    if _rows_conflict(existing, row, pbp_hash):
                        raise PersistRefused(
                            "persist refused: occupied play_index does not match this event"
                        )
                    report["noop"] += 1
        except UniqueViolation as exc:
            raise PersistRefused("persist refused: concurrent write conflict") from exc
    return report
