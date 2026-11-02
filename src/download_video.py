import os
import re
import sys
import time
import shutil
import signal
import argparse
import subprocess
from urllib.parse import urlparse


class JsRuntimeNotFound(RuntimeError):
    """Raised when no JavaScript runtime (node or deno) is available."""
    pass


# Subprocess timeout (seconds)
DEFAULT_TIMEOUT = 1800
MIN_TIMEOUT = 10
MAX_TIMEOUT = 7200

# yt-dlp --socket-timeout (seconds)
DEFAULT_SOCKET_TIMEOUT = 60
MIN_SOCKET_TIMEOUT = 5
MAX_SOCKET_TIMEOUT = 300

# yt-dlp --max-filesize (bytes)
DEFAULT_MAX_FILESIZE = 5 * 1024 * 1024 * 1024  # 5 GiB
MIN_MAX_FILESIZE = 1 * 1024 * 1024  # 1 MiB
MAX_MAX_FILESIZE = 20 * 1024 * 1024 * 1024  # 20 GiB

ALLOWED_YOUTUBE_HOSTS = frozenset({
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "www.youtu.be",
})

STDERR_MAX_LENGTH = 500


def _video_saved_since(folder, started):
    """Return a non-empty video written to film during this download."""
    newest = None
    newest_mtime = started - 2
    for name in os.listdir(folder):
        if not name.endswith((".mp4", ".mkv", ".webm")):
            continue
        path = os.path.join(folder, name)
        if not os.path.isfile(path) or os.path.getsize(path) <= 0:
            continue
        mtime = os.path.getmtime(path)
        if mtime >= newest_mtime:
            newest = path
            newest_mtime = mtime
    return newest


def _sanitize_stderr(stderr_bytes, max_length=STDERR_MAX_LENGTH):
    """Sanitize and truncate stderr output from yt-dlp before logging or raising."""
    if not stderr_bytes:
        return "No error details available"

    try:
        stderr = stderr_bytes.decode("utf-8", errors="replace")
    except Exception:
        return "Error decoding stderr"

    stderr = re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f-\x9f]", "", stderr)
    stderr = " ".join(stderr.split())

    if len(stderr) > max_length:
        stderr = stderr[:max_length] + "... (truncated)"

    return stderr


def _validate_youtube_url(url):
    """
    Validate that URL is a safe YouTube URL.

    Only https URLs on youtube.com / youtu.be (and www/m subdomains) are
    accepted. Userinfo, ports, backslashes, and whitespace are rejected.
    """
    if not url:
        raise ValueError("URL cannot be empty")
    if not isinstance(url, str):
        raise ValueError("URL must be a string")
    if any(ch.isspace() for ch in url):
        raise ValueError("URL must not contain whitespace")
    if "\\" in url:
        raise ValueError("URL must not contain backslash")
    if not url.startswith("https://"):
        raise ValueError("Only HTTPS URLs are allowed")

    try:
        parsed = urlparse(url)
    except Exception as e:
        raise ValueError(f"Invalid URL format: {e}") from e

    if parsed.scheme != "https":
        raise ValueError("Only HTTPS URLs are allowed")
    if parsed.username is not None or parsed.password is not None or "@" in (parsed.netloc or ""):
        raise ValueError("URL must not contain userinfo")
    if parsed.port is not None or (parsed.netloc and ":" in parsed.netloc):
        raise ValueError("URL must not contain a port")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL must have a valid hostname")

    hostname = hostname.lower()
    if hostname not in ALLOWED_YOUTUBE_HOSTS:
        raise ValueError(
            f"Only YouTube URLs are allowed (youtube.com or youtu.be), got: {hostname}"
        )

    return True


def _clamp_int_env(name, default, minimum, maximum):
    """Read an integer env var and clamp it to [minimum, maximum]."""
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError) as e:
        raise ValueError(f"Invalid {name} '{raw}': must be an integer") from e
    if value < minimum:
        return minimum
    if value > maximum:
        return maximum
    return value


def resolve_timeout():
    """Subprocess timeout in seconds (default 1800, clamped 10–7200)."""
    return _clamp_int_env("YTDLP_TIMEOUT", DEFAULT_TIMEOUT, MIN_TIMEOUT, MAX_TIMEOUT)


def resolve_socket_timeout():
    """yt-dlp --socket-timeout in seconds (default 60, clamped 5–300)."""
    return _clamp_int_env(
        "YTDLP_SOCKET_TIMEOUT",
        DEFAULT_SOCKET_TIMEOUT,
        MIN_SOCKET_TIMEOUT,
        MAX_SOCKET_TIMEOUT,
    )


def resolve_max_filesize():
    """yt-dlp --max-filesize in bytes (default 5 GiB, clamped 1 MiB–20 GiB)."""
    return _clamp_int_env(
        "YTDLP_MAX_FILESIZE",
        DEFAULT_MAX_FILESIZE,
        MIN_MAX_FILESIZE,
        MAX_MAX_FILESIZE,
    )


def detect_js_runtime(runtime=None):
    """
    Detect available JavaScript runtime (node or deno).

    Args:
        runtime: Specific runtime to validate ('node' or 'deno'), or None for auto-detect

    Returns:
        str: 'node' or 'deno'

    Raises:
        JsRuntimeNotFound: If neither node nor deno is available
        ValueError: If runtime argument is not 'node' or 'deno'
    """
    if runtime is not None:
        if runtime not in ("node", "deno"):
            raise ValueError(f"Invalid runtime '{runtime}': must be 'node' or 'deno'")
        if not shutil.which(runtime):
            raise JsRuntimeNotFound(
                f"Specified JavaScript runtime '{runtime}' not found on PATH. "
                f"Please install {runtime}."
            )
        return runtime

    if shutil.which("node"):
        return "node"
    if shutil.which("deno"):
        return "deno"

    raise JsRuntimeNotFound(
        "No JavaScript runtime available. yt-dlp requires node or deno for YouTube downloads. "
        "Please install Node.js (https://nodejs.org/) or Deno (https://deno.land/) and ensure "
        "it is on your PATH."
    )


def resolve_resolution(value):
    """
    Resolve and validate resolution value.

    Args:
        value: Resolution as int, string, or None (reads YTDLP_MAX_RES env var)

    Returns:
        int: Valid resolution value

    Raises:
        ValueError: If resolution is invalid
    """
    if value is None:
        env_value = os.environ.get("YTDLP_MAX_RES")
        if env_value is None:
            return 720
        value = env_value

    if isinstance(value, bool):
        raise ValueError(f"Invalid resolution '{value}': must be a number")

    if isinstance(value, str):
        value = value.strip()
        if not value:
            raise ValueError("Invalid resolution: empty string")
        if "," in value or ";" in value or not value.replace("-", "").replace("+", "").isdigit():
            raise ValueError(f"Invalid resolution '{value}': contains invalid characters")

    try:
        res = int(float(value))
        if float(value) != res:
            raise ValueError(f"Invalid resolution '{value}': must be an integer")
    except (TypeError, ValueError) as e:
        if "Invalid resolution" in str(e):
            raise
        raise ValueError(f"Invalid resolution '{value}': must be a number") from e

    if res <= 0:
        raise ValueError(f"Invalid resolution '{res}': must be positive")
    if res < 144:
        raise ValueError(
            f"Invalid resolution '{res}': must be at least 144p to ensure proper quality filtering"
        )
    if res > 4320:
        raise ValueError(f"Invalid resolution '{res}': maximum is 4320 (8K)")

    return res


def _with_venv_python(cmd):
    """
    Run command with the virtualenv interpreter.

    Routes/jobs.py pattern: python3 on PATH can be old, causing yt-dlp 403s.
    Downloads must use the venv's interpreter.
    """
    if not cmd:
        return cmd

    venv_python = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        ".venv", "bin", "python",
    )

    if os.path.isfile(venv_python):
        return [venv_python] + cmd

    return ["python3"] + cmd


def _kill_process_group(proc):
    """Kill a subprocess and its entire process group."""
    if proc is None or proc.pid is None:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except Exception:
            pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


def run_ytdlp_subprocess(cmd, timeout):
    """
    Run yt-dlp in a new session and kill the whole process group on timeout.

    stdout is inherited so progress streams to the job log. stderr is captured
    so failures can be sanitized before they are raised or logged.
    """
    proc = subprocess.Popen(
        cmd,
        stdout=None,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        _, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        raise subprocess.TimeoutExpired(cmd, timeout, output=b"Download timed out")
    if proc.returncode:
        raise subprocess.CalledProcessError(proc.returncode, cmd, stderr=stderr)
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout=b"", stderr=stderr)


def build_ytdlp_cmd(url, resolution=None, runtime=None, output_template=None):
    """
    Build yt-dlp command using recommended invocation with security controls.

    Every option (including -o) is placed before `--`. The URL is always last.

    Args:
        url: Video URL to download (must be HTTPS YouTube URL)
        resolution: Maximum resolution (int, string, or None for default/env)
        runtime: JavaScript runtime to use ('node', 'deno', or None for auto-detect)
        output_template: yt-dlp -o template; defaults to %(id)s.%(ext)s

    Returns:
        list: Command line arguments for yt-dlp

    Raises:
        JsRuntimeNotFound: If no JavaScript runtime is available
        ValueError: If resolution is invalid, runtime is not 'node'/'deno', or URL is unsafe
    """
    _validate_youtube_url(url)
    runtime = detect_js_runtime(runtime)
    res = resolve_resolution(resolution)
    socket_timeout = resolve_socket_timeout()
    max_filesize = resolve_max_filesize()
    if not output_template:
        output_template = "%(id)s.%(ext)s"

    cmd = [
        "-m", "yt_dlp",
        "--ignore-config",
        "--no-playlist",
        "--js-runtimes", runtime,
        "-S", f"res:{res},+size",
        "-f", "bv*+ba/b",
        "--merge-output-format", "mkv",
        "--restrict-filenames",
        "--socket-timeout", str(socket_timeout),
        "--max-filesize", str(max_filesize),
        "-o", output_template,
        "--",
        url,
    ]

    return _with_venv_python(cmd)


def download_video(url, output_folder="film", resolution=None):
    """
    Download video using yt-dlp with recommended invocation.

    Args:
        url: Video URL to download (must be HTTPS YouTube URL)
        output_folder: Folder to save video (default: 'film')
        resolution: Maximum resolution in pixels (None uses YTDLP_MAX_RES env or default 720)

    Returns:
        str: Path to downloaded video file

    Raises:
        JsRuntimeNotFound: If no JavaScript runtime is available
        ValueError: If resolution is invalid or URL is unsafe
        subprocess.CalledProcessError: If yt-dlp fails
        subprocess.TimeoutExpired: If download times out
    """
    _validate_youtube_url(url)

    if os.path.isabs(output_folder):
        output_path = output_folder
    else:
        output_path = os.path.join(os.getcwd(), output_folder)

    if not os.path.exists(output_path):
        os.makedirs(output_path)
        print(f"Created folder: {output_path}")

    template = os.path.join(os.path.abspath(output_path), "%(id)s.%(ext)s")
    cmd = build_ytdlp_cmd(url, resolution=resolution, output_template=template)

    print(f"Downloading to {output_folder}...")
    print(f"Resolution: {resolve_resolution(resolution)}p")
    print(f"Runtime: {cmd[cmd.index('--js-runtimes') + 1]}")

    started = time.time()
    timeout = resolve_timeout()

    try:
        run_ytdlp_subprocess(cmd, timeout)
    except subprocess.CalledProcessError as e:
        sanitized = _sanitize_stderr(e.stderr)
        raise subprocess.CalledProcessError(
            e.returncode, e.cmd,
            stderr=f"yt-dlp failed: {sanitized}".encode("utf-8"),
        )

    saved = _video_saved_since(output_path, started)
    if not saved:
        raise RuntimeError("Download failed: no video file was saved")

    print(f"Saved {os.path.basename(saved)}")
    return saved


def main():
    """Main function to handle command-line execution."""
    parser = argparse.ArgumentParser(
        description="Download video from YouTube using yt-dlp",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment Variables:
  YTDLP_MAX_RES        Maximum video resolution in pixels (default: 720)
                       Valid range: 144-4320
  YTDLP_TIMEOUT        Download timeout in seconds (default: 1800, clamped 10-7200)
  YTDLP_SOCKET_TIMEOUT Socket timeout in seconds (default: 60, clamped 5-300)
  YTDLP_MAX_FILESIZE   Maximum file size in bytes (default: 5GiB, clamped 1MiB-20GiB)

Examples:
  %(prog)s "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  %(prog)s "https://www.youtube.com/watch?v=dQw4w9WgXcQ" --resolution 1080
  YTDLP_MAX_RES=1080 %(prog)s "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        """,
    )

    parser.add_argument("url", help="YouTube video URL to download (HTTPS only)")
    parser.add_argument(
        "--resolution",
        type=int,
        default=None,
        help="Maximum video resolution in pixels (default: from YTDLP_MAX_RES env or 720)",
    )
    parser.add_argument(
        "--output-folder",
        default="film",
        help="Output folder for downloaded video (default: film)",
    )

    args = parser.parse_args()

    try:
        download_video(args.url, args.output_folder, args.resolution)
    except JsRuntimeNotFound as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except subprocess.TimeoutExpired as e:
        print(f"ERROR: Download timed out after {e.timeout} seconds", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        stderr = _sanitize_stderr(e.stderr)
        print(f"ERROR: {stderr}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
