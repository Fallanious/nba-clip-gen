"""Persist matcher results onto stored PBP identity. No HTTP, S3, or Ollama."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from db.capability import PROVENANCE_ATTESTED, PROVENANCE_NONE
from db.migrate import MigrationRefused, assert_migration_target_allowed
from db.pbp_identity import (
    MAX_PBP_PLAYS,
    matcher_offsets,
    normalize_pbp,
    pbp_clock_timedelta,
    resolve_matcher_pbp_index,
    _unique_natural_key,
)
from db.sql_loader import load_query_sql

MATCHER_VERSION = "match_play_timestamps/persist"


class PersistRefused(RuntimeError):
    pass


@dataclass
class PersistConfig:
    database_url: str
    game_bbref_id: str
    matcher_run_id: str
    player_bbref_id: Optional[str] = None
    player_role: str = "shot"
    matcher_version: str = MATCHER_VERSION
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


def config_from_env(*, game_bbref_id: str, matcher_run_id: str, player_bbref_id: Optional[str] = None) -> PersistConfig:
    url = os.getenv("DATABASE_URL", "")
    if not url.strip():
        raise PersistRefused("persist refused: missing DATABASE_URL")
    assert_migration_target_allowed(url)
    commit = persist_commit_enabled()
    max_plays = int(os.getenv("MATCHER_PERSIST_MAX_PLAYS") or MAX_PBP_PLAYS)
    if max_plays < 1 or max_plays > MAX_PBP_PLAYS:
        raise PersistRefused("persist refused: max plays out of bound")
    bbref = (game_bbref_id or os.getenv("MATCHER_PERSIST_GAME_BBREF_ID") or "").strip()
    if not bbref:
        raise PersistRefused("persist refused: missing game bbref_id")
    run_id = (matcher_run_id or os.getenv("MATCHER_PERSIST_RUN_ID") or "").strip()
    if not run_id:
        raise PersistRefused("persist refused: missing matcher_run_id")
    player = player_bbref_id if player_bbref_id is not None else os.getenv("MATCHER_PERSIST_PLAYER_BBREF_ID")
    player = player.strip() if isinstance(player, str) and player.strip() else None
    return PersistConfig(
        database_url=url,
        game_bbref_id=bbref,
        matcher_run_id=run_id,
        player_bbref_id=player,
        commit=commit,
        max_plays=max_plays,
        matcher_version=(os.getenv("MATCHER_PERSIST_MATCHER_VERSION") or MATCHER_VERSION).strip(),
    )


def _sql(name: str) -> str:
    return load_query_sql(f"persist/{name}")


def _attested_incoming(pbp: list[dict[str, Any]], matcher_plays: list[Mapping[str, Any]]) -> dict[int, dict[str, Any]]:
    natural = _unique_natural_key(pbp)
    by_index = {int(p["play_index"]): p for p in pbp}
    attested: dict[int, dict[str, Any]] = {}
    for row in matcher_plays:
        idx = resolve_matcher_pbp_index(row, pbp, natural_keys=natural)
        if idx is None or idx not in by_index:
            continue
        if idx in attested:
            # Identity collision: keep the first valid mapping. Do not apply extras.
            continue
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
    matcher_plays: list[Mapping[str, Any]],
    config: PersistConfig,
    *,
    conn=None,
) -> PersistReport:
    """Transactionally upsert PBP, attest valid matches, preserve prior verified mapping."""
    pbp = normalize_pbp(pbp_plays, max_plays=config.max_plays)
    if len(matcher_plays) > config.max_plays:
        raise PersistRefused("persist refused: matcher batch exceeds bound")
    incoming = _attested_incoming(pbp, list(matcher_plays))
    report = PersistReport(dry_run=not config.commit, play_indexes=[p["play_index"] for p in pbp])
    if not config.commit:
        report.upserted = len(pbp)
        report.attested = len(incoming)
        report.unmatched = len(pbp) - len(incoming)
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
        source_version = game["source_version"]
        if not isinstance(source_version, str) or not source_version.strip():
            raise PersistRefused("persist refused: game missing source_version")
        report.game_id = game["id"]
        report.source_version = source_version
        player_id = None
        if config.player_bbref_id:
            player = conn.execute(_sql("lookup_player"), {"bbref_id": config.player_bbref_id}).fetchone()
            if player is None:
                raise PersistRefused("persist refused: player not found")
            player_id = player["id"]

        for play in pbp:
            clock = pbp_clock_timedelta(play.get("time") or play.get("game_clock"))
            period = play.get("quarter") or play.get("period")
            if not isinstance(period, int) or isinstance(period, bool) or period < 1 or period > 10:
                raise PersistRefused("persist refused: invalid period")
            row = conn.execute(
                _sql("upsert_play_metadata"),
                {
                    "game_id": game["id"],
                    "game_date": game["game_date"],
                    "play_index": play["play_index"],
                    "period": period,
                    "game_clock": clock,
                    "description": play.get("description") or "",
                    "action_type": play.get("action_type") or play.get("primary_action"),
                    "outcome": play.get("outcome"),
                },
            ).fetchone()
            report.upserted += 1
            clip_id = row["id"]
            attested = incoming.get(play["play_index"])
            prior_verified = (
                row["timing_provenance"] == PROVENANCE_ATTESTED
                and row["matched_source_version"] == source_version
            )
            if attested:
                conn.execute("SAVEPOINT persist_play")
                try:
                    conn.execute(
                        _sql("apply_attestation"),
                        {
                            "clip_id": clip_id,
                            "game_id": game["id"],
                            "play_index": play["play_index"],
                            "video_start_s": attested["video_start_s"],
                            "video_end_s": attested["video_end_s"],
                            "video_timestamp_s": attested["video_timestamp_s"],
                            "matcher_run_id": config.matcher_run_id,
                            "matcher_version": config.matcher_version,
                            "source_version": source_version,
                        },
                    )
                    conn.execute("RELEASE SAVEPOINT persist_play")
                    report.attested += 1
                except Exception:
                    conn.execute("ROLLBACK TO SAVEPOINT persist_play")
                    conn.execute("RELEASE SAVEPOINT persist_play")
                    if prior_verified:
                        report.kept_prior += 1
                    else:
                        report.unmatched += 1
            else:
                if prior_verified:
                    report.kept_prior += 1
                else:
                    report.unmatched += 1
            if player_id is not None:
                conn.execute(
                    _sql("insert_clip_player"),
                    {
                        "clip_id": clip_id,
                        "player_id": player_id,
                        "role": config.player_role,
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
    matcher_plays: list[Mapping[str, Any]],
    game_bbref_id: Optional[str] = None,
    matcher_run_id: Optional[str] = None,
    player_bbref_id: Optional[str] = None,
) -> PersistReport:
    """Entry point from match_timestamps. No-op unless MATCHER_PERSIST=1."""
    if not persist_enabled():
        return PersistReport(dry_run=True, skipped_reason="MATCHER_PERSIST is not 1")
    config = config_from_env(
        game_bbref_id=game_bbref_id or "",
        matcher_run_id=matcher_run_id or "",
        player_bbref_id=player_bbref_id,
    )
    return persist_pbp_and_matcher(list(raw_pbp), list(matcher_plays), config)
