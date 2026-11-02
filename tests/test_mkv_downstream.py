"""Downstream of the mkv change: ffmpeg clipping, extensions, S3 keys/ContentType, leftover .mp4."""
import os, re, pytest
from conftest import (load, flag_value, CLIPS_MODULE, STORAGE_MODULE,
                      REPO_ROOT, MKV_TYPE, SCAN_DIRS)


# ---------- ffmpeg accepts mkv input ----------
@pytest.mark.parametrize("fast_copy", [True, False])
def test_extract_clip_from_mkv(fake_run, tmp_path, fast_copy):
    src, out = str(tmp_path / "game.mkv"), str(tmp_path / "clip.mp4")
    assert load(CLIPS_MODULE, "extract_clip")(src, 10.0, 16.5, out, fast_copy=fast_copy)
    argv = [str(x) for x in fake_run[-1]]
    assert argv[0].endswith("ffmpeg") and flag_value(argv, "-i") == src
    assert argv.index("-ss") < argv.index("-i")
    assert out in argv


def test_mkv_to_mp4_fast_copy_is_browser_safe(fake_run, tmp_path):
    """mkv from bv*+ba/b may carry VP9/AV1+Opus. A -c copy into .mp4 then plays poorly in Safari.
    Expect either re-encode (libx264/aac) or an explicit, documented codec check (Q-Y4)."""
    load(CLIPS_MODULE, "extract_clip")(str(tmp_path / "g.mkv"), 0, 5, str(tmp_path / "c.mp4"))
    argv = " ".join(map(str, fake_run[-1]))
    assert "libx264" in argv or "-c copy" not in argv, "copying unknown codecs from mkv into mp4"


def test_faststart_only_for_mp4_output(fake_run, tmp_path):
    load(CLIPS_MODULE, "extract_clip")(str(tmp_path / "g.mkv"), 0, 5, str(tmp_path / "c.mkv"))
    assert "+faststart" not in map(str, fake_run[-1])  # mov/mp4-only muxer flag


def test_combine_clips_mkv_concat_file_not_output(fake_run, tmp_path, monkeypatch):
    """main's combine_clips uses output_path.replace('.mp4', '_concat.txt'); for .mkv that
    returns output_path itself, so the concat list overwrites/is the output."""
    out = str(tmp_path / "combined.mkv")
    a = tmp_path / "a.mkv"; a.write_bytes(b"x")
    load(CLIPS_MODULE, "combine_clips")([str(a)], out)
    argv = [str(x) for x in fake_run[-1]]
    assert flag_value(argv, "-i") != out


def test_generate_clips_cli_accepts_mkv_source(fake_run, tmp_path, monkeypatch):
    import json, sys
    # Create dummy mkv file
    (tmp_path / "game.mkv").write_bytes(b"x")
    ts = tmp_path / "ts.json"
    # Need at least one play for the script to proceed
    ts.write_text(json.dumps([{"video_start": 0, "video_end": 1, "team": "T", "quarter": 1, "game_time": "12:00", "description": "test"}]))
    monkeypatch.setattr(sys, "argv", ["generate_clips.py", str(tmp_path / "game.mkv"), str(ts), "--output-dir", str(tmp_path)])
    mod = load(CLIPS_MODULE)
    try:
        mod.main()
    except SystemExit as e:
        assert e.code in (0, None), "mkv source rejected by CLI"


# ---------- extensions / listing ----------
def test_timestamps_infers_mkv_video(tmp_path, monkeypatch):
    mod = load("routes.timestamps")
    assert ".mkv" in open(mod.__file__).read()  # infer_video_from_timestamps already loops .mp4/.mkv/.webm


def test_clip_listing_not_mp4_only():
    """routes/timestamps.py filters clips with endswith('.mp4') (get_clips, get_clip_runs). If clips
    can be mkv (generate_clips --format mkv) they vanish from the library."""
    src = open(load("routes.timestamps").__file__).read()
    assert not re.search(r"endswith\(\s*['\"]\.mp4['\"]\s*\)", src)


# ---------- S3 key + ContentType ----------
@pytest.mark.parametrize("name,ctype", [("g.mkv", MKV_TYPE), ("g.MKV", MKV_TYPE), ("c.mp4", "video/mp4"), ("c.webm", "video/webm")])
def test_content_type_for(name, ctype):
    assert load(STORAGE_MODULE, "content_type_for")(name) == ctype


def test_content_type_unknown_rejected():
    with pytest.raises(ValueError):
        load(STORAGE_MODULE, "content_type_for")("g.avi")


def test_game_key_keeps_mkv_extension():
    key = load(STORAGE_MODULE, "s3_key_for_game")("202604240PHI", "/film/Game.mkv")
    assert key.endswith("202604240PHI.mkv") and not key.endswith(".mp4")


def test_upload_sets_content_type_mkv(fake_s3, tmp_path):
    f = tmp_path / "Game.mkv"; f.write_bytes(b"x")
    mod = load(STORAGE_MODULE)
    up = getattr(mod, "upload_game", None) or pytest.fail("services.storage.upload_game missing (Q-Y6)")
    up(str(f), "202604240PHI")
    call = (fake_s3.upload or fake_s3.put)[-1]
    extra = call.get("ExtraArgs", call)
    assert call["Key"].endswith(".mkv") and extra["ContentType"] == MKV_TYPE


# ---------- leftover hardcoded .mp4 audit ----------
ALLOWED_MP4 = [  # file -> regex of lines that may legitimately mention mp4 (clip output is still mp4)
    (r"generate_clips\.py", r"default='mp4'|%\(prog\)s video|\.mp4\b.*example|mkv.* to mp4|mp4|\.mov\)|input_ext.*output_ext|force_reencode"),
    (r"routes/(videos|timestamps)\.py", r"\(\s*\"\.mp4\",\s*\"\.mkv\"|# Support all video formats.*mp4"),
    (r"services/(playback|storage)\.py", r"video/mp4"),
    (r"download_video\.py", r"\.mp4.*\.mkv.*\.webm"),
]


def _mp4_hits():
    hits = []
    for d in SCAN_DIRS:
        for root, _, files in os.walk(os.path.join(REPO_ROOT, d)):
            for f in files:
                if not f.endswith(".py"):
                    continue
                p = os.path.join(root, f); rel = os.path.relpath(p, REPO_ROOT)
                for i, line in enumerate(open(p, encoding="utf-8"), 1):
                    if re.search(r"mp4", line, re.I) and not any(
                            re.search(fr, rel) and re.search(lr, line) for fr, lr in ALLOWED_MP4):
                        hits.append(f"{rel}:{i}: {line.strip()}")
    return hits


def test_no_unreviewed_hardcoded_mp4():
    hits = _mp4_hits()
    assert not hits, "Unreviewed mp4 references (fix or add to ALLOWED_MP4 with a reason):\n" + "\n".join(hits)
