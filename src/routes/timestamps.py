"""Timestamp, play-by-play, and clip-listing endpoints."""

import os
import re as _re
import json
from flask import Blueprint, jsonify, request

bp = Blueprint("timestamps", __name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def infer_video_from_timestamps(ts_filename):
    """Infer the source video filename from a timestamps filename."""
    name = os.path.basename(ts_filename)
    name = _re.sub(r"\.json$", "", name)
    name = _re.sub(r"_worker\d+$", "", name)
    name = _re.sub(r"_timestamps_\d{8}_\d{6}$", "", name)

    film_dir = os.path.join(BASE_DIR, "film")
    if os.path.isdir(film_dir):
        for ext in (".mp4", ".mkv", ".webm"):
            candidate = os.path.join(film_dir, name + ext)
            if os.path.exists(candidate):
                return f"film/{name}{ext}"
    return None


# ---------- endpoints ----------

@bp.route("/api/playbyplay")
def list_playbyplay():
    """List available play-by-play files."""
    pbp_dir = os.path.join(BASE_DIR, "playbyplay")
    if not os.path.exists(pbp_dir):
        return jsonify({"files": []})

    files = []
    for f in sorted(os.listdir(pbp_dir)):
        if f.endswith(".json"):
            files.append({"name": f, "path": f"playbyplay/{f}"})
    return jsonify({"files": files})


@bp.route("/api/timestamps")
def list_timestamps():
    """List available timestamp files."""
    ts_dir = os.path.join(BASE_DIR, "output", "cliptimestamps")
    if not os.path.exists(ts_dir):
        return jsonify({"files": []})

    files = []
    for f in sorted(os.listdir(ts_dir), reverse=True):
        if f.endswith(".json"):
            path = os.path.join(ts_dir, f)
            try:
                with open(path) as fp:
                    data = json.load(fp)
                    play_count = len(data) if isinstance(data, list) else 0
            except Exception:
                play_count = 0
            files.append({
                "name": f,
                "path": f"output/cliptimestamps/{f}",
                "play_count": play_count,
            })
    return jsonify({"files": files})


@bp.route("/api/timestamps/<path:file_path>/plays")
def get_timestamp_plays(file_path):
    """Load plays from a timestamp file."""
    full_path = os.path.join(BASE_DIR, file_path)

    if not os.path.exists(full_path):
        return jsonify({"error": "File not found"}), 404

    try:
        with open(full_path) as fp:
            plays = json.load(fp)
        video_path = infer_video_from_timestamps(file_path)
        return jsonify({"plays": plays, "file": file_path, "video": video_path})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/clips")
def get_clips():
    """List all generated clip file paths."""
    clips_dir = os.path.join(BASE_DIR, "output", "clips")
    run_filter = request.args.get("run", "").strip()
    if not os.path.exists(clips_dir):
        return jsonify({"clips": []})

    clips = []
    for root, _, files in os.walk(clips_dir):
        rel_root = os.path.relpath(root, clips_dir).replace("\\", "/")
        if run_filter and rel_root != run_filter:
            continue
        for filename in files:
            if not filename.endswith(".mp4"):
                continue
            full = os.path.join(root, filename)
            rel_path = os.path.relpath(full, BASE_DIR).replace("\\", "/")
            clips.append(rel_path)

    clips.sort(reverse=True)
    return jsonify({"clips": clips})


@bp.route("/api/clip-runs")
def get_clip_runs():
    """List clip run folders under output/clips."""
    clips_dir = os.path.join(BASE_DIR, "output", "clips")
    if not os.path.exists(clips_dir):
        return jsonify({"runs": []})

    runs = []
    for entry in sorted(os.listdir(clips_dir), reverse=True):
        run_path = os.path.join(clips_dir, entry)
        if not os.path.isdir(run_path):
            continue
        clip_count = len([f for f in os.listdir(run_path) if f.endswith(".mp4")])
        runs.append({"name": entry, "path": entry, "clip_count": clip_count})

    return jsonify({"runs": runs})
