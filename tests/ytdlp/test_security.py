"""Security tests for yt-dlp/ffmpeg fixes (section #2 and re-review)."""

import os
import re
import sys
import signal
import subprocess
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from download_video import (
    DEFAULT_MAX_FILESIZE,
    DEFAULT_SOCKET_TIMEOUT,
    DEFAULT_TIMEOUT,
    MAX_MAX_FILESIZE,
    MAX_SOCKET_TIMEOUT,
    MAX_TIMEOUT,
    MIN_MAX_FILESIZE,
    MIN_SOCKET_TIMEOUT,
    MIN_TIMEOUT,
    STDERR_CAPTURE_MAX,
    _drain_stderr_capped,
    _sanitize_stderr,
    _validate_youtube_url,
    build_ytdlp_cmd,
    download_video,
    resolve_max_filesize,
    resolve_socket_timeout,
    resolve_timeout,
    run_ytdlp_subprocess,
)
from services.storage import (
    BBREF_ID_RE,
    _validate_bbref_id,
    s3_key_for_game,
    upload_game,
)

from conftest import REPO_ROOT, flag_value

SAFE_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def _ytdlp_args(cmd):
    """Return argv as yt-dlp itself sees it (after python -m yt_dlp)."""
    if "yt_dlp" in cmd:
        return cmd[cmd.index("yt_dlp") + 1 :]
    return cmd[1:]


# ==================== URL Validation Tests ====================

class TestYouTubeURLValidation:
    """Test URL validation (security: only HTTPS YouTube URLs)."""

    def test_https_youtube_com_valid(self):
        assert _validate_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert _validate_youtube_url("https://youtube.com/watch?v=dQw4w9WgXcQ")
        assert _validate_youtube_url("https://m.youtube.com/watch?v=dQw4w9WgXcQ")

    def test_https_youtu_be_valid(self):
        assert _validate_youtube_url("https://youtu.be/dQw4w9WgXcQ")
        assert _validate_youtube_url("https://www.youtu.be/dQw4w9WgXcQ")

    def test_http_rejected(self):
        with pytest.raises(ValueError, match="Only HTTPS URLs are allowed"):
            _validate_youtube_url("http://youtube.com/watch?v=test")

    def test_non_youtube_domain_rejected(self):
        with pytest.raises(ValueError, match="Only YouTube URLs are allowed"):
            _validate_youtube_url("https://evil.com/watch?v=test")

        with pytest.raises(ValueError, match="Only YouTube URLs are allowed"):
            _validate_youtube_url("https://youtube.evil.com/watch?v=test")

    def test_empty_url_rejected(self):
        with pytest.raises(ValueError, match="URL cannot be empty"):
            _validate_youtube_url("")

    def test_malformed_url_rejected(self):
        with pytest.raises(ValueError, match="URL must have a valid hostname"):
            _validate_youtube_url("https://")

    def test_userinfo_rejected(self):
        with pytest.raises(ValueError, match="userinfo"):
            _validate_youtube_url("https://user:pass@youtube.com/watch?v=test")
        with pytest.raises(ValueError, match="userinfo"):
            _validate_youtube_url("https://evil.com@youtube.com/watch?v=test")

    def test_port_rejected(self):
        with pytest.raises(ValueError, match="port"):
            _validate_youtube_url("https://youtube.com:443/watch?v=test")
        with pytest.raises(ValueError, match="port"):
            _validate_youtube_url("https://www.youtube.com:8080/watch?v=test")

    def test_backslash_rejected(self):
        with pytest.raises(ValueError, match="backslash"):
            _validate_youtube_url(r"https://youtube.com\watch?v=test")
        with pytest.raises(ValueError, match="backslash"):
            _validate_youtube_url(r"https://youtube.com\@evil.com/watch?v=test")

    def test_whitespace_rejected(self):
        with pytest.raises(ValueError, match="whitespace"):
            _validate_youtube_url("https://youtube.com/watch?v=test extra")
        with pytest.raises(ValueError, match="whitespace"):
            _validate_youtube_url("https://youtube.com/watch?v=test\n")
        with pytest.raises(ValueError, match="whitespace"):
            _validate_youtube_url(" https://youtube.com/watch?v=test")


# ==================== Timeout / size clamp Tests ====================

@pytest.mark.usefixtures("which")
class TestDownloadTimeouts:
    """Test timeout configuration (security: prevent indefinite hangs)."""

    def test_socket_timeout_in_command(self, monkeypatch):
        monkeypatch.setenv("YTDLP_SOCKET_TIMEOUT", "120")
        cmd = build_ytdlp_cmd(SAFE_URL)
        assert flag_value(cmd, "--socket-timeout") == "120"

    def test_max_filesize_in_command(self, monkeypatch):
        monkeypatch.setenv("YTDLP_MAX_FILESIZE", "1000000000")
        cmd = build_ytdlp_cmd(SAFE_URL)
        assert flag_value(cmd, "--max-filesize") == "1000000000"

    def test_default_socket_timeout_is_60(self, monkeypatch):
        monkeypatch.delenv("YTDLP_SOCKET_TIMEOUT", raising=False)
        assert resolve_socket_timeout() == 60
        cmd = build_ytdlp_cmd(SAFE_URL)
        assert flag_value(cmd, "--socket-timeout") == "60"

    def test_socket_timeout_clamped_to_min(self, monkeypatch):
        monkeypatch.setenv("YTDLP_SOCKET_TIMEOUT", "1")
        assert resolve_socket_timeout() == MIN_SOCKET_TIMEOUT

    def test_socket_timeout_clamped_to_max(self, monkeypatch):
        monkeypatch.setenv("YTDLP_SOCKET_TIMEOUT", "99999")
        assert resolve_socket_timeout() == MAX_SOCKET_TIMEOUT

    def test_timeout_clamped_to_min(self, monkeypatch):
        monkeypatch.setenv("YTDLP_TIMEOUT", "1")
        assert resolve_timeout() == MIN_TIMEOUT

    def test_timeout_clamped_to_max(self, monkeypatch):
        monkeypatch.setenv("YTDLP_TIMEOUT", "999999")
        assert resolve_timeout() == MAX_TIMEOUT

    def test_filesize_clamped_to_min(self, monkeypatch):
        monkeypatch.setenv("YTDLP_MAX_FILESIZE", "1")
        assert resolve_max_filesize() == MIN_MAX_FILESIZE

    def test_filesize_clamped_to_max(self, monkeypatch):
        monkeypatch.setenv("YTDLP_MAX_FILESIZE", str(10**18))
        assert resolve_max_filesize() == MAX_MAX_FILESIZE

    def test_default_timeout_and_filesize(self, monkeypatch):
        monkeypatch.delenv("YTDLP_TIMEOUT", raising=False)
        monkeypatch.delenv("YTDLP_MAX_FILESIZE", raising=False)
        assert resolve_timeout() == DEFAULT_TIMEOUT
        assert resolve_max_filesize() == DEFAULT_MAX_FILESIZE
        assert DEFAULT_SOCKET_TIMEOUT == 60

    def test_subprocess_timeout_applied(self, monkeypatch, tmp_path):
        monkeypatch.setenv("YTDLP_TIMEOUT", "60")

        def boom(cmd, timeout=None):
            raise subprocess.TimeoutExpired(cmd, timeout)

        monkeypatch.setattr("download_video.run_ytdlp_subprocess", boom)
        with pytest.raises(subprocess.TimeoutExpired):
            download_video(SAFE_URL, str(tmp_path))


# ==================== Command Structure Tests ====================

@pytest.mark.usefixtures("which")
class TestCommandStructure:
    """Test command structure security."""

    def test_always_uses_double_dash(self):
        cmd = build_ytdlp_cmd(SAFE_URL)
        assert "--" in cmd
        assert cmd[-1] == SAFE_URL
        assert cmd[-2] == "--"

    def test_double_dash_is_second_to_last_and_url_is_last(self):
        cmd = build_ytdlp_cmd(SAFE_URL, output_template="/tmp/%(id)s.%(ext)s")
        assert cmd[-1] == SAFE_URL
        assert cmd[-2] == "--"
        assert cmd.index("-o") < cmd.index("--")
        assert cmd.index("-o") + 1 < cmd.index("--")

    def test_output_option_is_before_double_dash(self):
        cmd = build_ytdlp_cmd(SAFE_URL, output_template="/film/%(id)s.%(ext)s")
        o_idx = cmd.index("-o")
        dash_idx = cmd.index("--")
        assert o_idx < dash_idx
        assert "%(id)s" in cmd[o_idx + 1]
        assert "%(title)s" not in cmd[o_idx + 1]
        # Nothing after -- except the URL
        assert cmd[dash_idx + 1 :] == [SAFE_URL]

    def test_option_looking_url_after_double_dash(self):
        with pytest.raises(ValueError):
            build_ytdlp_cmd("--exec=malicious")

    def test_restrict_filenames_present(self):
        cmd = build_ytdlp_cmd(SAFE_URL)
        assert "--restrict-filenames" in cmd

    def test_ignore_config_and_no_playlist_present(self):
        cmd = build_ytdlp_cmd(SAFE_URL)
        assert "--ignore-config" in cmd
        assert "--no-playlist" in cmd
        dash = cmd.index("--")
        assert cmd.index("--ignore-config") < dash
        assert cmd.index("--no-playlist") < dash

    def test_uses_video_id_not_title(self):
        cmd = build_ytdlp_cmd(SAFE_URL)
        tmpl = flag_value(cmd, "-o")
        assert "%(id)s" in tmpl
        assert "%(title)s" not in tmpl


# ==================== Real yt-dlp CLI parse test ====================

@pytest.mark.usefixtures("which")
class TestRealYtDlpCli:
    """Confirm the built argv is accepted by the pinned yt-dlp (no download, no network)."""

    def test_pinned_ytdlp_help_includes_js_runtimes(self):
        help_text = subprocess.check_output(
            [sys.executable, "-m", "yt_dlp", "--help"],
            text=True,
            timeout=30,
        )
        assert "--js-runtimes" in help_text
        for flag in (
            "--ignore-config",
            "--no-playlist",
            "--restrict-filenames",
            "--socket-timeout",
            "--max-filesize",
            "--merge-output-format",
        ):
            assert flag in help_text

    def test_built_argv_accepted_by_real_ytdlp_cli(self):
        """Run the real CLI with our options plus --version (no URL, no network)."""
        cmd = build_ytdlp_cmd(SAFE_URL)
        dash = cmd.index("--")
        check = cmd[:dash] + ["--version"]
        result = subprocess.run(check, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        version = (result.stdout or result.stderr).strip()
        assert version.startswith("2026"), version
        help_text = subprocess.check_output(
            [sys.executable, "-m", "yt_dlp", "--help"],
            text=True,
            timeout=30,
        )
        for flag in _ytdlp_args(cmd):
            if flag.startswith("-") and flag != "--":
                # -S / -f / -o are short options listed in --help
                assert flag.lstrip("-")[0]  # non-empty
                if flag.startswith("--"):
                    assert flag in help_text, f"{flag} missing from yt-dlp --help"

    def test_built_argv_parses_with_ytdlp_parse_options(self):
        from yt_dlp import parse_options

        cmd = build_ytdlp_cmd(SAFE_URL)
        parsed = parse_options(_ytdlp_args(cmd))
        assert parsed.urls == [SAFE_URL]
        opts = parsed.ydl_opts
        assert opts["merge_output_format"] == "mkv"
        assert opts["restrictfilenames"] is True
        assert opts["noplaylist"] is True
        assert opts["format"] == "bv*+ba/b"
        assert "node" in opts["js_runtimes"]
        assert "%(id)s" in str(opts["outtmpl"])


# ==================== Process group kill ====================

class TestProcessGroupKill:
    def test_timeout_kills_process_group(self, monkeypatch):
        import io

        killed = []

        class FakePopen:
            def __init__(self, cmd, *a, **k):
                assert k.get("start_new_session") is True
                self.pid = 4242
                self.returncode = None
                self.args = cmd
                self.stderr = io.BytesIO(b"")

            def wait(self, timeout=None):
                raise subprocess.TimeoutExpired(self.args, timeout)

            def kill(self):
                killed.append(("kill", self.pid))

            def poll(self):
                return self.returncode

        def fake_killpg(pid, sig):
            killed.append((pid, sig))

        monkeypatch.setattr(subprocess, "Popen", FakePopen)
        monkeypatch.setattr(os, "killpg", fake_killpg)

        with pytest.raises(subprocess.TimeoutExpired):
            run_ytdlp_subprocess(["python3", "-m", "yt_dlp"], timeout=1)

        assert (4242, signal.SIGKILL) in killed

    def test_popen_uses_new_session(self, monkeypatch):
        import io

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
        run_ytdlp_subprocess(["true"], timeout=5)
        assert seen.get("start_new_session") is True
        assert seen.get("bufsize") == 0
        assert seen.get("stdout") is None
        assert seen.get("stderr") is subprocess.PIPE


# ==================== Stderr Sanitization Tests ====================

class TestStderrSanitization:
    def test_sanitize_normal_error(self):
        stderr = b"ERROR: HTTP Error 403: Forbidden"
        result = _sanitize_stderr(stderr)
        assert "ERROR" in result
        assert "403" in result

    def test_sanitize_truncates_long_output(self):
        stderr = b"A" * 1000
        result = _sanitize_stderr(stderr, max_length=500)
        assert len(result) <= 520
        assert "truncated" in result

    def test_sanitize_removes_control_characters(self):
        stderr = b"Error\x00\x01\x02 message\x1b[31m colored\x1b[0m"
        result = _sanitize_stderr(stderr)
        assert "\x00" not in result
        assert "\x01" not in result
        assert "\x1b" not in result

    def test_sanitize_handles_invalid_utf8(self):
        stderr = b"Error \xff\xfe message"
        result = _sanitize_stderr(stderr)
        assert "Error" in result
        assert isinstance(result, str)

    def test_sanitize_empty_stderr(self):
        result = _sanitize_stderr(b"")
        assert "No error details available" in result

    def test_sanitize_none_stderr(self):
        result = _sanitize_stderr(None)
        assert "No error details available" in result

    def test_drain_stderr_capped_keeps_limit(self):
        import io

        data = b"A" * (STDERR_CAPTURE_MAX + 8000)
        result = _drain_stderr_capped(io.BytesIO(data))
        assert len(result) == STDERR_CAPTURE_MAX
        assert result == b"A" * STDERR_CAPTURE_MAX

    def test_run_ytdlp_subprocess_caps_stderr(self):
        n = STDERR_CAPTURE_MAX + 20000
        script = (
            "import sys; sys.stderr.buffer.write(b'Z' * %d); "
            "sys.stderr.buffer.flush(); raise SystemExit(1)" % n
        )
        with pytest.raises(subprocess.CalledProcessError) as ei:
            run_ytdlp_subprocess([sys.executable, "-c", script], timeout=10)
        assert ei.value.stderr is not None
        assert len(ei.value.stderr) <= STDERR_CAPTURE_MAX


# ==================== bbref_id Validation Tests ====================

class TestBbrefIdValidation:
    def test_valid_bbref_id(self):
        assert _validate_bbref_id("202604240PHI") == "202604240PHI"
        assert _validate_bbref_id("202511110BOS") == "202511110BOS"

    def test_invalid_format_too_short(self):
        with pytest.raises(ValueError, match="Invalid bbref_id format"):
            _validate_bbref_id("2026042PHI")

    def test_invalid_format_too_long(self):
        with pytest.raises(ValueError, match="Invalid bbref_id format"):
            _validate_bbref_id("2026042400PHI")

    def test_invalid_format_lowercase_letters(self):
        with pytest.raises(ValueError, match="Invalid bbref_id format"):
            _validate_bbref_id("202604240phi")

    def test_invalid_format_special_characters(self):
        with pytest.raises(ValueError, match="Invalid bbref_id format"):
            _validate_bbref_id("202604240../")
        with pytest.raises(ValueError, match="Invalid bbref_id format"):
            _validate_bbref_id("202604240PHI; DROP TABLE")

    def test_invalid_format_path_traversal(self):
        with pytest.raises(ValueError, match="Invalid bbref_id format"):
            _validate_bbref_id("../../../etc/passwd")
        with pytest.raises(ValueError, match="Invalid bbref_id format"):
            _validate_bbref_id("202604240../")

    def test_empty_bbref_id(self):
        with pytest.raises(ValueError, match="bbref_id cannot be empty"):
            _validate_bbref_id("")

    def test_non_string_bbref_id(self):
        with pytest.raises(ValueError, match="bbref_id must be a string"):
            _validate_bbref_id(202604240)

    def test_regex_is_ascii_digits_not_unicode_nd(self):
        src = open(os.path.join(REPO_ROOT, "src", "services", "storage.py"), encoding="utf-8").read()
        assert r"[0-9]{9}[A-Z]{3}" in src
        assert r"\d{9}" not in src
        assert BBREF_ID_RE.pattern == r"[0-9]{9}[A-Z]{3}"

    def test_unicode_digits_rejected(self):
        # Arabic-Indic digits would match \d but must not match [0-9]
        with pytest.raises(ValueError, match="Invalid bbref_id format"):
            _validate_bbref_id("٢٠٢٦٠٤٢٤٠PHI")

    def test_s3_key_validates_bbref_id(self):
        key = s3_key_for_game("202604240PHI", "/path/video.mkv")
        assert key == "games/202604240PHI.mkv"
        with pytest.raises(ValueError, match="Invalid bbref_id format"):
            s3_key_for_game("../../../etc", "/path/video.mkv")

    def test_s3_key_validates_extension(self):
        assert s3_key_for_game("202604240PHI", "/path/video.mp4") == "games/202604240PHI.mp4"
        assert s3_key_for_game("202604240PHI", "/path/video.webm") == "games/202604240PHI.webm"
        with pytest.raises(ValueError, match="Unsupported video format"):
            s3_key_for_game("202604240PHI", "/path/video.exe")
        with pytest.raises(ValueError, match="Unsupported video format"):
            s3_key_for_game("202604240PHI", "/path/video")
        with pytest.raises(ValueError, match="Unsupported video format"):
            s3_key_for_game("202604240PHI", "/path/video.txt")

    @patch("services.storage.boto3")
    def test_upload_game_validates_bbref_id(self, mock_boto3, tmp_path, monkeypatch):
        monkeypatch.setenv("S3_BUCKET", "test-bucket")

        video_file = tmp_path / "video.mkv"
        video_file.write_bytes(b"test")

        mock_s3 = MagicMock()
        mock_boto3.client.return_value = mock_s3

        upload_game(str(video_file), "202604240PHI")
        mock_s3.upload_file.assert_called_once()

        with pytest.raises(ValueError, match="Invalid bbref_id format"):
            upload_game(str(video_file), "../../../etc")


# ==================== Integration Tests ====================

@pytest.mark.usefixtures("which")
class TestSecurityIntegration:
    def test_url_validation_in_build_cmd(self):
        with pytest.raises(ValueError, match="Only HTTPS URLs are allowed"):
            build_ytdlp_cmd("http://youtube.com/watch?v=test")

    def test_url_validation_in_download(self, tmp_path):
        with pytest.raises(ValueError, match="Only HTTPS URLs are allowed"):
            download_video("http://youtube.com/watch?v=test", str(tmp_path))

    def test_stderr_sanitized_in_download(self, tmp_path, monkeypatch):
        original = subprocess.CalledProcessError(
            1, ["yt-dlp"], output=b"stdout-keep", stderr=b"Very " + b"long " * 200 + b" error"
        )

        def boom(cmd, timeout=None):
            raise original

        monkeypatch.setattr("download_video.run_ytdlp_subprocess", boom)
        with pytest.raises(subprocess.CalledProcessError) as exc:
            download_video(SAFE_URL, str(tmp_path))

        error_msg = exc.value.stderr.decode("utf-8")
        assert "truncated" in error_msg or len(error_msg) < 600
        assert exc.value.__cause__ is original
        assert exc.value.output == b"stdout-keep"

    def test_venv_fallback_is_sys_executable(self, monkeypatch):
        monkeypatch.setattr(os.path, "isfile", lambda path: False)
        cmd = build_ytdlp_cmd(SAFE_URL)
        assert cmd[0] == sys.executable

# ==================== Supply chain / repo policy Tests ====================

class TestSupplyChain:
    def _lock_version(self, text, name):
        match = re.search(rf"^{re.escape(name)}==([0-9][^\s\\]+)", text, re.M)
        assert match, f"{name} missing from lock/requirements"
        return match.group(1)

    def _version_tuple(self, ver):
        return tuple(int(p) if p.isdigit() else p for p in ver.split("."))

    def test_requirements_lock_has_hashes_and_ytdlp_pin(self):
        lock_path = os.path.join(REPO_ROOT, "requirements.lock")
        assert os.path.isfile(lock_path), "requirements.lock is missing"
        lock = open(lock_path, encoding="utf-8").read()
        assert "--hash=sha256:" in lock
        assert "yt-dlp==2026.10.26" in lock
        for pkg in ("boto3==", "sqlalchemy==", "python-dotenv==", "psycopg==", "alembic==", "click=="):
            assert pkg in lock
        assert "flask==3.1.0" not in lock
        assert "lxml==5.3.0" not in lock
        assert "python-dotenv==1.0.1" not in lock
        assert "requests==2.32.3" not in lock
        assert self._version_tuple(self._lock_version(lock, "flask")) >= (3, 1, 3)
        assert self._version_tuple(self._lock_version(lock, "lxml")) >= (6, 1, 0)
        assert self._version_tuple(self._lock_version(lock, "python-dotenv")) >= (1, 2, 2)
        assert self._version_tuple(self._lock_version(lock, "requests")) >= (2, 33, 0)

    def test_runtime_requirements_pin_ytdlp_and_db_libs(self):
        req = open(os.path.join(REPO_ROOT, "requirements.txt"), encoding="utf-8").read()
        assert "yt-dlp==2026.10.26" in req
        for pkg in ("psycopg==", "alembic==", "click==", "boto3==", "sqlalchemy==", "python-dotenv=="):
            assert pkg in req
        for dev in ("uv==", "pylint==", "black==", "ruff==", "pytest=="):
            assert not re.search(rf"^{re.escape(dev)}", req, re.M)
        assert self._version_tuple(self._lock_version(req, "flask")) >= (3, 1, 3)
        assert self._version_tuple(self._lock_version(req, "lxml")) >= (6, 1, 0)
        assert self._version_tuple(self._lock_version(req, "python-dotenv")) >= (1, 2, 2)
        assert self._version_tuple(self._lock_version(req, "requests")) >= (2, 33, 0)

    def test_dev_tools_live_in_requirements_dev(self):
        dev = open(os.path.join(REPO_ROOT, "requirements-dev.txt"), encoding="utf-8").read()
        assert "-r requirements.txt" in dev
        for pkg in ("uv==", "pylint==", "black==", "ruff==", "pytest==", "pip-audit=="):
            assert pkg in dev
        assert self._version_tuple(self._lock_version(dev, "uv")) >= (0, 11, 15)
        assert self._version_tuple(self._lock_version(dev, "black")) >= (26, 10, 1)
        assert self._version_tuple(self._lock_version(dev, "pylint")) >= (4, 0, 9)
        assert self._version_tuple(self._lock_version(dev, "pytest")) >= (9, 0, 3)

    def test_requirements_dev_lock_has_hashes(self):
        lock_path = os.path.join(REPO_ROOT, "requirements-dev.lock")
        assert os.path.isfile(lock_path), "requirements-dev.lock is missing"
        lock = open(lock_path, encoding="utf-8").read()
        assert "--hash=sha256:" in lock
        for pkg in ("pytest==", "pip-audit==", "black==", "ruff==", "pylint==", "uv=="):
            assert pkg in lock
        assert self._version_tuple(self._lock_version(lock, "uv")) >= (0, 11, 15)
        assert self._version_tuple(self._lock_version(lock, "black")) >= (26, 10, 1)
        assert self._version_tuple(self._lock_version(lock, "pylint")) >= (4, 0, 9)
        assert self._version_tuple(self._lock_version(lock, "pytest")) >= (9, 0, 3)

    def test_pip_audit_install_uses_hashed_requirements(self):
        hashed = os.path.join(REPO_ROOT, "requirements-pip-audit.txt")
        assert os.path.isfile(hashed)
        text = open(hashed, encoding="utf-8").read()
        assert "pip-audit==2.10.1" in text
        assert "--hash=sha256:" in text

    def test_security_audit_workflow_hardening(self):
        path = os.path.join(REPO_ROOT, ".github", "workflows", "security-audit.yml")
        src = open(path, encoding="utf-8").read()
        assert "contents: read" in src
        assert re.search(r"actions/checkout@[0-9a-f]{40}", src)
        assert re.search(r"actions/setup-python@[0-9a-f]{40}", src)
        assert "--require-hashes" in src
        assert "requirements-pip-audit.txt" in src
        assert "pip install pip-audit==2.10.1" not in src
        assert "requirements.lock" in src
        assert "pip install -r requirements.txt" not in src
        assert "pip-audit --desc -r requirements.lock" in src
        assert "pip-audit --desc -r requirements-dev.lock" in src

    def test_readme_does_not_document_removed_js_runtime_config(self):
        readme = open(os.path.join(REPO_ROOT, "README.md"), encoding="utf-8").read()
        assert "YTDLP_JS_RUNTIME" not in readme
        assert "--js-runtime " not in readme
        assert "--js-runtime\n" not in readme


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
