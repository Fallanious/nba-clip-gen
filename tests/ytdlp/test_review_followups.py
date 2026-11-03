"""PR #2 review follow-ups (Max + QA): Popen kwargs, exception chaining, venv, codecs, docs, film mime."""
import importlib.util
import io
import os
import re
import subprocess
import sys

import pytest

import download_video as dv
import generate_clips as gc
from conftest import (
    MKV_TYPE,
    PLAYBACK_MODULE,
    REPO_ROOT,
    load,
)

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def _has_pair(argv, a, b):
    argv = [str(x) for x in argv]
    return any(argv[i] == a and argv[i + 1] == b for i in range(len(argv) - 1))


# ---------- R1: stdout inherited, stderr captured/capped, timeout kills process group ----------

def test_ytdlp_stdout_inherited_stderr_captured(monkeypatch):
    seen = {}

    class FakePopen:
        def __init__(self, cmd, *a, **k):
            seen.update(k)
            self.pid = 1
            self.returncode = 0
            self.stderr = io.BytesIO(b"")

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    dv.run_ytdlp_subprocess(["yt-dlp", "--", URL], 10)
    assert seen["stdout"] is None
    assert seen["stderr"] is subprocess.PIPE
    assert seen.get("start_new_session") is True
    assert seen.get("shell", False) is False


def test_ytdlp_progress_reaches_parent_stdout(capfd):
    result = dv.run_ytdlp_subprocess(
        [sys.executable, "-c", "print('[download]  42.0%', flush=True)"],
        10,
    )
    assert "[download]  42.0%" in capfd.readouterr().out
    assert result.stdout == b""


def test_ytdlp_stderr_capped_64k():
    n = 200 * 1024
    script = (
        "import sys; sys.stderr.buffer.write(b'Z' * %d); "
        "sys.stderr.buffer.flush(); raise SystemExit(1)" % n
    )
    with pytest.raises(subprocess.CalledProcessError) as ei:
        dv.run_ytdlp_subprocess([sys.executable, "-c", script], 10)
    assert dv.STDERR_CAPTURE_MAX == 65536
    assert ei.value.stderr is not None
    assert len(ei.value.stderr) <= dv.STDERR_CAPTURE_MAX == 65536


def test_ytdlp_timeout_kills_process_group(monkeypatch):
    killed = []

    class FakePopen:
        def __init__(self, cmd, *a, **k):
            self.pid = 99
            self.returncode = None
            self.args = cmd
            self.stderr = io.BytesIO(b"")

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired(self.args, timeout)

    proc = FakePopen(["yt-dlp"],)

    def fake_popen(cmd, *a, **k):
        proc.args = cmd
        return proc

    def recorder(p):
        killed.append(p)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(dv, "_kill_process_group", recorder)
    with pytest.raises(subprocess.TimeoutExpired):
        dv.run_ytdlp_subprocess(["yt-dlp", "--", URL], 10)
    assert killed == [proc]
    assert proc.stderr.closed


# ---------- R2: CalledProcessError keeps cause + stdout ----------

def test_download_failure_keeps_exception_chain(tmp_path, monkeypatch):
    original = subprocess.CalledProcessError(
        1, ["yt-dlp"], output=b"OUT", stderr=b"ERROR: HTTP 403"
    )

    monkeypatch.setattr(
        dv,
        "build_ytdlp_cmd",
        lambda *a, **k: [sys.executable, "-m", "yt_dlp", "--js-runtimes", "node", "--", URL],
    )

    def boom(cmd, timeout=None):
        raise original

    monkeypatch.setattr(dv, "run_ytdlp_subprocess", boom)
    with pytest.raises(subprocess.CalledProcessError) as ei:
        dv.download_video(URL, output_folder=str(tmp_path))
    e = ei.value
    assert isinstance(e.__cause__, subprocess.CalledProcessError)
    assert e.__cause__.stderr == b"ERROR: HTTP 403"
    assert e.returncode == 1
    assert b"HTTP 403" in e.stderr
    # Prefer keeping the original stdout on the re-raised error.
    assert e.output in (b"OUT", None)
    assert e.output == b"OUT"


def test_download_failure_message_sanitized(tmp_path, monkeypatch):
    original = subprocess.CalledProcessError(
        1, ["yt-dlp"], output=b"OUT", stderr=b"\x1b[31mERR\x00" + b"x" * 2000
    )
    monkeypatch.setattr(
        dv,
        "build_ytdlp_cmd",
        lambda *a, **k: [sys.executable, "-m", "yt_dlp", "--js-runtimes", "node", "--", URL],
    )
    monkeypatch.setattr(dv, "run_ytdlp_subprocess", lambda *a, **k: (_ for _ in ()).throw(original))
    with pytest.raises(subprocess.CalledProcessError) as ei:
        dv.download_video(URL, output_folder=str(tmp_path))
    e = ei.value
    assert b"\x1b" not in e.stderr
    assert b"\x00" not in e.stderr
    assert len(e.stderr) <= len(b"yt-dlp failed: ") + 500


# ---------- R3: venv fallback is sys.executable, never bare python3 ----------

def test_venv_fallback_uses_sys_executable(monkeypatch):
    monkeypatch.setattr(os.path, "isfile", lambda p: False)
    assert dv._with_venv_python(["-m", "yt_dlp"])[0] == sys.executable
    assert "python3" not in dv._with_venv_python(["-m", "yt_dlp"])[:1]


def test_venv_python_preferred_when_present(monkeypatch):
    venv = os.path.join(REPO_ROOT, ".venv", "bin", "python")

    def isfile(p):
        return os.path.normpath(p) == os.path.normpath(venv)

    monkeypatch.setattr(os.path, "isfile", isfile)
    cmd = dv._with_venv_python(["-m", "yt_dlp"])
    assert cmd[0].endswith(os.path.join(".venv", "bin", "python"))


def test_build_cmd_interpreter_is_never_bare_python3(which):
    cmd = dv.build_ytdlp_cmd(URL)
    assert cmd[0] != "python3"
    assert os.path.isabs(cmd[0])


# ---------- R4: probe_codecs; re-encode unless H.264+AAC ----------

@pytest.mark.parametrize("ext", [".webm", ".mkv", ".mov", ".MKV"])
def test_mp4_output_reencodes_non_mp4_inputs(fake_run, ext):
    assert gc.extract_clip(f"/v/g{ext}", 0, 5, "/o/c.mp4", fast_copy=True)
    argv = [str(x) for x in fake_run[-1]]
    assert "-c:v" in argv and argv[argv.index("-c:v") + 1] == "libx264"
    assert "aac" in argv
    assert not _has_pair(argv, "-c", "copy")


def test_mp4_input_with_h264_aac_copies(fake_run, monkeypatch):
    monkeypatch.setattr(gc, "probe_codecs", lambda path: ("h264", "aac"))
    assert gc.extract_clip("/v/g.mp4", 0, 5, "/o/c.mp4", fast_copy=True)
    argv = [str(x) for x in fake_run[-1]]
    assert _has_pair(argv, "-c", "copy")


def test_mp4_input_with_av1_reencodes(fake_run, monkeypatch):
    monkeypatch.setattr(gc, "probe_codecs", lambda path: ("av1", "opus"))
    assert gc.extract_clip("/v/g.mp4", 0, 5, "/o/c.mp4", fast_copy=True)
    argv = [str(x) for x in fake_run[-1]]
    assert "libx264" in argv
    assert not _has_pair(argv, "-c", "copy")


@pytest.mark.parametrize("codecs", [("h264", "opus"), ("h264", None)])
def test_mp4_h264_without_aac_reencodes(fake_run, monkeypatch, codecs):
    monkeypatch.setattr(gc, "probe_codecs", lambda path: codecs)
    assert gc.extract_clip("/v/g.mp4", 0, 5, "/o/c.mp4", fast_copy=True)
    argv = [str(x) for x in fake_run[-1]]
    assert "libx264" in argv
    assert not _has_pair(argv, "-c", "copy")


def test_probe_failure_defaults_to_reencode(fake_run, monkeypatch):
    def boom(path):
        raise subprocess.CalledProcessError(1, ["ffprobe"])

    monkeypatch.setattr(gc, "probe_codecs", boom)
    assert gc.extract_clip("/v/g.mp4", 0, 5, "/o/c.mp4", fast_copy=True) is True
    argv = [str(x) for x in fake_run[-1]]
    assert "libx264" in argv


def test_mkv_output_may_copy(fake_run):
    assert gc.extract_clip("/v/g.webm", 0, 5, "/o/c.mkv", fast_copy=True)
    argv = [str(x) for x in fake_run[-1]]
    assert _has_pair(argv, "-c", "copy")
    assert "+faststart" not in argv


# ---------- R6: boto3 pin + restore playback skipif test ----------

def test_boto3_is_a_declared_runtime_dependency():
    req = open(os.path.join(REPO_ROOT, "requirements.txt"), encoding="utf-8").read()
    assert re.search(r"^boto3==\d", req, re.M)


@pytest.mark.skipif(
    importlib.util.find_spec("services.playback") is None,
    reason="needs PR #1 stack",
)
def test_fallback_presign_content_type_follows_key(fake_s3, monkeypatch):
    """PR #1 services/playback.py hardcodes ResponseContentType 'video/mp4' and returns mime
    'video/mp4'. Fallback presigns games.s3_key, which becomes .mkv."""
    mod = load(PLAYBACK_MODULE)
    monkeypatch.setattr(mod, "_s3_client", lambda *a, **k: __import__("boto3").client("s3"), raising=False)
    cfg = load("config").config
    for k, v in dict(S3_BUCKET="qa-fake-bucket", PLAYBACK_ALLOW_FALLBACK=True, PLAYBACK_VERIFY_OBJECT=False).items():
        monkeypatch.setattr(cfg, k, v, raising=False)
    res = mod.get_playback_url(dict(id=1, status="pending", s3_key=None, video_start_s=1.0, video_end_s=5.0,
                                    game_key="games/202604240PHI.mkv"), "auto")
    assert fake_s3.presign[-1]["ResponseContentType"] == MKV_TYPE
    assert res["mime"] == MKV_TYPE


# ---------- R7: README matches real config; CLI does not load .env ----------

def test_readme_has_no_removed_runtime_options():
    text = open(os.path.join(REPO_ROOT, "README.md"), encoding="utf-8").read()
    assert "YTDLP_JS_RUNTIME" not in text
    assert "--js-runtime " not in text and "--js-runtime\n" not in text


def test_readme_documented_flags_exist():
    readme = open(os.path.join(REPO_ROOT, "README.md"), encoding="utf-8").read()
    start = readme.find("### Step 1: Download Video")
    end = readme.find("### Step 2:", start)
    section = readme[start:end]
    flags = ["--" + name for name in re.findall(r"`--([a-z-]+)", section)]

    option_actions = dv.build_arg_parser()._option_string_actions

    help_text = subprocess.check_output(
        [sys.executable, "-m", "yt_dlp", "--help"],
        text=True,
        timeout=30,
    )
    for flag in flags:
        assert flag in option_actions or flag in help_text, f"{flag} missing from CLI and yt-dlp --help"


def test_env_file_is_loaded_or_not_documented():
    """Option B: the CLI never loads .env, so README must not tell users to put YTDLP_MAX_RES there."""
    text = open(os.path.join(REPO_ROOT, "README.md"), encoding="utf-8").read()
    assert ".env" not in text.split("### Step 1: Download Video")[1].split("### Step 2:")[0] or (
        "does not load .env" in text
    )
    download_section = text.split("### Step 1: Download Video")[1].split("### Step 2:")[0]
    assert "Via .env" not in download_section
    assert not re.search(r"\.env file", download_section)
    assert "YTDLP_MAX_RES" not in download_section or "export YTDLP_MAX_RES" in download_section


# ---------- R8: full games stay mkv; film route serves video/x-matroska ----------

def test_film_route_mimetype_mkv(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    film = tmp_path / "film"
    film.mkdir()
    (film / "g.mkv").write_bytes(b"\x1aE\xdf\xa3fake-mkv")
    import routes.videos as videos
    monkeypatch.setattr(videos, "BASE_DIR", str(tmp_path))
    from app import app

    app.config["TESTING"] = True
    with app.test_client() as client:
        resp = client.get("/api/film/g.mkv")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"].split(";")[0].strip() == "video/x-matroska"
    assert resp.headers["Content-Type"].split(";")[0].strip() != "application/octet-stream"


def test_full_game_docs_note_safari_unsupported():
    readme = open(os.path.join(REPO_ROOT, "README.md"), encoding="utf-8").read()
    import routes.videos as videos
    doc = videos.serve_video.__doc__ or ""
    for text in (readme, doc):
        assert "Safari is not supported for full-game playback" in text
        assert "not remuxed" in text or "are not remuxed" in text
    assert "clips are MP4" in readme or "clips are mp4" in doc.lower()
