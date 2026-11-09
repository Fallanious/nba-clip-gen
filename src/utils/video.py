"""Shared video utilities."""

import json
import os
import signal
import subprocess

TARGET_HEIGHT = 720


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
    """Return the container name and video height, or None when ffprobe cannot read the file."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "stream=height,codec_type:format=format_name",
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
    }


def is_upload_ready(info):
    """True when the file is already an MKV at or under 720p. Those files upload as-is."""
    if not info or not info.get("height"):
        return False
    names = {part.strip() for part in info["format_name"].split(",")}
    return "matroska" in names and info["height"] <= TARGET_HEIGHT


def _run_ffmpeg(cmd):
    """Run ffmpeg. A stop signal also stops ffmpeg, so the job does not leave it running."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )

    def stop(signum, frame):
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        raise SystemExit(1)

    previous = signal.signal(signal.SIGTERM, stop)
    try:
        _, err = proc.communicate()
    finally:
        signal.signal(signal.SIGTERM, previous)
    return proc.returncode, (err or b"").decode("utf-8", "replace")


def transcode_to_mkv(src, dest, height):
    """Write a 720p MKV. Shorter videos are not scaled up. A 720p file in another container is remuxed."""
    if height and height <= TARGET_HEIGHT:
        code, _err = _run_ffmpeg(["ffmpeg", "-y", "-i", src, "-c", "copy", dest])
        if code == 0:
            return "remux"
    cmd = ["ffmpeg", "-y", "-i", src]
    if not height or height > TARGET_HEIGHT:
        cmd += ["-vf", "scale=-2:720"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-b:a", "128k", dest]
    code, err = _run_ffmpeg(cmd)
    if code != 0:
        raise RuntimeError(f"ffmpeg failed: {err[-500:]}")
    return "transcode"
