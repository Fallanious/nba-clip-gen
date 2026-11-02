"""Fixtures for the yt-dlp CLI / mkv migration tests. No network, no real binaries, no AWS.

ADAPT HERE: every module/function name DEV's change must expose. Names on main today:
  src/download_video.py (yt_dlp Python API, mp4 merge), src/generate_clips.py (ffmpeg),
  src/routes/videos.py, src/routes/timestamps.py. No S3 upload code exists on main; the
  only S3 code is services/playback.py on PR #1 (hardcoded video/mp4).
"""
import importlib, os, sys, subprocess
from types import SimpleNamespace
import pytest

REPO_ROOT = os.environ.get("NBA_CLIP_GEN_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
SRC = os.path.join(REPO_ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)  # repo imports top-level modules (config, routes.*, ...)

# ---- ADAPT: module paths / symbols (confirm with DEV) ----
DOWNLOAD_MODULE = "download_video"           # src/download_video.py
BUILD_CMD = "build_ytdlp_cmd"                # (url, resolution=None, runtime=None) -> list[str]
DETECT_RUNTIME = "detect_js_runtime"         # () -> "node" | "deno"; raises on none
RESOLVE_RES = "resolve_resolution"           # (value|None) -> int; reads env when None
DOWNLOAD_FN = "download_video"               # (url, output_folder="film", resolution=None) -> path
RUNTIME_ERROR = "JsRuntimeNotFound"          # exception class (subclass of RuntimeError ok)
RES_ENV = "YTDLP_MAX_RES"                    # env var for default resolution
CLIPS_MODULE = "generate_clips"              # extract_clip / combine_clips
STORAGE_MODULE = "services.storage"          # content_type_for(path), s3_key_for_game(bbref_id, path)
PLAYBACK_MODULE = "services.playback"        # PR #1; skip tests that need it when absent
SCAN_DIRS = ["src"]                          # for the leftover-.mp4 audit
DEFAULT_RES = 720
MKV_TYPE = "video/x-matroska"


def load(module, attr=None):
    try:
        m = importlib.import_module(module)
    except ImportError as e:
        pytest.fail(f"cannot import {module!r} (update conftest constants): {e}")
    if attr is None:
        return m
    if not hasattr(m, attr):
        pytest.fail(f"{module}.{attr} missing (update conftest constants or implement)")
    return getattr(m, attr)


@pytest.fixture(autouse=True)
def no_network(monkeypatch, request):
    """Strip AWS creds and block real sockets for every test except @pytest.mark.network."""
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE", RES_ENV):
        monkeypatch.delenv(k, raising=False)
    if request.node.get_closest_marker("network"):
        return
    import socket
    def _blocked(*a, **k):
        raise AssertionError("network access attempted in unit test")
    monkeypatch.setattr(socket.socket, "connect", _blocked)


@pytest.fixture
def which(monkeypatch):
    """Control shutil.which: which.available = {'node', 'ffmpeg'}."""
    import shutil
    state = SimpleNamespace(available={"node", "ffmpeg"}, calls=[])
    def fake(name, *a, **k):
        state.calls.append(name)
        return f"/usr/bin/{name}" if name in state.available else None
    monkeypatch.setattr(shutil, "which", fake)
    return state


@pytest.fixture
def fake_run(monkeypatch, tmp_path):
    """Record subprocess.run/Popen calls; for yt-dlp, create the mkv the CLI would produce."""
    calls = []

    def _record_and_maybe_write(cmd):
        calls.append(list(cmd))
        argv = [str(x) for x in cmd]
        if any("yt-dlp" in x or x == "yt_dlp" for x in argv[:3]) and "-o" in argv:
            tmpl = argv[argv.index("-o") + 1]
            out = tmpl.replace("%(title)s", "Game").replace("%(id)s", "abc").replace("%(ext)s", "mkv")
            os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
            open(out, "wb").write(b"\x1aE\xdf\xa3fake-mkv")

    def run(cmd, *a, **k):
        _record_and_maybe_write(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    def ytdlp_run(cmd, timeout=None):
        _record_and_maybe_write(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda cmd, *a, **k: (_ for _ in ()).throw(AssertionError(f"Popen not mocked: {cmd}")),
    )
    monkeypatch.setattr(importlib.import_module("download_video"), "run_ytdlp_subprocess", ytdlp_run)
    return calls


@pytest.fixture
def fake_s3(monkeypatch):
    """Mock S3 client for upload tests."""
    boto3 = pytest.importorskip("boto3")
    
    calls = SimpleNamespace(upload=[], put=[], presign=[])
    class C:
        def upload_file(self, Filename, Bucket, Key, ExtraArgs=None, **k):
            calls.upload.append(dict(Filename=Filename, Bucket=Bucket, Key=Key, ExtraArgs=ExtraArgs or {}))
        def put_object(self, **k):
            calls.put.append(k)
        def generate_presigned_url(self, op, Params, ExpiresIn):
            calls.presign.append(Params)
            return f"https://b.s3/{Params['Key']}?sig=fake"
        def head_object(self, **k):
            return {}
    monkeypatch.setattr(boto3, "client", lambda *a, **k: C())
    monkeypatch.setenv("S3_BUCKET", "qa-fake-bucket")
    monkeypatch.setenv("AWS_REGION", "us-east-2")
    return calls


def flag_value(argv, flag):
    assert flag in argv, f"{flag} missing from {argv}"
    return argv[argv.index(flag) + 1]
