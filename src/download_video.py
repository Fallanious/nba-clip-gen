import os
import sys
import time
import shutil
import subprocess
import argparse


class JsRuntimeNotFound(RuntimeError):
    """Raised when no JavaScript runtime (node or deno) is available."""
    pass


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


def detect_js_runtime():
    """
    Detect available JavaScript runtime (node or deno).
    
    Returns:
        str: 'node' or 'deno'
        
    Raises:
        JsRuntimeNotFound: If neither node nor deno is available
    """
    # Prefer node over deno
    if shutil.which('node'):
        return 'node'
    if shutil.which('deno'):
        return 'deno'
    
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
        env_value = os.environ.get('YTDLP_MAX_RES')
        if env_value is None:
            return 720  # default
        value = env_value
    
    # Validate type and convert
    if isinstance(value, bool):
        raise ValueError(f"Invalid resolution '{value}': must be a number")
    
    if isinstance(value, str):
        value = value.strip()
        if not value:
            raise ValueError("Invalid resolution: empty string")
        # Check for injection attempts
        if ',' in value or ';' in value or not value.replace('-', '').replace('+', '').isdigit():
            raise ValueError(f"Invalid resolution '{value}': contains invalid characters")
    
    try:
        res = int(float(value))  # Handle strings like "1.5"
        if float(value) != res:  # Reject non-integers like 1.5
            raise ValueError(f"Invalid resolution '{value}': must be an integer")
    except (TypeError, ValueError) as e:
        if "Invalid resolution" in str(e):
            raise
        raise ValueError(f"Invalid resolution '{value}': must be a number")
    
    # Validate range
    if res <= 0:
        raise ValueError(f"Invalid resolution '{res}': must be positive")
    if res < 144:
        raise ValueError(f"Invalid resolution '{res}': must be at least 144p to ensure proper quality filtering")
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
    
    # Find venv python
    venv_python = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        ".venv", "bin", "python",
    )
    
    if os.path.isfile(venv_python):
        return [venv_python] + cmd
    
    # Fallback to system python
    return ["python3"] + cmd


def build_ytdlp_cmd(url, resolution=None, runtime=None):
    """
    Build yt-dlp command using recommended invocation.
    
    Args:
        url: Video URL to download
        resolution: Maximum resolution (int, string, or None for default/env)
        runtime: JavaScript runtime to use ('node', 'deno', or None for auto-detect)
        
    Returns:
        list: Command line arguments for yt-dlp
        
    Raises:
        JsRuntimeNotFound: If no JavaScript runtime is available
        ValueError: If resolution is invalid
    """
    # Detect or validate JS runtime
    if runtime is None:
        runtime = detect_js_runtime()
    else:
        # Verify specified runtime exists
        if not shutil.which(runtime):
            raise JsRuntimeNotFound(
                f"Specified JavaScript runtime '{runtime}' not found on PATH. "
                f"Please install {runtime} or use an available runtime."
            )
    
    # Resolve resolution
    res = resolve_resolution(resolution)
    
    # Build command: python -m yt_dlp --js-runtimes <runtime> -S "res:<N>,+size" -f "bv*+ba/b" --merge-output-format mkv URL
    cmd = [
        "-m", "yt_dlp",
        "--js-runtimes", runtime,
        "-S", f"res:{res},+size",
        "-f", "bv*+ba/b",
        "--merge-output-format", "mkv",
    ]
    
    # Handle URLs that look like options
    if url.startswith("-"):
        cmd.extend(["--", url])
    else:
        cmd.append(url)
    
    return _with_venv_python(cmd)


def download_video(url, output_folder="film", resolution=None):
    """
    Download video using yt-dlp with recommended invocation.
    
    Args:
        url: Video URL to download
        output_folder: Folder to save video (default: 'film')
        resolution: Maximum resolution in pixels (None uses YTDLP_MAX_RES env or default 720)
        
    Returns:
        str: Path to downloaded video file
        
    Raises:
        JsRuntimeNotFound: If no JavaScript runtime is available
        ValueError: If resolution is invalid
        subprocess.CalledProcessError: If yt-dlp fails
    """
    # Create output folder (handle both absolute and relative paths)
    if os.path.isabs(output_folder):
        output_path = output_folder
    else:
        output_path = os.path.join(os.getcwd(), output_folder)
    
    if not os.path.exists(output_path):
        os.makedirs(output_path)
        print(f"Created folder: {output_path}")
    
    # Build command
    cmd = build_ytdlp_cmd(url, resolution=resolution)
    
    # Add output template with absolute path
    template = os.path.join(os.path.abspath(output_path), "%(title)s.%(ext)s")
    # Insert -o before the URL (last arg or after --)
    if "--" in cmd:
        idx = cmd.index("--")
    else:
        idx = len(cmd) - 1
    cmd.insert(idx, "-o")
    cmd.insert(idx + 1, template)
    
    print(f"Downloading to {output_folder}...")
    print(f"Resolution: {resolve_resolution(resolution)}p")
    print(f"Runtime: {cmd[cmd.index('--js-runtimes') + 1]}")
    
    started = time.time()
    
    # Run yt-dlp
    try:
        subprocess.run(cmd, check=True, stderr=subprocess.PIPE, stdout=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode('utf-8') if e.stderr else ''
        raise subprocess.CalledProcessError(
            e.returncode, e.cmd,
            stderr=f"yt-dlp failed: {stderr}".encode('utf-8')
        )
    
    # Find saved file
    saved = _video_saved_since(output_path, started)
    if not saved:
        raise RuntimeError("Download failed: no video file was saved")
    
    print(f"Saved {os.path.basename(saved)}")
    return saved


def main():
    """Main function to handle command-line execution."""
    parser = argparse.ArgumentParser(
        description='Download video from YouTube using yt-dlp',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment Variables:
  YTDLP_MAX_RES        Maximum video resolution in pixels (default: 720)
                       Valid range: 144-4320

Examples:
  %(prog)s "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  %(prog)s "https://www.youtube.com/watch?v=dQw4w9WgXcQ" --resolution 1080
  YTDLP_MAX_RES=1080 %(prog)s "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        """
    )
    
    parser.add_argument('url', help='YouTube video URL to download')
    parser.add_argument(
        '--resolution',
        type=int,
        default=None,
        help='Maximum video resolution in pixels (default: from YTDLP_MAX_RES env or 720)'
    )
    parser.add_argument(
        '--output-folder',
        default='film',
        help='Output folder for downloaded video (default: film)'
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
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode('utf-8') if e.stderr else str(e)
        print(f"ERROR: {stderr}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()