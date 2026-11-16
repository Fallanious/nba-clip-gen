"""Shared video utilities."""

import json
import os
import subprocess

def get_video_duration(video_path):
    """Get video duration in seconds using ffprobe."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError):
        return None


def project_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def resolve_film_file(video_arg):
    """Return a real file inside film/, or raise ValueError."""
    film = os.path.realpath(os.path.join(project_root(), "film"))
    raw = (video_arg or "").strip()
    candidate = raw if os.path.isabs(raw) else os.path.join(project_root(), raw)
    path = os.path.realpath(candidate)
    try:
        inside = os.path.commonpath([film, path]) == film
    except ValueError:
        inside = False
    ext = os.path.splitext(path)[1].lower()
    if not inside or not os.path.isfile(path) or ext not in (".mp4", ".mkv", ".webm"):
        raise ValueError("Video must be a file in the film folder")
    return path


def probe_video(video_path):
    """Return storage-relevant metadata, or None when ffprobe cannot read the file."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "stream=height,codec_type:format=format_name,duration,size",
        "-of", "json",
        video_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout or "{}")
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError):
        return None
    height = None
    for stream in data.get("streams") or []:
        if stream.get("codec_type") == "video" and stream.get("height"):
            height = int(stream["height"])
            break
    return {
        "format_name": (data.get("format") or {}).get("format_name") or "",
        "height": height,
        "duration": (data.get("format") or {}).get("duration"),
        "size": int((data.get("format") or {}).get("size") or 0),
        "has_audio": any(s.get("codec_type") == "audio" for s in data.get("streams", [])),
    }
