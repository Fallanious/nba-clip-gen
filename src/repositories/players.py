"""Player repository."""

from typing import Optional
from sqlalchemy.orm import Session
import db.sql_loader


def search_players(
    session: Session,
    q: Optional[str],
    team_id: Optional[int],
    active: Optional[bool],
    cursor_score: Optional[float],
    cursor_last_name: Optional[str],
    cursor_id: Optional[int],
    limit: int
):
    """Search or browse players."""
    if q:
        # Search with similarity
        params = {
            "q": q,
            "team_id": team_id,
            "active": active,
            "c_score": cursor_score,
            "c_last_name": cursor_last_name,
            "c_id": cursor_id,
            "limit": limit,
        }
        return session.execute(db.sql_loader.registry["players/search"], params).mappings().all()
    else:
        # Browse alphabetically
        params = {
            "team_id": team_id,
            "active": active,
            "c_last_name": cursor_last_name,
            "c_id": cursor_id,
            "limit": limit,
        }
        return session.execute(db.sql_loader.registry["players/search_browse"], params).mappings().all()


def get_player(session: Session, player_id: int):
    """Get a single player by ID."""
    result = session.execute(db.sql_loader.registry["players/get"], {"id": player_id}).mappings().first()
    return result


def get_player_role_counts(session: Session, player_id: int):
    """Get clip counts per role for a player."""
    rows = session.execute(db.sql_loader.registry["players/role_counts"], {"id": player_id}).mappings().all()
    return {row["role"]: row["n"] for row in rows}


def player_exists(session: Session, player_id: int) -> bool:
    """Check if a player exists."""
    result = session.execute(
        db.sql_loader.registry["players/exists"],
        {"id": player_id}
    ).mappings().first()
    return result is not None
