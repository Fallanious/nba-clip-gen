"""yt-dlp CLI command, resolution config, JS runtime detection, mkv output."""
import os, pytest
from conftest import (load, flag_value, DOWNLOAD_MODULE, BUILD_CMD, DETECT_RUNTIME, RESOLVE_RES,
                      DOWNLOAD_FN, RUNTIME_ERROR, RES_ENV, DEFAULT_RES)

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def build(*a, **k):
    return [str(x) for x in load(DOWNLOAD_MODULE, BUILD_CMD)(*a, **k)]


# ---------- exact argv ----------
def test_exact_argv_default(which):
    argv = build(URL)
    assert os.path.basename(argv[0]) in ("yt-dlp", "yt-dlp.exe") or argv[:3][-1] == "yt_dlp"  # binary or `python -m yt_dlp`
    assert flag_value(argv, "--js-runtimes") == "node"
    assert flag_value(argv, "-S") == "res:720,+size"
    assert flag_value(argv, "-f") == "bv*+ba/b"
    assert flag_value(argv, "--merge-output-format") == "mkv"
    assert argv[-1] == URL                      # URL last, never interpolated into another arg
    assert argv.count(URL) == 1


def test_no_legacy_options(which):
    argv = " ".join(build(URL))
    for legacy in ("ext=mp4", "ext=m4a", "--merge-output-format mp4", "faststart", "height<=1080"):
        assert legacy not in argv


def test_argv_is_list_no_shell(which, fake_run, tmp_path):
    load(DOWNLOAD_MODULE, DOWNLOAD_FN)(URL, output_folder=str(tmp_path))
    assert fake_run and isinstance(fake_run[0], list)


@pytest.mark.parametrize("hostile", ["https://x.test/v?a=1;rm -rf /", "--exec=touch /tmp/pwn"])
def test_url_not_treated_as_option(which, hostile):
    argv = build(hostile)
    if hostile.startswith("-"):
        assert "--" in argv and argv.index("--") < argv.index(hostile), "option-looking URL must follow --"


# ---------- resolution ----------
def test_default_resolution_720(which):
    assert load(DOWNLOAD_MODULE, RESOLVE_RES)(None) == DEFAULT_RES
    assert flag_value(build(URL), "-S") == f"res:{DEFAULT_RES},+size"


@pytest.mark.parametrize("res", [144, 360, 480, 1080, 1440, 2160])
def test_custom_resolution_param(which, res):
    assert flag_value(build(URL, resolution=res), "-S") == f"res:{res},+size"


@pytest.mark.parametrize("res", ["1080", " 480 "])
def test_resolution_string_numeric_ok(which, res):
    assert flag_value(build(URL, resolution=res), "-S") == f"res:{int(res)},+size"


def test_resolution_from_env(which, monkeypatch):
    monkeypatch.setenv(RES_ENV, "1080")
    assert flag_value(build(URL), "-S") == "res:1080,+size"


def test_explicit_param_beats_env(which, monkeypatch):
    monkeypatch.setenv(RES_ENV, "1080")
    assert flag_value(build(URL, resolution=480), "-S") == "res:480,+size"


@pytest.mark.parametrize("bad", [0, -1, -720, "abc", "720p", "", "1e3", 1.5, 99999, 10**9, True, "720,+size", "720;x"])
def test_invalid_resolution_rejected(which, bad):
    with pytest.raises(ValueError) as ei:
        build(URL, resolution=bad)
    assert "resolution" in str(ei.value).lower()


@pytest.mark.parametrize("bad", ["0", "-5", "abc", "99999"])
def test_invalid_env_resolution_rejected(which, monkeypatch, bad):
    monkeypatch.setenv(RES_ENV, bad)
    with pytest.raises(ValueError, match="(?i)resolution|" + RES_ENV):
        build(URL)


@pytest.mark.parametrize("res", [None, 144, 720, 2160])
def test_res_cap_always_in_sort(which, res):
    s = flag_value(build(URL, resolution=res), "-S")
    parts = s.split(",")
    assert parts[0].startswith("res:") and parts[0][4:].isdigit() and "+size" in parts
    assert build(URL, resolution=res).count("-S") == 1


# ---------- JS runtime ----------
@pytest.mark.parametrize("avail,expected", [({"node"}, "node"), ({"deno"}, "deno"), ({"node", "deno"}, "node")])
def test_runtime_detection(which, avail, expected):
    which.available = avail | {"ffmpeg"}
    assert load(DOWNLOAD_MODULE, DETECT_RUNTIME)() == expected
    assert flag_value(build(URL), "--js-runtimes") == expected


def test_runtime_missing_clear_error(which):
    which.available = {"ffmpeg"}
    exc = load(DOWNLOAD_MODULE, RUNTIME_ERROR)
    with pytest.raises(exc) as ei:
        build(URL)
    msg = str(ei.value).lower()
    assert "node" in msg and "deno" in msg and ("install" in msg or "path" in msg)


def test_runtime_missing_download_does_not_spawn(which, fake_run, tmp_path):
    which.available = {"ffmpeg"}
    with pytest.raises(BaseException):  # error class, or SystemExit(!=0) from the CLI wrapper
        load(DOWNLOAD_MODULE, DOWNLOAD_FN)(URL, output_folder=str(tmp_path))
    assert fake_run == []


def test_cli_main_runtime_missing_exits_nonzero(which, monkeypatch, capsys):
    which.available = {"ffmpeg"}
    mod = load(DOWNLOAD_MODULE)
    monkeypatch.setattr("sys.argv", ["download_video.py", URL])
    with pytest.raises(SystemExit) as ei:
        mod.main()
    assert ei.value.code not in (0, None)
    out = capsys.readouterr()
    assert "node" in (out.out + out.err).lower()


# ---------- mkv output ----------
def test_output_template_and_returned_path_mkv(which, fake_run, tmp_path):
    path = load(DOWNLOAD_MODULE, DOWNLOAD_FN)(URL, output_folder=str(tmp_path))
    argv = [str(x) for x in fake_run[0]]
    tmpl = flag_value(argv, "-o")
    assert tmpl.startswith(str(tmp_path)) and tmpl.endswith(".%(ext)s")
    assert path and str(path).endswith(".mkv") and os.path.isfile(path)


def test_ytdlp_failure_surfaces(which, monkeypatch, tmp_path):
    import subprocess
    def boom(cmd, *a, **k):
        raise subprocess.CalledProcessError(1, cmd, stderr=b"ERROR: HTTP Error 403")
    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(BaseException) as ei:
        load(DOWNLOAD_MODULE, DOWNLOAD_FN)(URL, output_folder=str(tmp_path))
    assert not isinstance(ei.value, SystemExit) or ei.value.code != 0


def test_saved_file_detection_accepts_mkv(tmp_path):
    import time
    started = time.time()
    p = tmp_path / "Game.mkv"; p.write_bytes(b"x")
    found = load(DOWNLOAD_MODULE, "_video_saved_since")(str(tmp_path), started)
    assert found and found.endswith(".mkv")
