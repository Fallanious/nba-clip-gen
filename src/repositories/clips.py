"""Clips repository."""

from typing import Optional
from sqlalchemy.orm import Session
import db.sql_loader
from datetime import date


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
    """Get clips for a player with filters and pagination."""
    params = {
        "pid": player_id,
        "roles": roles,
        "date_from": date_from,
        "date_to": date_to,
        "season": season,
        "game_id": game_id,
        "action_types": action_types,
        "subtypes": shot_subtypes,
        "outcome": outcome,
        "status_all": status_all,
        "c_date": cursor_date,
        "c_id": cursor_id,
        "limit": limit,
    }
    return session.execute(db.sql_loader.registry["clips/by_player"], params).mappings().all()


def get_clip(session: Session, clip_id: int):
    """Get a single clip by ID."""
    result = session.execute(db.sql_loader.registry["clips/get"], {"id": clip_id}).mappings().first()
    return result


def get_playback_target(session: Session, clip_id: int):
    """Get minimal clip data needed for playback URL generation."""
    result = session.execute(db.sql_loader.registry["clips/playback_target"], {"id": clip_id}).mappings().first()
    return result
