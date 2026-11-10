"""Players who have clips, and one cut of a clip from the S3 game."""

import os
import tempfile
from datetime import timedelta
from decimal import Decimal

from flask import Blueprint, jsonify, send_file

bp = Blueprint("players", __name__)

MAX_CLIP_S = 120

ROLE_LABELS = {
    "shot": "Shot",
    "assist": "Assist",
    "rebound": "Rebound",
    "block": "Block",
    "steal": "Steal",
    "foul": "Foul",
    "fouled": "Fouled",
    "turnover": "Turnover",
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


def clip_window(start_s, end_s):
    """Seconds to keep. A bad row must not ask ffmpeg to cut the whole game."""
    start = float(start_s)
    end = float(end_s)
    if start < 0 or end <= start or end - start > MAX_CLIP_S:
        raise ValueError("Clip window is not usable")
    return start, end


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
    """Metadata for every clip linked to this player. No video bytes."""
    params, error = _db_params()
    if error:
        return error
    conn, error = _open(params)
    if error:
        return error
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
            }
        )
    return jsonify({"player": player[0], "team": player[1] or "", "clips": clips})


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


@bp.route("/api/players/<int:player_id>/clips/<int:clip_id>")
def serve_player_clip(player_id, clip_id):
    """Cut this play from the S3 game and return only that file."""
    params, error = _db_params()
    if error:
        return error
    conn, error = _open(params)
    if error:
        return error
    with conn:
        row = conn.execute(
            """
            SELECT c.video_start_s, c.video_end_s, g.s3_key
            FROM clip_players cp
            JOIN clips c ON c.id = cp.clip_id
            JOIN games g ON g.id = c.game_id
            WHERE cp.player_id = %s AND c.id = %s
            LIMIT 1
            """,
            (player_id, clip_id),
        ).fetchone()
    if row is None:
        return jsonify({"error": "Clip not found."}), 404
    if not row[2]:
        return jsonify({"error": "This game has no film in storage."}), 404
    try:
        start, end = clip_window(row[0], row[1])
    except (TypeError, ValueError):
        return jsonify({"error": "Clip window is not usable."}), 422
    try:
        from services.storage import presign_get_url

        source = presign_get_url(row[2])
    except Exception:
        return jsonify({"error": "Could not open the game film."}), 502

    fd, path = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    try:
        from generate_clips import extract_clip

        ok = extract_clip(source, start, end, path, quiet=True)
        if not ok or os.path.getsize(path) <= 0:
            _remove(path)
            return jsonify({"error": "Could not cut this clip."}), 502
    except Exception:
        _remove(path)
        return jsonify({"error": "Could not cut this clip."}), 502

    response = send_file(
        path,
        mimetype="video/mp4",
        conditional=True,
        download_name=f"{clip_id}.mp4",
    )
    response.headers["Cache-Control"] = "private, max-age=3600"
    response.call_on_close(lambda: _remove(path))
    return response
