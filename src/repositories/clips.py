"""Clips repository."""

from typing import Optional
from datetime import date
from sqlalchemy import text, bindparam, BigInteger, Date, Text, Integer
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Session
import db.sql_loader


def get_player_clips(
    session: Session,
    player_id: int,
    roles: Optional[list[str]],
    date_from: Optional[date],
    date_to: Optional[date],
    season: Optional[str],
    game_id: Optional[int],
    action_types: Optional[list[str]],
    shot_subtypes: Optional[list[str]],
    outcome: Optional[str],
    status_all: bool,
    cursor_date: Optional[date],
    cursor_id: Optional[int],
    limit: int
):
    """Get clips for a player. Optional filters are spliced in so unused binds stay out of the plan."""
    clauses = []
    params = {
        "pid": player_id,
        "c_date": cursor_date,
        "c_id": cursor_id,
        "limit": limit,
    }
    extra_binds = []

    if roles:
        clauses.append("AND cp.role = ANY(CAST(:roles AS clip_role[]))")
        params["roles"] = roles
        extra_binds.append(bindparam("roles", type_=ARRAY(Text)))
    if date_from is not None:
        clauses.append("AND cp.game_date >= :date_from")
        params["date_from"] = date_from
        extra_binds.append(bindparam("date_from", type_=Date))
    if date_to is not None:
        clauses.append("AND cp.game_date <= :date_to")
        params["date_to"] = date_to
        extra_binds.append(bindparam("date_to", type_=Date))
    if season:
        clauses.append("AND g.season = :season")
        params["season"] = season
        extra_binds.append(bindparam("season", type_=Text))
    if game_id is not None:
        clauses.append("AND c.game_id = :game_id")
        params["game_id"] = game_id
        extra_binds.append(bindparam("game_id", type_=BigInteger))
    if action_types:
        clauses.append("AND c.action_type = ANY(:action_types)")
        params["action_types"] = action_types
        extra_binds.append(bindparam("action_types", type_=ARRAY(Text)))
    if shot_subtypes:
        clauses.append("AND c.shot_subtype = ANY(:subtypes)")
        params["subtypes"] = shot_subtypes
        extra_binds.append(bindparam("subtypes", type_=ARRAY(Text)))
    if outcome:
        clauses.append("AND c.outcome = :outcome")
        params["outcome"] = outcome
        extra_binds.append(bindparam("outcome", type_=Text))
    if not status_all:
        clauses.append("AND c.status = 'uploaded' AND c.s3_key IS NOT NULL")
    else:
        params["status_all"] = True

    sql = db.sql_loader.get_raw_sql("clips/by_player").replace(
        "/*dynamic_filters*/",
        ("\n    " + "\n    ".join(clauses)) if clauses else "",
    )
    stmt = text(sql)
    binds = [
        bindparam("pid", type_=BigInteger),
        bindparam("c_date", type_=Date),
        bindparam("c_id", type_=BigInteger),
        bindparam("limit", type_=Integer),
        *extra_binds,
    ]
    stmt = stmt.bindparams(*binds)
    return session.execute(stmt, params).mappings().all()


def get_clip(session: Session, clip_id: int):
    """Get a single clip by ID."""
    result = session.execute(db.sql_loader.registry["clips/get"], {"id": clip_id}).mappings().first()
    return result


def get_playback_target(session: Session, clip_id: int):
    """Get minimal clip data needed for playback URL generation."""
    result = session.execute(db.sql_loader.registry["clips/playback_target"], {"id": clip_id}).mappings().first()
    return result
