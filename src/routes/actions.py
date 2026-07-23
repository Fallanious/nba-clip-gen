"""Action endpoints: download, scrape, match, generate."""

import os
import uuid
import threading
from flask import Blueprint, jsonify, request

from routes.jobs import jobs, run_script_async
from utils.video import get_video_duration

bp = Blueprint("actions", __name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _start_job(cmd):
    """Helper: create job, launch thread, return job_id."""
    job_id = str(uuid.uuid4())[:8]
    thread = threading.Thread(target=run_script_async, args=(job_id, cmd))
    thread.start()
    return job_id


@bp.route("/api/download", methods=["POST"])
def download_video():
    """Download a video from YouTube."""
    url = (request.json or {}).get("url", "").strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400

    job_id = _start_job(["python3", "src/download_video.py", url])
    return jsonify({"job_id": job_id, "message": "Download started"})


@bp.route("/api/scrape", methods=["POST"])
def scrape_playbyplay():
    """Scrape play-by-play data from Basketball Reference."""
    url = (request.json or {}).get("url", "").strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400

    job_id = _start_job(["python3", "src/scrape_playbyplay.py", url])
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

    if num_workers <= 1:
        job_id = _start_job([
            "python3", "src/match_play_timestamps.py",
            video, playbyplay,
            "--buffer", str(buffer),
            "--sample-interval", str(sample_interval),
            "--max-plays", str(max_plays),
            "--start-time", str(start_time),
        ])
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

        job_id = str(uuid.uuid4())[:8]
        job_ids.append(job_id)

        cmd = [
            "python3", "src/match_play_timestamps.py",
            video, playbyplay,
            "--buffer", str(buffer),
            "--sample-interval", str(sample_interval),
            "--max-plays", str(max_plays),
            "--start-time", str(worker_start),
            "--end-time", str(worker_end),
            "--worker-id", str(i + 1),
        ]

        thread = threading.Thread(target=run_script_async, args=(job_id, cmd))
        thread.start()

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

    job_id = _start_job(cmd)
    return jsonify({"job_id": job_id, "message": "Generating clips"})

