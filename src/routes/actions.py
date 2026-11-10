"""Action endpoints: download, scrape, match, generate."""

import os
import time
import uuid
import threading
from flask import Blueprint, jsonify, request

from routes.jobs import run_script_async, jobs
from utils.video import get_video_duration

bp = Blueprint("actions", __name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _crop_args(data):
    """Pass a scoreboard box through as frame fractions."""
    crop = (data or {}).get("crop")
    if not crop:
        return []
    try:
        values = [float(crop[key]) for key in ("x", "y", "w", "h")]
    except (TypeError, KeyError, ValueError):
        return []
    if values[2] <= 0 or values[3] <= 0:
        return []
    return ["--crop", ",".join(str(value) for value in values)]


def _start_job(cmd, kind="Task", label="", env=None):
    """Create a job record, launch it, and return the id."""
    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {
        "status": "running",
        "output": "",
        "error": None,
        "return_code": None,
        "pid": None,
        "kind": kind,
        "label": label,
        "started_at": time.time(),
        "finished_at": None,
    }
    thread = threading.Thread(
        target=run_script_async,
        args=(job_id, cmd),
        kwargs={"env": env} if env else {},
    )
    thread.start()
    return job_id


def _persist_login_error(database_url):
    """Return a short error when the clip save cannot sign in."""
    from persist_matcher_clips import PersistRefused, assert_persist_ready

    try:
        params = assert_persist_ready(database_url)
    except PersistRefused as exc:
        return str(exc)
    try:
        import psycopg

        with psycopg.connect(**params, connect_timeout=8) as conn:
            conn.execute("SELECT 1")
    except Exception:
        return "Database login failed. Fix DATABASE_URL before matching."
    return None


def _match_persist_env(playbyplay):
    """Point the match job at the direct database and the game id."""
    import re

    from persist_matcher_clips import direct_database_url

    bbref_id = os.path.splitext(os.path.basename(playbyplay or ""))[0]
    env = {"DATABASE_URL": direct_database_url(os.getenv("DATABASE_URL", ""))}
    if re.fullmatch(r"[0-9]{9}[A-Z]{3}", bbref_id):
        env["MATCHER_GAME_BBREF_ID"] = bbref_id
    return env


@bp.route("/api/download", methods=["POST"])
def download_video():
    """Download a video from YouTube."""
    url = (request.json or {}).get("url", "").strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400

    job_id = _start_job(["python3", "src/download_video.py", url], kind="Download", label=url)
    return jsonify({"job_id": job_id, "message": "Download started"})


@bp.route("/api/scrape", methods=["POST"])
def scrape_playbyplay():
    """Scrape play-by-play data from Basketball Reference."""
    url = (request.json or {}).get("url", "").strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400

    job_id = _start_job(["python3", "src/scrape_playbyplay.py", url], kind="Scrape play-by-play", label=url)
    return jsonify({"job_id": job_id, "message": "Scraping started"})


@bp.route("/api/match", methods=["POST"])
def match_timestamps():
    """Match video frames to play-by-play data."""
    data = request.json
    video = data.get("video", "").strip()
    playbyplay = data.get("playbyplay", "").strip()
    buffer = data.get("buffer", 3)
    sample_interval = data.get("sample_interval", 2)
    max_plays = data.get("max_plays", 50)
    start_time = data.get("start_time", 0)
    num_workers = data.get("num_workers", 1)

    if not video or not playbyplay:
        return jsonify({"error": "Video and playbyplay are required"}), 400

    label = os.path.basename(video)
    crop_args = _crop_args(data)
    persist_env = _match_persist_env(playbyplay)
    login_error = _persist_login_error(persist_env["DATABASE_URL"])
    if login_error:
        return jsonify({"error": login_error}), 400
    if num_workers <= 1:
        job_id = _start_job([
            "python3", "src/match_play_timestamps.py",
            video, playbyplay,
            "--buffer", str(buffer),
            "--sample-interval", str(sample_interval),
            "--max-plays", str(max_plays),
            "--start-time", str(start_time),
            *crop_args,
        ], kind="Match timestamps", label=label, env=persist_env)
        return jsonify({"job_id": job_id, "job_ids": [job_id], "message": "Matching started"})

    # Multiple workers — split video into time ranges
    video_path = os.path.join(BASE_DIR, video)
    duration = get_video_duration(video_path)
    if duration is None:
        return jsonify({"error": "Could not determine video duration"}), 400

    effective_duration = duration - start_time
    chunk_size = effective_duration / num_workers

    job_ids = []
    for i in range(num_workers):
        worker_start = start_time + int(i * chunk_size)
        worker_end = start_time + int((i + 1) * chunk_size) if i < num_workers - 1 else int(duration)
        cmd = [
            "python3", "src/match_play_timestamps.py",
            video, playbyplay,
            "--buffer", str(buffer),
            "--sample-interval", str(sample_interval),
            "--max-plays", str(max_plays),
            "--start-time", str(worker_start),
            "--end-time", str(worker_end),
            "--worker-id", str(i + 1),
            *crop_args,
        ]
        job_id = _start_job(cmd, kind="Match timestamps", label=f"Worker {i + 1} · {label}", env=persist_env)
        job_ids.append(job_id)

    return jsonify({
        "job_ids": job_ids,
        "job_id": job_ids[0],
        "message": f"Started {num_workers} parallel workers",
    })


@bp.route("/api/generate", methods=["POST"])
def generate_clips():
    """Generate video clips from timestamps."""
    data = request.json
    video = data.get("video", "").strip()
    timestamps = data.get("timestamps", "").strip()
    no_audio = data.get("no_audio", True)

    if not video or not timestamps:
        return jsonify({"error": "Video and timestamps are required"}), 400

    cmd = ["python3", "src/generate_clips.py", video, timestamps]
    if no_audio:
        cmd.append("--no-audio")

    job_id = _start_job(cmd, kind="Generate clips", label=os.path.basename(video))
    return jsonify({"job_id": job_id, "message": "Generating clips"})


@bp.route("/api/upload", methods=["POST"])
def upload_video():
    """Convert a local game to 720p MKV when needed, then upload it to S3."""
    from routes.videos import parse_video_filename
    from utils.video import resolve_film_file

    video = (request.json or {}).get("video", "").strip()
    if not video:
        return jsonify({"error": "Video is required"}), 400
    try:
        path = resolve_film_file(video)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    parsed = parse_video_filename(os.path.basename(path))
    if not parsed:
        return jsonify({"error": "Filename needs teams and a date, like TEAM at TEAM | FULL GAME HIGHLIGHTS | Month D, YYYY."}), 400

    job_id = _start_job(
        ["python3", "src/upload_game.py", os.path.relpath(path, BASE_DIR)],
        kind="Upload",
        label=os.path.basename(path),
    )
    return jsonify({"job_id": job_id, "message": "Upload started", "bbref_id": parsed["bbref_id"]})

