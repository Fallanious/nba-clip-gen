"""Shared video utilities."""

import json
import math
import tempfile
import os
import signal
import subprocess

TARGET_HEIGHT = 720
TARGET_BYTES = 250_000_000  # Decimal MB, matching the compression experiments.
MUX_BUDGET = 0.995


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


def is_upload_ready(info):
    """Avoid another lossy encode when a silent MKV already meets the budget."""
    if not info or not info.get("height"):
        return False
    names = set(info["format_name"].split(","))
    return (
        "matroska" in names
        and info["height"] <= TARGET_HEIGHT
        and 0 < info.get("size", 0) <= TARGET_BYTES
        and info.get("has_audio") is False
    )


def storage_bitrate(duration):
    """Video bits/second, leaving room for the MKV container; no audio budget."""
    try:
        seconds = float(duration)
    except (ValueError, TypeError):
        raise ValueError("A valid video duration is required for size-targeted encoding.")
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("A valid video duration is required for size-targeted encoding.")
    return max(1, int(TARGET_BYTES * 8 * MUX_BUDGET / seconds))


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
    """Prepare silent <=720p storage; preserve frame rate and target ~250 MB.

    Small sources can be remuxed without quality loss. Larger sources use
    two-pass HEVC. Pass statistics live in an isolated temporary directory.
    """
    if os.path.realpath(src) == os.path.realpath(dest):
        raise ValueError("Storage output must be separate from the original video.")
    if height and height <= TARGET_HEIGHT and os.path.getsize(src) <= TARGET_BYTES:
        code, err = _run_ffmpeg([
            "ffmpeg", "-nostdin", "-y", "-i", src,
            "-map", "0:v:0", "-an", "-c:v", "copy", dest,
        ])
        if code == 0:
            return "remux"
        raise RuntimeError(f"ffmpeg remux failed: {err[-500:]}")

    bitrate = storage_bitrate(get_video_duration(src))
    with tempfile.TemporaryDirectory(prefix="clip-encode-") as tmp:
        stats = os.path.join(tmp, "pass")
        for pass_number in (1, 2):
            print(f"Encoding storage video: pass {pass_number}/2, {bitrate / 1e6:.2f} Mbps", flush=True)
            cmd = ["ffmpeg", "-nostdin", "-y", "-i", src, "-map", "0:v:0", "-an"]
            if not height or height > TARGET_HEIGHT:
                cmd += ["-vf", "scale=-2:min(720\\,ih)"]
            cmd += [
                "-c:v", "libx265", "-preset", "slow", "-b:v", str(bitrate),
                "-x265-params", f"pools=4:pass={pass_number}:stats={stats}",
            ]
            cmd += ["-f", "null", os.devnull] if pass_number == 1 else [dest]
            code, err = _run_ffmpeg(cmd)
            if code != 0:
                raise RuntimeError(f"ffmpeg pass {pass_number} failed: {err[-500:]}")
    return "transcode"
