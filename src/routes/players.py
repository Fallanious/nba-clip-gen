"""Players and Clips API blueprint."""

import re
import json
import hashlib
from datetime import date, datetime
from typing import Optional
from flask import Blueprint, request, g, jsonify, make_response
from sqlalchemy.orm import Session

from auth import require_user
from authz import authorize_player, authorize_clip, require_admin
from db import session as db_session
from errors import ApiError
from pagination import encode_cursor, decode_cursor
from repositories import players as players_repo
from repositories import clips as clips_repo

try:
    from services.playback import get_playback_url, _allow_fallback
except ModuleNotFoundError as e:
    # Only swallow a missing playback layer, not import failures inside it.
    if e.name not in {"services", "services.playback"}:
        raise
    get_playback_url = None
    def _allow_fallback():
        from config import config as _cfg
        return _cfg.PLAYBACK_ALLOW_FALLBACK

bp = Blueprint('players', __name__, url_prefix='/api')

ENUM_LIST_MAX = 20
_PLAYERS_LIST_CC = "private, max-age=60"
_RESOURCE_CC = "private, max-age=300"


def _clip_is_playable(row) -> bool:
    status = row.get("status")
    if row.get("has_object") is not None:
        return status == "uploaded" and bool(row.get("has_object"))
    return status == "uploaded" and row.get("s3_key") is not None


def _require_published_or_admin(user, row) -> None:
    """Unpublished clips are admin-only on detail and playback, matching the list."""
    if not _clip_is_playable(row):
        require_admin(user)


def _weak_etag(parts) -> str:
    raw = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    return f'W/"{hashlib.md5(raw.encode("utf-8")).hexdigest()}"'


def _not_modified(etag: str, cache_control: str):
    """304 must repeat ETag, Cache-Control, and Vary. Empty body."""
    resp = make_response(b"", 304)
    resp.headers["ETag"] = etag
    resp.headers["Cache-Control"] = cache_control
    resp.headers["Vary"] = "Origin"
    return resp


def _json_cached(payload, etag: str, cache_control: str):
    result = jsonify(payload)
    result.headers["ETag"] = etag
    result.headers["Cache-Control"] = cache_control
    result.headers["Vary"] = "Origin"
    return result


def _reject_empty(param_name: str, raw: Optional[str]) -> str:
    if raw is None or not str(raw).strip():
        raise ApiError(
            code="invalid_param",
            message=f"{param_name} cannot be empty",
            status=400,
            details={"param": param_name},
        )
    return str(raw).strip()


def _optional_cursor() -> Optional[str]:
    """Absent cursor -> None (first page). Present empty/whitespace -> 400."""
    cursor_str = request.args.get("cursor")
    if cursor_str is None:
        return None
    if not str(cursor_str).strip():
        raise ApiError(
            code="invalid_cursor",
            message="Invalid cursor format",
            status=400,
        )
    return str(cursor_str)


def _clips_cursor_filters(
    player_id,
    roles,
    date_from,
    date_to,
    season,
    game_id,
    action_types,
    shot_subtypes,
    outcome,
    status_param,
):
    """Single source for encode and decode so filter hashes cannot drift."""
    return {
        "player_id": player_id,
        "roles": sorted(roles) if roles else None,
        "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
        "season": season,
        "game_id": game_id,
        "action_types": sorted(action_types) if action_types else None,
        "shot_subtypes": sorted(shot_subtypes) if shot_subtypes else None,
        "outcome": outcome,
        "status": status_param,
    }


def _parse_int(value: str, param_name: str, min_val: int = 1) -> int:
    """Parse and validate integer parameter."""
    try:
        val = int(value)
        if val < min_val:
            raise ValueError()
        return val
    except (ValueError, TypeError):
        raise ApiError(
            code="invalid_param",
            message=f"{param_name} must be >= {min_val}",
            status=400,
            details={"param": param_name}
        )


def _parse_bool(value: str, param_name: str) -> Optional[bool]:
    """Parse boolean parameter."""
    if value is None:
        return None
    if value == "":
        raise ApiError(
            code="invalid_param",
            message=f"{param_name} cannot be empty",
            status=400,
            details={"param": param_name}
        )
    if value.lower() == "true":
        return True
    elif value.lower() == "false":
        return False
    elif value.lower() == "all":
        return None
    else:
        raise ApiError(
            code="invalid_param",
            message=f"{param_name} must be true, false, or all",
            status=400,
            details={"param": param_name}
        )


def _parse_date(value: str, param_name: str) -> date:
    """Parse ISO date parameter."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise ApiError(
            code="invalid_param",
            message=f"{param_name} must be YYYY-MM-DD format",
            status=400,
            details={"param": param_name}
        )


def _parse_team_abbr(value: str) -> Optional[int]:
    """Parse and validate team abbreviation.
    
    Returns team_id or None. Raises ApiError if format invalid or team not found.
    """
    if value is None:
        return None
    
    # Empty string or only whitespace is invalid
    if value == "" or not value.strip():
        raise ApiError(
            code="invalid_param",
            message="team cannot be empty",
            status=400,
            details={"param": "team"}
        )
    
    value = value.strip()

    value = value.upper()
    if not re.match(r'^[A-Z]{3}$', value):
        raise ApiError(
            code="invalid_param",
            message="team must be a 3-letter abbreviation",
            status=400,
            details={"param": "team"}
        )

    # Look up team by abbreviation using SQL query
    from db.sql_loader import registry
    session = db_session.get_db()
    result = session.execute(
        registry["teams/by_abbr"],
        {"abbr": value}
    ).mappings().first()

    if not result:
        raise ApiError(
            code="invalid_param",
            message="Unknown team",
            status=400,
            details={"param": "team"}
        )

    return result["id"]


def _parse_enum_list(values: str, param_name: str, allowed: set[str]) -> Optional[list[str]]:
    """Parse comma-separated enum list."""
    if not values:
        return None

    # Support both ?role=shot&role=assist and ?role=shot,assist
    if isinstance(values, list):
        items = values
    else:
        items = [v.strip() for v in values.split(',')]
    if any(item == "" for item in items):
        raise ApiError(
            code="invalid_param",
            message=f"{param_name} cannot be empty",
            status=400,
            details={"param": param_name}
        )

    if len(items) > ENUM_LIST_MAX:
        raise ApiError(
            code="invalid_param",
            message=f"{param_name} accepts at most {ENUM_LIST_MAX} values",
            status=400,
            details={"param": param_name}
        )

    items = list(dict.fromkeys(items))  # Dedupe preserving order

    for item in items:
        if item not in allowed:
            raise ApiError(
                code="invalid_param",
                message=f"Invalid {param_name}",
                status=400,
                details={"param": param_name}
            )

    return items if items else None


def _format_game_clock_iso(interval) -> Optional[str]:
    """Format interval as ISO 8601 duration (PT4M12S)."""
    if interval is None:
        return None

    total_seconds = int(interval.total_seconds())
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"PT{minutes}M{seconds}S"


def _format_game_clock_display(interval) -> Optional[str]:
    """Format interval as human-readable time (M:SS)."""
    if interval is None:
        return None

    total_seconds = int(interval.total_seconds())
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes}:{seconds:02d}"


def _serialize_team(row, prefix: str = "") -> Optional[dict]:
    """Serialize team from row."""
    team_id_key = f"{prefix}team_id" if prefix else "team_id"
    abbr_key = f"{prefix}abbr" if prefix else "abbr"
    city_key = f"{prefix}city" if prefix else "city"
    name_key = f"{prefix}name" if prefix else "name"

    if row.get(team_id_key) is None:
        return None

    return {
        "id": str(row[team_id_key]),
        "abbr": row[abbr_key],
        "city": row.get(city_key, ""),
        "name": row.get(name_key, "")
    }


def _serialize_player_item(row) -> dict:
    """Serialize player for list view."""
    return {
        "id": str(row["id"]),
        "full_name": row["full_name"],
        "first_name": row.get("first_name"),
        "last_name": row["last_name"],
        "position": row.get("position"),
        "headshot_url": row.get("headshot_url"),
        "is_active": row["is_active"],
        "team": _serialize_team(row)
    }


def _serialize_player_detail(row, role_counts: dict) -> dict:
    """Serialize player for detail view."""
    base = _serialize_player_item(row)
    base.update({
        "bbref_id": row["bbref_id"],
        "clip_count": row["clip_count"],
        "first_clip_date": row["first_clip_date"].isoformat() if row.get("first_clip_date") else None,
        "last_clip_date": row["last_clip_date"].isoformat() if row.get("last_clip_date") else None,
        "role_counts": role_counts
    })
    return base


def _serialize_clip_row(row) -> dict:
    """Serialize a single clip row (used in player clips endpoint)."""
    return {
        "id": str(row["clip_id"]),
        "play_index": row["play_index"],
        "period": row["period"],
        "game_clock": _format_game_clock_iso(row.get("game_clock")),
        "game_clock_display": _format_game_clock_display(row.get("game_clock")),
        "description": row["description"],
        "action_type": row.get("action_type"),
        "shot_subtype": row.get("shot_subtype"),
        "outcome": row.get("outcome"),
        "duration_s": float(row["duration_s"]) if row.get("duration_s") is not None else None,
        "roles": row["roles"],
        "team_id": str(row["team_id"]) if row.get("team_id") is not None else None,
        "status": row["status"],
        "playable": row["status"] == "uploaded" and row["has_object"]
    }


def _group_clips_by_game(rows: list) -> list[dict]:
    """Group clip rows by game for display."""
    games = []
    current_game = None

    for row in rows:
        game_id = row["game_id"]

        if current_game is None or current_game["game"]["id"] != str(game_id):
            # Start a new game group
            game_obj = {
                "id": str(game_id),
                "bbref_id": row["bbref_id"],
                "date": row["game_date"].isoformat(),
                "season": row["season"],
                "home_abbr": row["home_abbr"],
                "away_abbr": row["away_abbr"]
            }
            current_game = {
                "game": game_obj,
                "continues": False,
                "plays": []
            }
            games.append(current_game)

        # Add play to current game
        current_game["plays"].append(_serialize_clip_row(row))

    # Sort plays within each game by period, play_index
    for game in games:
        game["plays"].sort(key=lambda p: (p["period"], p["play_index"]))

    return games


@bp.route('/players', methods=['GET'])
@require_user
def search_players_endpoint():
    """GET /api/players - Search or browse players."""
    # Parse parameters
    q = request.args.get('q', '').strip()
    if q and len(q) < 2:
        raise ApiError(
            code="invalid_param",
            message="q must be 2-100 characters",
            status=400,
            details={"param": "q"}
        )

    if q and len(q) > 100:
        raise ApiError(
            code="invalid_param",
            message="q must be 2-100 characters",
            status=400,
            details={"param": "q"}
        )

    team_abbr = request.args.get('team')
    team_id = _parse_team_abbr(team_abbr)

    active_str = request.args.get('active', 'true')
    active = _parse_bool(active_str, 'active')

    limit_str = request.args.get('limit', '25')
    limit = _parse_int(limit_str, 'limit', min_val=1)
    if limit > 100:
        raise ApiError(
            code="invalid_param",
            message="limit must be 1-100",
            status=400,
            details={"param": "limit"}
        )

    # Parse cursor. Empty cursor= is 400, not first page.
    cursor_str = _optional_cursor()
    cursor_score = None
    cursor_last_name = None
    cursor_id = None

    if cursor_str is not None:
        filters = {"q": q, "team": team_id, "active": active}
        cursor_key = decode_cursor(cursor_str, filters)

        if q:
            # Search cursor: [score, last_name, id]
            if len(cursor_key) != 3:
                raise ApiError(
                    code="invalid_cursor",
                    message="Invalid cursor format",
                    status=400
                )
            cursor_score, cursor_last_name, cursor_id = cursor_key
        else:
            # Browse cursor: [last_name, id]
            if len(cursor_key) != 2:
                raise ApiError(
                    code="invalid_cursor",
                    message="Invalid cursor format",
                    status=400
                )
            cursor_last_name, cursor_id = cursor_key

    session = db_session.get_db()
    rows = players_repo.search_players(
        session,
        q=q,
        team_id=team_id,
        active=active,
        cursor_score=cursor_score,
        cursor_last_name=cursor_last_name,
        cursor_id=cursor_id,
        limit=limit
    )

    # Check for next page
    has_next = len(rows) > limit
    if has_next:
        rows = rows[:limit]

    # Serialize
    items = [_serialize_player_item(row) for row in rows]

    # Build next cursor
    next_cursor = None
    if has_next:
        last = rows[-1]
        if q:
            key = [float(last["score"]), last["last_name"], int(last["id"])]
        else:
            key = [last["last_name"], int(last["id"])]

        filters = {"q": q, "team": team_id, "active": active}
        next_cursor = encode_cursor(key, filters)

    response = {
        "items": items,
        "next_cursor": next_cursor,
        "limit": limit
    }
    etag = _weak_etag([
        "GET /api/players",
        q,
        team_id,
        active,
        limit,
        cursor_str,
        [(int(r["id"]), str(r.get("updated_at") or "")) for r in rows],
    ])
    if request.headers.get("If-None-Match") == etag:
        return _not_modified(etag, _PLAYERS_LIST_CC)
    return _json_cached(response, etag, _PLAYERS_LIST_CC)


@bp.route('/players/<int:player_id>', methods=['GET'])
@require_user
def get_player_endpoint(player_id: int):
    """GET /api/players/:id - Get player detail."""
    session = db_session.get_db()

    # Get player
    row = players_repo.get_player(session, player_id)
    if not row:
        raise ApiError(
            code="not_found",
            message="Player not found",
            status=404
        )

    # Check authorization
    authorize_player(g.user, row)

    role_counts = row.get("role_counts") or {}
    if isinstance(role_counts, str):
        role_counts = json.loads(role_counts)
    elif role_counts is None:
        role_counts = {}

    etag = _weak_etag([
        "GET /api/players/:id",
        player_id,
        row.get("clip_count"),
        row.get("last_clip_date"),
        row.get("clips_updated_at"),
        row.get("updated_at"),
        row.get("full_name"),
    ])
    if request.headers.get("If-None-Match") == etag:
        return _not_modified(etag, _RESOURCE_CC)

    # Serialize (role_counts come from the same players/get round trip)
    player = _serialize_player_detail(row, dict(role_counts))
    return _json_cached(player, etag, _RESOURCE_CC)


def _listed_or_none(name: str, allowed: set[str]) -> Optional[list[str]]:
    """Parse a repeated/csv enum query param. Absent -> None; empty/whitespace -> 400."""
    if name not in request.args:
        return None
    raw_list = request.args.getlist(name)
    if any(not str(v).strip() for v in raw_list):
        raise ApiError(
            code="invalid_param",
            message=f"{name} cannot be empty",
            status=400,
            details={"param": name},
        )
    return _parse_enum_list(",".join(raw_list), name, allowed)


@bp.route('/players/<int:player_id>/clips', methods=['GET'])
@require_user
def get_player_clips_endpoint(player_id: int):
    """GET /api/players/:id/clips - Get clips for a player."""
    session = db_session.get_db()

    row = players_repo.get_player(session, player_id)
    if not row:
        raise ApiError(
            code="not_found",
            message="Player not found",
            status=404
        )
    authorize_player(g.user, row)

    VALID_ROLES = {'shot', 'assist', 'block', 'steal', 'rebound', 'foul', 'fouled', 'turnover'}
    roles = _listed_or_none('role', VALID_ROLES)

    VALID_ACTIONS = {'shot', 'rebound', 'steal', 'block', 'turnover', 'foul', 'free_throw', 'other'}
    action_types = _listed_or_none('action_type', VALID_ACTIONS)

    VALID_SUBTYPES = {'dunk', 'layup', 'midrange', 'three_pointer', 'hook', 'floater'}
    shot_subtypes = _listed_or_none('shot_subtype', VALID_SUBTYPES)

    if 'outcome' in request.args:
        outcome = _reject_empty('outcome', request.args.get('outcome'))
        if outcome not in ('made', 'missed'):
            raise ApiError(
                code="invalid_param",
                message="outcome must be 'made' or 'missed'",
                status=400,
                details={"param": "outcome"}
            )
    else:
        outcome = None

    if 'season' in request.args:
        season = _reject_empty('season', request.args.get('season'))
        if not re.match(r'^\d{4}-\d{2}$', season):
            raise ApiError(
                code="invalid_param",
                message="season must match YYYY-YY format",
                status=400,
                details={"param": "season"}
            )
    else:
        season = None

    if 'game_id' in request.args:
        game_id = _parse_int(_reject_empty('game_id', request.args.get('game_id')), 'game_id')
    else:
        game_id = None

    if 'date_from' in request.args:
        date_from = _parse_date(_reject_empty('date_from', request.args.get('date_from')), 'date_from')
    else:
        date_from = None
    if 'date_to' in request.args:
        date_to = _parse_date(_reject_empty('date_to', request.args.get('date_to')), 'date_to')
    else:
        date_to = None

    if date_from and date_to and date_from > date_to:
        raise ApiError(
            code="invalid_range",
            message="date_from must be <= date_to",
            status=422
        )

    if 'status' in request.args:
        status_param = _reject_empty('status', request.args.get('status'))
    else:
        status_param = 'playable'
    if status_param not in ('playable', 'all'):
        raise ApiError(
            code="invalid_param",
            message="status must be 'playable' or 'all'",
            status=400,
            details={"param": "status"}
        )
    status_all = status_param == 'all'
    if status_all:
        require_admin(g.user)

    if 'limit' in request.args:
        limit_str = _reject_empty('limit', request.args.get('limit'))
    else:
        limit_str = '50'
    limit = _parse_int(limit_str, 'limit', min_val=1)
    if limit > 200:
        raise ApiError(
            code="invalid_param",
            message="limit must be 1-200",
            status=400,
            details={"param": "limit"}
        )

    cursor_date = None
    cursor_game_id = None
    cursor_period = None
    cursor_play_index = None
    cursor_id = None
    cursor_str = _optional_cursor()

    filters = _clips_cursor_filters(
        player_id, roles, date_from, date_to, season, game_id,
        action_types, shot_subtypes, outcome, status_param,
    )

    if cursor_str is not None:
        cursor_key = decode_cursor(cursor_str, filters)

        if len(cursor_key) != 5:
            raise ApiError(
                code="invalid_cursor",
                message="Invalid cursor format",
                status=400
            )

        cursor_date_str, cursor_game_id, cursor_period, cursor_play_index, cursor_id = cursor_key
        try:
            cursor_date = datetime.strptime(str(cursor_date_str), "%Y-%m-%d").date()
            cursor_game_id = int(cursor_game_id)
            cursor_period = int(cursor_period)
            cursor_play_index = int(cursor_play_index)
            cursor_id = int(cursor_id)
        except (TypeError, ValueError):
            raise ApiError(
                code="invalid_cursor",
                message="Invalid cursor format",
                status=400,
            )

    etag = _weak_etag([
        "GET /api/players/:id/clips",
        player_id,
        filters,
        row.get("clip_count"),
        row.get("last_clip_date"),
        row.get("clips_updated_at"),
        limit,
        cursor_str,
    ])
    if request.headers.get("If-None-Match") == etag:
        return _not_modified(etag, _RESOURCE_CC)

    rows = clips_repo.get_player_clips(
        session,
        player_id=player_id,
        roles=roles,
        date_from=date_from,
        date_to=date_to,
        season=season,
        game_id=game_id,
        action_types=action_types,
        shot_subtypes=shot_subtypes,
        outcome=outcome,
        status_all=status_all,
        cursor_date=cursor_date,
        cursor_game_id=cursor_game_id,
        cursor_period=cursor_period,
        cursor_play_index=cursor_play_index,
        cursor_id=cursor_id,
        limit=limit
    )

    # Check for next page and save lookahead row before slicing
    has_next = len(rows) > limit
    lookahead_row = rows[limit] if has_next else None
    if has_next:
        rows = rows[:limit]

    # Group by game
    games = _group_clips_by_game(rows)

    # Build next cursor and check continues
    next_cursor = None
    if has_next and rows:
        last = rows[-1]
        key = [
            last["game_date"].isoformat(),
            int(last["game_id"]),
            int(last["period"]),
            int(last["play_index"]),
            int(last["clip_id"]),
        ]
        next_cursor = encode_cursor(key, filters)

        # Mark last game as continuing if lookahead row is same game
        if games and lookahead_row:
            games[-1]["continues"] = (lookahead_row["game_id"] == last["game_id"])

    response = {
        "player_id": str(player_id),
        "limit": limit,
        "next_cursor": next_cursor,
        "item_count": len(rows),
        "games": games
    }
    return _json_cached(response, etag, _RESOURCE_CC)


@bp.route('/clips/<int:clip_id>', methods=['GET'])
@require_user
def get_clip_endpoint(clip_id: int):
    """GET /api/clips/:id - Get clip detail."""
    session = db_session.get_db()

    row = clips_repo.get_clip(session, clip_id)
    if not row:
        raise ApiError(
            code="not_found",
            message="Clip not found",
            status=404
        )

    # Check authorization
    authorize_clip(g.user, row)
    _require_published_or_admin(g.user, row)

    etag = _weak_etag([
        "GET /api/clips/:id",
        clip_id,
        row.get("status"),
        row.get("duration_s"),
        row.get("updated_at"),
        row.get("s3_key"),
    ])
    if request.headers.get("If-None-Match") == etag:
        return _not_modified(etag, _RESOURCE_CC)

    from config import config
    players_json = json.loads(row["players"]) if isinstance(row["players"], str) else row["players"]

    clip = {
        "id": str(row["id"]),
        "game_id": str(row["game_id"]),
        "game_date": row["game_date"].isoformat(),
        "play_index": row["play_index"],
        "period": row["period"],
        "game_clock": _format_game_clock_iso(row.get("game_clock")),
        "game_clock_display": _format_game_clock_display(row.get("game_clock")),
        "description": row["description"],
        "action_type": row.get("action_type"),
        "shot_subtype": row.get("shot_subtype"),
        "outcome": row.get("outcome"),
        "attributes": row.get("attributes", {}),
        "video_start_s": float(row["video_start_s"]),
        "video_end_s": float(row["video_end_s"]),
        "duration_s": float(row["duration_s"]) if row.get("duration_s") is not None else None,
        "status": row["status"],
        "team_id": str(row["team_id"]) if row.get("team_id") is not None else None,
        "game": {
            "bbref_id": row["bbref_id"],
            "season": row["season"],
            "home_abbr": row["home_abbr"],
            "away_abbr": row["away_abbr"],
        },
        "team": {"id": str(row["team_id"]), "abbr": row.get("team_abbr")} if row.get("team_id") is not None else None,
        "players": players_json,
        "playable": row["status"] == "uploaded" and row.get("s3_key") is not None,
        "fallback_available": row["status"] != "uploaded" and row["has_source"] and _allow_fallback()
    }
    return _json_cached(clip, etag, _RESOURCE_CC)


@bp.route('/clips/<int:clip_id>/playback-url', methods=['GET'])
@require_user
def get_playback_url_endpoint(clip_id: int):
    """GET /api/clips/:id/playback-url - Get presigned playback URL."""
    session = db_session.get_db()

    # Get minimal clip data
    clip_data = clips_repo.get_playback_target(session, clip_id)
    if not clip_data:
        raise ApiError(
            code="not_found",
            message="Clip not found",
            status=404
        )

    # Check authorization
    authorize_clip(g.user, clip_data)
    _require_published_or_admin(g.user, clip_data)

    # Parse mode parameter
    mode = request.args.get('mode', 'auto')
    if mode not in ('clip', 'fallback', 'auto'):
        raise ApiError(
            code="invalid_param",
            message="mode must be 'clip', 'fallback', or 'auto'",
            status=400,
            details={"param": "mode"}
        )

    if get_playback_url is None:
        raise ApiError(
            code="storage_unavailable",
            message="Storage service unavailable",
            status=503,
        )

    # Generate playback URL
    result_data = get_playback_url(clip_data, mode)

    result = jsonify(result_data)
    result.headers['Cache-Control'] = 'no-store'
    result.headers['Pragma'] = 'no-cache'
    result.headers['Vary'] = 'Origin'

    return result
