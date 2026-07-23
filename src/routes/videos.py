"""Video listing, serving, and metadata endpoints."""

import os
import re
from datetime import datetime
from flask import Blueprint, jsonify, send_from_directory

bp = Blueprint("videos", __name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEAM_CODES = {
    "76ERS": "PHI", "SIXERS": "PHI", "PHILADELPHIA": "PHI",
    "CELTICS": "BOS", "BOSTON": "BOS",
    "KNICKS": "NYK", "NEW YORK": "NYK",
    "NETS": "BRK", "BROOKLYN": "BRK",
    "RAPTORS": "TOR", "TORONTO": "TOR",
    "BULLS": "CHI", "CHICAGO": "CHI",
    "CAVALIERS": "CLE", "CAVS": "CLE", "CLEVELAND": "CLE",
    "PISTONS": "DET", "DETROIT": "DET",
    "PACERS": "IND", "INDIANA": "IND",
    "BUCKS": "MIL", "MILWAUKEE": "MIL",
    "HAWKS": "ATL", "ATLANTA": "ATL",
    "HORNETS": "CHO", "CHARLOTTE": "CHO",
    "HEAT": "MIA", "MIAMI": "MIA",
    "MAGIC": "ORL", "ORLANDO": "ORL",
    "WIZARDS": "WAS", "WASHINGTON": "WAS",
    "NUGGETS": "DEN", "DENVER": "DEN",
    "TIMBERWOLVES": "MIN", "WOLVES": "MIN", "MINNESOTA": "MIN",
    "THUNDER": "OKC", "OKLAHOMA CITY": "OKC",
    "TRAIL BLAZERS": "POR", "BLAZERS": "POR", "PORTLAND": "POR",
    "JAZZ": "UTA", "UTAH": "UTA",
    "WARRIORS": "GSW", "GOLDEN STATE": "GSW",
    "CLIPPERS": "LAC", "LA CLIPPERS": "LAC",
    "LAKERS": "LAL", "LA LAKERS": "LAL",
    "SUNS": "PHO", "PHOENIX": "PHO",
    "KINGS": "SAC", "SACRAMENTO": "SAC",
    "MAVERICKS": "DAL", "MAVS": "DAL", "DALLAS": "DAL",
    "ROCKETS": "HOU", "HOUSTON": "HOU",
    "GRIZZLIES": "MEM", "MEMPHIS": "MEM",
    "PELICANS": "NOP", "NEW ORLEANS": "NOP",
    "SPURS": "SAS", "SAN ANTONIO": "SAS",
}


def parse_video_filename(filename):
    """Parse video filename to extract teams and date."""
    pattern = r"(.+?)\s+at\s+(.+?)\s+[｜|]\s+FULL GAME HIGHLIGHTS\s+[｜|]\s+(\w+)\s+(\d+),?\s+(\d{4})"
    match = re.search(pattern, filename, re.IGNORECASE)

    if not match:
        return None

    away_team = match.group(1).strip().upper()
    home_team = match.group(2).strip().upper()
    month_str = match.group(3)
    day = int(match.group(4))
    year = int(match.group(5))

    try:
        month = datetime.strptime(month_str, "%B").month
    except ValueError:
        try:
            month = datetime.strptime(month_str, "%b").month
        except ValueError:
            return None

    home_code = TEAM_CODES.get(home_team)
    if not home_code:
        return None

    date_str = f"{year}{month:02d}{day:02d}0"

    return {
        "away_team": away_team,
        "home_team": home_team,
        "home_code": home_code,
        "date": f"{year}-{month:02d}-{day:02d}",
        "bbref_url": f"https://www.basketball-reference.com/boxscores/pbp/{date_str}{home_code}.html",
        "expected_pbp": f"{date_str}{home_code}.json",
    }


# ---------- endpoints ----------

@bp.route("/api/film/<path:filename>")
def serve_video(filename):
    """Serve a video file from the film directory with range request support."""
    film_dir = os.path.join(BASE_DIR, "film")
    return send_from_directory(film_dir, filename, conditional=True)


@bp.route("/api/videos")
def list_videos():
    """List available video files."""
    video_dir = os.path.join(BASE_DIR, "film")
    if not os.path.exists(video_dir):
        return jsonify({"videos": []})

    videos = []
    for f in sorted(os.listdir(video_dir)):
        if f.endswith((".mp4", ".mkv", ".webm")):
            path = os.path.join(video_dir, f)
            size_mb = os.path.getsize(path) / (1024 * 1024)
            parsed = parse_video_filename(f)
            videos.append({
                "name": f,
                "path": f"film/{f}",
                "size_mb": round(size_mb, 1),
                "parsed": parsed,
            })
    return jsonify({"videos": videos})


@bp.route("/api/videos/<path:video_path>/bbref")
def get_bbref_url(video_path):
    """Get suggested Basketball Reference URL for a video."""
    filename = os.path.basename(video_path)
    result = parse_video_filename(filename)
    if result:
        return jsonify(result)
    return jsonify({"error": "Could not parse video filename"}), 400


@bp.route("/api/videos/<path:video_path>/suggested-pbp")
def get_suggested_pbp(video_path):
    """Get suggested play-by-play file for a video."""
    filename = os.path.basename(video_path)
    result = parse_video_filename(filename)

    if not result:
        return jsonify({"suggested": None, "reason": "Could not parse video filename"})

    expected_path = f"playbyplay/{result['expected_pbp']}"
    full_path = os.path.join(BASE_DIR, expected_path)

    if os.path.exists(full_path):
        return jsonify({
            "suggested": expected_path,
            "filename": result["expected_pbp"],
            "game_info": f"{result['away_team']} at {result['home_team']} ({result['date']})",
        })
    return jsonify({
        "suggested": None,
        "expected": result["expected_pbp"],
        "bbref_url": result["bbref_url"],
        "reason": f"File {result['expected_pbp']} not found. You may need to scrape it first.",
    })
