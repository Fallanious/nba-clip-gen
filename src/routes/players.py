"""Players who have clips, and the plays for one player."""

import os
from datetime import timedelta
from decimal import Decimal

from flask import Blueprint, jsonify

from routes.videos import parse_video_filename

bp = Blueprint("players", __name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROLE_LABELS = {
    "shot": "Shots",
    "assist": "Assists",
    "rebound": "Rebounds",
    "block": "Blocks",
    "steal": "Steals",
    "foul": "Fouls",
    "fouled": "Fouled",
    "turnover": "Turnovers",
}

POSITION_LABELS = {
    "PG": "Guard",
    "SG": "Guard",
    "G": "Guard",
    "SF": "Forward",
    "PF": "Forward",
    "F": "Forward",
    "C": "Center",
}


def position_label(position):
    """Turn a roster code into the word on the player card."""
    if not position:
        return ""
    return POSITION_LABELS.get(position, position)


def clock_label(value):
    """Game clock interval as M:SS."""
    if not isinstance(value, timedelta):
        return ""
    total = int(value.total_seconds())
    minutes, seconds = divmod(abs(total), 60)
    return f"{minutes}:{seconds:02d}"


def duration_label(value):
    """Clip length as M:SS."""
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, str):
        return ""
    try:
        total = int(round(float(value)))
    except (TypeError, ValueError):
        return ""
    if total < 0:
        return ""
    minutes, seconds = divmod(total, 60)
    return f"{minutes}:{seconds:02d}"


def season_label(season):
    """2026-27 becomes 2026–27, matching the archive line."""
    if not season:
        return ""
    return str(season).replace("-", "–")


def matchup_label(player_team, home, away):
    """The player's team first, then the other team."""
    if player_team and player_team == home:
        return f"{home} vs {away}"
    if player_team and player_team == away:
        return f"{away} vs {home}"
    return f"{away} at {home}"


def film_paths_by_game(film_dir):
    """Map a game id to the local film file, when that file is on disk."""
    found = {}
    if not os.path.isdir(film_dir):
        return found
    for name in os.listdir(film_dir):
        if not name.lower().endswith((".mp4", ".mkv", ".webm")):
            continue
        parsed = parse_video_filename(name)
        if parsed:
            found[parsed["bbref_id"]] = f"film/{name}"
    return found


def _db_params():
    from persist_matcher_clips import PersistRefused, assert_persist_ready, direct_database_url

    try:
        return assert_persist_ready(direct_database_url(os.environ.get("DATABASE_URL", ""))), None
    except PersistRefused as exc:
        return None, (jsonify({"error": str(exc)}), 400)


def _open(params):
    import psycopg

    try:
        return psycopg.connect(**params, connect_timeout=8), None
    except Exception:
        return None, (jsonify({"error": "Database login failed."}), 503)


@bp.route("/api/players")
def list_players_with_clips():
    """Players that already have at least one clip link."""
    params, error = _db_params()
    if error:
        return error
    conn, error = _open(params)
    if error:
        return error
    with conn:
        seasons = [
            season_label(row[0])
            for row in conn.execute(
                """
                SELECT DISTINCT g.season
                FROM games g
                JOIN clips c ON c.game_id = g.id
                ORDER BY g.season
                """
            )
        ]
        rows = conn.execute(
            """
            SELECT p.id, p.bbref_id, p.full_name, p.position, p.headshot_url,
                   t.abbr,
                   COUNT(DISTINCT cp.clip_id),
                   COUNT(DISTINCT c.game_id)
            FROM players p
            JOIN clip_players cp ON cp.player_id = p.id
            JOIN clips c ON c.id = cp.clip_id
            LEFT JOIN teams t ON t.id = p.team_id
            GROUP BY p.id, t.abbr
            ORDER BY p.full_name
            """
        ).fetchall()
    players = [
        {
            "id": row[0],
            "bbref_id": row[1],
            "full_name": row[2],
            "position": position_label(row[3]),
            "headshot_url": row[4],
            "team": row[5] or "",
            "clips": row[6],
            "games": row[7],
        }
        for row in rows
    ]
    return jsonify({"season": seasons[0] if len(seasons) == 1 else "", "seasons": seasons, "players": players})


@bp.route("/api/players/<int:player_id>/clips")
def list_player_clips(player_id):
    """Plays linked to one player, in film order."""
    params, error = _db_params()
    if error:
        return error
    conn, error = _open(params)
    if error:
        return error
    films = film_paths_by_game(os.path.join(BASE_DIR, "film"))
    with conn:
        player = conn.execute(
            """
            SELECT p.full_name, t.abbr
            FROM players p
            LEFT JOIN teams t ON t.id = p.team_id
            WHERE p.id = %s
            """,
            (player_id,),
        ).fetchone()
        if player is None:
            return jsonify({"error": "Player not found."}), 404
        rows = conn.execute(
            """
            SELECT c.id, cp.role::text, c.description, c.period, c.game_clock,
                   c.duration_s, c.video_start_s, c.video_end_s,
                   g.bbref_id, g.game_date, g.season, ht.abbr, at.abbr
            FROM clip_players cp
            JOIN clips c ON c.id = cp.clip_id
            JOIN games g ON g.id = c.game_id
            JOIN teams ht ON ht.id = g.home_team_id
            JOIN teams at ON at.id = g.away_team_id
            WHERE cp.player_id = %s
            ORDER BY c.video_start_s, c.play_index, cp.role::text
            """,
            (player_id,),
        ).fetchall()
    clips = []
    for row in rows:
        clips.append(
            {
                "id": row[0],
                "role": row[1],
                "role_label": ROLE_LABELS.get(row[1], row[1]),
                "description": row[2],
                "period": row[3],
                "game_clock": clock_label(row[4]),
                "duration_label": duration_label(row[5]),
                "video_start_s": float(row[6]),
                "video_end_s": float(row[7]),
                "bbref_id": row[8],
                "game_date": row[9].isoformat(),
                "season": season_label(row[10]),
                "matchup": matchup_label(player[1], row[11], row[12]),
                "film_path": films.get(row[8]),
            }
        )
    return jsonify({"player": player[0], "team": player[1] or "", "clips": clips})
