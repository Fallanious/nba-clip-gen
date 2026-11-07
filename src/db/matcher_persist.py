"""Persist matcher results onto stored PBP identity. No HTTP, S3, or Ollama."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from psycopg.errors import DataError, IntegrityError

from db.analyzed_source import IMMUTABLE_IDENTITY_KINDS
from db.bounds import CLIP_MAX_DURATION_S
from db.capability import FIXTURE_PREFIX, PROVENANCE_ATTESTED, SOURCE_ORIGINAL
from db.migrate import _LOCAL_HOSTS, MigrationRefused, assert_migration_target_allowed, parse_host
from db.pbp_identity import (
    MAX_PBP_PLAYS,
    matcher_offsets,
    normalize_pbp,
    pbp_clock_timedelta,
    play_participants,
    resolve_matcher_pbp_index,
)
from db.sql_loader import load_query_sql

MATCHER_VERSION = "match_play_timestamps/persist"
_SHA256_IDENTITY = re.compile(r"^sha256:[0-9a-f]{64}$")

# Attestation CHECK / constraint failures only. Locking, connectivity, and
# programming faults must propagate out of the per-play savepoint.
EXPECTED_ATTESTATION_ERRORS = (IntegrityError, DataError)


class PersistRefused(RuntimeError):
    pass


@dataclass
class PersistConfig:
    database_url: str
    game_bbref_id: str
    commit: bool = False
    max_plays: int = MAX_PBP_PLAYS


@dataclass
class PersistReport:
    dry_run: bool
    game_id: Optional[int] = None
    source_version: Optional[str] = None
    upserted: int = 0
    attested: int = 0
    unmatched: int = 0
    kept_prior: int = 0
    players_linked: int = 0
    skipped_reason: Optional[str] = None
    play_indexes: list[int] = field(default_factory=list)


def persist_enabled() -> bool:
    return os.getenv("MATCHER_PERSIST", "") == "1"


def persist_commit_enabled() -> bool:
    return os.getenv("MATCHER_PERSIST_COMMIT", "") == "1"


def _canonical_token(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise PersistRefused(f"persist refused: {field} must be a nonempty canonical token")
    return value


def validate_analyzed_source(
    *,
    key: str,
    identity: str,
    kind: str,
    bucket: Optional[str] = None,
) -> tuple[str, str, str]:
    key = _canonical_token(key, field="analyzed_source_key")
    identity = _canonical_token(identity, field="analyzed_source_identity")
    kind = _canonical_token(kind, field="analyzed_identity_kind")
    if kind not in IMMUTABLE_IDENTITY_KINDS:
        raise PersistRefused(
            "persist refused: analyzed identity kind is not immutable "
            "(ETag is not accepted)"
        )
    if kind == "content_sha256" and _SHA256_IDENTITY.fullmatch(identity) is None:
        raise PersistRefused("persist refused: content_sha256 identity is malformed")
    if kind == "object_version_id" and identity.startswith("sha256:"):
        raise PersistRefused("persist refused: object_version_id must be captured at download")
    if bucket is not None and str(bucket).strip():
        raise PersistRefused("persist refused: source bucket is not used in this local slice")
    lowered = key.lower()
    if lowered.startswith("http://") or lowered.startswith("https://") or lowered.startswith("s3://"):
        raise PersistRefused("persist refused: analyzed source key must not be a URL")
    if not key.startswith(FIXTURE_PREFIX):
        raise PersistRefused("persist refused: this slice allows fixtures/ keys only")
    return key, identity, kind


def parse_matcher_output(document: Any) -> tuple[dict[str, str], list[Mapping[str, Any]]]:
    """Identity/key/kind/run come from matcher output. Env and DB do not fill them."""
    if not isinstance(document, Mapping):
        raise PersistRefused("persist refused: matcher output missing analyzed_source envelope")
    source = document.get("analyzed_source")
    plays = document.get("plays")
    if not isinstance(source, Mapping):
        raise PersistRefused("persist refused: matcher output missing analyzed_source envelope")
    if not isinstance(plays, list):
        raise PersistRefused("persist refused: matcher output plays must be a list")
    run_id = document.get("matcher_run_id") or source.get("matcher_run_id")
    version = document.get("matcher_version")
    if version is None:
        version = source.get("matcher_version")
    key, identity, kind = validate_analyzed_source(
        key=source.get("analyzed_source_key") or "",
        identity=source.get("analyzed_source_identity") or "",
        kind=source.get("analyzed_identity_kind") or "",
        bucket=source.get("analyzed_source_bucket"),
    )
    return {
        "analyzed_source_key": key,
        "analyzed_source_identity": identity,
        "analyzed_identity_kind": kind,
        "matcher_run_id": _canonical_token(run_id, field="matcher_run_id"),
        "matcher_version": _canonical_token(version, field="matcher_version"),
    }, plays


def assert_persist_target_allowed(database_url: str) -> None:
    """Guard before connect. Local-only for this slice; migrate DSN/APP_ENV still apply."""
    try:
        assert_migration_target_allowed(database_url)
    except MigrationRefused as exc:
        raise PersistRefused(str(exc)) from exc
    host = parse_host(database_url)
    if host not in _LOCAL_HOSTS:
        raise PersistRefused("persist refused: local target required")


def config_from_env(*, game_bbref_id: str) -> PersistConfig:
    url = os.getenv("DATABASE_URL", "")
    if not url.strip():
        raise PersistRefused("persist refused: missing DATABASE_URL")
    assert_persist_target_allowed(url)
    commit = persist_commit_enabled()
    max_plays = int(os.getenv("MATCHER_PERSIST_MAX_PLAYS") or MAX_PBP_PLAYS)
    if max_plays < 1 or max_plays > MAX_PBP_PLAYS:
        raise PersistRefused("persist refused: max plays out of bound")
    bbref = (game_bbref_id or os.getenv("MATCHER_PERSIST_GAME_BBREF_ID") or "").strip()
    if not bbref:
        raise PersistRefused("persist refused: missing game bbref_id")
    return PersistConfig(
        database_url=url,
        game_bbref_id=bbref,
        commit=commit,
        max_plays=max_plays,
    )


def _sql(name: str) -> str:
    return load_query_sql(f"persist/{name}")


def _window_within_trusted_duration(start, end, ts, duration) -> bool:
    if duration is None:
        return False
    try:
        if duration <= 0:
            return False
        if ts < 0 or start < 0 or end <= start:
            return False
        if (end - start) > CLIP_MAX_DURATION_S:
            return False
        if start >= duration or end > duration:
            return False
    except TypeError:
        return False
    return True


def _attested_incoming(pbp: list[dict[str, Any]], matcher_plays: list[Mapping[str, Any]]) -> dict[int, dict[str, Any]]:
    by_index = {int(p["play_index"]): p for p in pbp}
    attested: dict[int, dict[str, Any]] = {}
    seen: set[int] = set()
    for row in matcher_plays:
        idx = resolve_matcher_pbp_index(row, pbp)
        if idx is None or idx not in by_index:
            continue
        if idx in seen:
            raise PersistRefused("persist refused: duplicate matcher original play_index")
        seen.add(idx)
        offsets = matcher_offsets(row)
        if offsets is None:
            continue
        start, end, ts = offsets
        attested[idx] = {
            "video_start_s": start,
            "video_end_s": end,
            "video_timestamp_s": ts,
        }
    return attested


def persist_pbp_and_matcher(
    pbp_plays: list[Mapping[str, Any]],
    matcher_output: Any,
    config: PersistConfig,
    *,
    conn=None,
) -> PersistReport:
    """Transactionally upsert PBP, attest valid matches, preserve prior verified mapping."""
    assert_persist_target_allowed(config.database_url)
    analyzed, matcher_plays = parse_matcher_output(matcher_output)
    key = analyzed["analyzed_source_key"]
    identity = analyzed["analyzed_source_identity"]
    run_id = analyzed["matcher_run_id"]
    matcher_version = analyzed["matcher_version"]
    pbp = normalize_pbp(pbp_plays, max_plays=config.max_plays)
    if len(matcher_plays) > config.max_plays:
        raise PersistRefused("persist refused: matcher batch exceeds bound")
    incoming = _attested_incoming(pbp, list(matcher_plays))
    report = PersistReport(dry_run=not config.commit, play_indexes=[p["play_index"] for p in pbp])
    if not config.commit:
        report.upserted = len(pbp)
        report.attested = len(incoming)
        report.unmatched = len(pbp) - len(incoming)
        report.source_version = identity
        return report

    own_conn = conn is None
    if own_conn:
        import psycopg
        from psycopg.rows import dict_row
        conn = psycopg.connect(config.database_url, row_factory=dict_row)
    try:
        conn.autocommit = False
        game = conn.execute(_sql("lookup_game"), {"bbref_id": config.game_bbref_id}).fetchone()
        if game is None:
            raise PersistRefused("persist refused: game not found")
        if game.get("is_fictional") is not True:
            raise PersistRefused("persist refused: this slice requires is_fictional is True")
        if game.get("source_kind") != SOURCE_ORIGINAL:
            raise PersistRefused("persist refused: game is not original_private")
        game_key = game.get("s3_key")
        game_identity = game.get("source_version")
        if game_key != key or game_identity != identity:
            raise PersistRefused("persist refused: analyzed source does not match locked game")
        report.game_id = game["id"]
        report.source_version = identity
        duration = game.get("duration_s")

        player_ids: dict[str, int] = {}

        for play in pbp:
            clock = pbp_clock_timedelta(play.get("time") or play.get("game_clock"))
            period = play.get("quarter") or play.get("period")
            if not isinstance(period, int) or isinstance(period, bool) or period < 1 or period > 10:
                raise PersistRefused("persist refused: invalid period")
            description = play.get("description") or ""
            if not isinstance(description, str):
                raise PersistRefused("persist refused: description must be text")
            row = conn.execute(
                _sql("upsert_play_metadata"),
                {
                    "game_id": game["id"],
                    "game_date": game["game_date"],
                    "play_index": play["play_index"],
                    "period": period,
                    "game_clock": clock,
                    "description": description,
                    "action_type": play.get("action_type") or play.get("primary_action"),
                    "outcome": play.get("outcome"),
                },
            ).fetchone()
            if row is None:
                raise PersistRefused(
                    "persist refused: play identity mismatch at "
                    f"play_index={play['play_index']}"
                )
            report.upserted += 1
            clip_id = row["id"]
            attested = incoming.get(play["play_index"])
            prior_verified = (
                row["timing_provenance"] == PROVENANCE_ATTESTED
                and row["matched_source_version"] == identity
            )
            applied = False
            if attested and _window_within_trusted_duration(
                attested["video_start_s"], attested["video_end_s"],
                attested["video_timestamp_s"], duration,
            ):
                conn.execute("SAVEPOINT persist_play")
                try:
                    updated = conn.execute(
                        _sql("apply_attestation"),
                        {
                            "clip_id": clip_id,
                            "game_id": game["id"],
                            "play_index": play["play_index"],
                            "video_start_s": attested["video_start_s"],
                            "video_end_s": attested["video_end_s"],
                            "video_timestamp_s": attested["video_timestamp_s"],
                            "matcher_run_id": run_id,
                            "matcher_version": matcher_version,
                            "analyzed_source_key": key,
                            "analyzed_source_identity": identity,
                        },
                    ).fetchone()
                    conn.execute("RELEASE SAVEPOINT persist_play")
                    if updated is not None:
                        report.attested += 1
                        applied = True
                except EXPECTED_ATTESTATION_ERRORS:
                    conn.execute("ROLLBACK TO SAVEPOINT persist_play")
                    conn.execute("RELEASE SAVEPOINT persist_play")
            if not applied:
                if prior_verified:
                    report.kept_prior += 1
                else:
                    report.unmatched += 1
                    conn.execute(
                        _sql("clear_unmatched_timing"),
                        {
                            "clip_id": clip_id,
                            "game_id": game["id"],
                            "play_index": play["play_index"],
                            "analyzed_source_identity": identity,
                        },
                    )
            conn.execute(_sql("replace_clip_players"), {"clip_id": clip_id})
            for part in play_participants(play):
                bbref = part["bbref_id"]
                if bbref not in player_ids:
                    player = conn.execute(_sql("lookup_player"), {"bbref_id": bbref}).fetchone()
                    if player is None:
                        raise PersistRefused("persist refused: participant player not found")
                    player_ids[bbref] = player["id"]
                conn.execute(
                    _sql("insert_clip_player"),
                    {
                        "clip_id": clip_id,
                        "player_id": player_ids[bbref],
                        "role": part["role"],
                        "game_date": game["game_date"],
                    },
                )
                report.players_linked += 1
        conn.commit()
        return report
    except Exception:
        conn.rollback()
        raise
    finally:
        if own_conn:
            conn.close()


def maybe_persist_after_match(
    *,
    raw_pbp: list[Mapping[str, Any]],
    matcher_output: Any,
    game_bbref_id: Optional[str] = None,
) -> PersistReport:
    """Entry point from match_timestamps. No-op unless MATCHER_PERSIST=1."""
    if not persist_enabled():
        return PersistReport(dry_run=True, skipped_reason="MATCHER_PERSIST is not 1")
    config = config_from_env(game_bbref_id=game_bbref_id or "")
    return persist_pbp_and_matcher(list(raw_pbp), matcher_output, config)
