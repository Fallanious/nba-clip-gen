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


@pytest.mark.parametrize("src_name", ["g.webm", "g.mp4"])
def test_non_mkv_to_mp4_also_reencodes(fake_run, tmp_path, src_name):
    """`/b` fallback can yield webm (VP9/Opus) or an mp4 that still carries AV1."""
    load(CLIPS_MODULE, "extract_clip")(str(tmp_path / src_name), 0, 5, str(tmp_path / "c.mp4"))
    argv = " ".join(map(str, fake_run[-1]))
    assert "libx264" in argv
    assert "-c copy" not in argv


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
# Exact (relpath, stripped line) -> reason. Unanchored regexes were matching stray lines.
ALLOWED_MP4 = {  # (relpath, exact stripped line) -> reason
    ("src/download_video.py", 'if not name.endswith((".mp4", ".mkv", ".webm")):'): "saved-file scan",
    ("src/generate_clips.py", "Note: mp4/mov clip output is re-encoded unless the source is already H.264+AAC"): "doc",
    ("src/generate_clips.py", "# mkv/webm/mov from bv*+ba/b may contain VP9/AV1+Opus. Using -c copy into mp4 can result in"): "doc",
    ("src/generate_clips.py", "# codecs that play poorly in Safari. Re-encode mp4/mov clips unless probe_codecs reports H.264+AAC."): "doc",
    ("src/generate_clips.py", "if fast_copy and output_ext in ('.mp4', '.mov'):"): "probe-based re-encode guard",
    ("src/generate_clips.py", "# Add faststart flag for web compatibility (only for mp4/mov)"): "doc",
    ("src/generate_clips.py", "if output_ext in ('.mp4', '.mov'):"): "faststart guard",
    ("src/generate_clips.py", "%(prog)s video.mp4 timestamps.json"): "usage",
    ("src/generate_clips.py", "%(prog)s video.mp4 timestamps.json --output-dir output/my_clips"): "usage",
    ("src/generate_clips.py", "%(prog)s video.mp4 timestamps.json --combined"): "usage",
    ("src/generate_clips.py", "%(prog)s video.mp4 timestamps.json --no-audio --combined"): "usage",
    ("src/generate_clips.py", "parser.add_argument('--format', default='mp4', help='Output video format (default: mp4)')"): "clip default",
    ("src/routes/timestamps.py", 'for ext in (".mp4", ".mkv", ".webm"):'): "infer video",
    ("src/routes/timestamps.py", "# Support all video formats (mp4, mkv, webm)"): "doc (x2)",
    ("src/routes/timestamps.py", 'if not filename.endswith((".mp4", ".mkv", ".webm")):'): "clip list",
    ("src/routes/timestamps.py", 'clip_count = len([f for f in os.listdir(run_path) if f.endswith((".mp4", ".mkv", ".webm"))])'): "run count",
    ("src/routes/videos.py", "Safari is not supported for full-game playback; clips are mp4 and work everywhere."): "R8 Safari note",
    ("src/routes/videos.py", 'if f.endswith((".mp4", ".mkv", ".webm")):'): "film list",
    ("src/services/storage.py", '".mp4": "video/mp4",'): "content-type map",
}
EXPECTED_COUNTS = {
    ("src/routes/timestamps.py", "# Support all video formats (mp4, mkv, webm)"): 2,
}


def _iter_mp4_lines(src_root, rel_prefix="src"):
    hits = []
    for root, _, files in os.walk(src_root):
        for f in files:
            if not f.endswith(".py"):
                continue
            p = os.path.join(root, f)
            rel = os.path.join(rel_prefix, os.path.relpath(p, src_root)).replace("\\", "/")
            for line in open(p, encoding="utf-8"):
                if re.search(r"mp4", line, re.I):
                    hits.append((rel, line.strip()))
    return hits


def _unreviewed_mp4_hits(src_root, rel_prefix="src"):
    return [
        (rel, stripped)
        for rel, stripped in _iter_mp4_lines(src_root, rel_prefix=rel_prefix)
        if (rel, stripped) not in ALLOWED_MP4
    ]


def test_no_unreviewed_hardcoded_mp4():
    hits = _unreviewed_mp4_hits(os.path.join(REPO_ROOT, "src"))
    assert not hits, "Unreviewed mp4 references (fix or add to ALLOWED_MP4 with a reason):\n" + "\n".join(
        f"{rel}: {stripped}" for rel, stripped in hits
    )


def test_allowed_mp4_entries_each_match_exactly_n():
    counts = {}
    for key in _iter_mp4_lines(os.path.join(REPO_ROOT, "src")):
        counts[key] = counts.get(key, 0) + 1
    for key in ALLOWED_MP4:
        expected = EXPECTED_COUNTS.get(key, 1)
        actual = counts.get(key, 0)
        assert actual == expected, f"{key}: expected {expected} occurrence(s), found {actual}"


def test_mp4_audit_catches_stray_line(tmp_path):
    """Self-test: unanchored allow-list entries used to miss these strays."""
    import shutil

    copied = tmp_path / "src"
    shutil.copytree(os.path.join(REPO_ROOT, "src"), copied)

    stray_replace = "out = p.replace('.mp4', '_x')  # faststart mp4"
    (copied / "generate_clips.py").write_text(
        (copied / "generate_clips.py").read_text(encoding="utf-8") + "\n" + stray_replace + "\n",
        encoding="utf-8",
    )
    hits = _unreviewed_mp4_hits(str(copied))
    assert hits == [("src/generate_clips.py", stray_replace)]

    stray_default = "parser_extra_default = dict(fmt='x', default='mp4')"
    (copied / "generate_clips.py").write_text(
        (copied / "generate_clips.py").read_text(encoding="utf-8") + stray_default + "\n",
        encoding="utf-8",
    )
    hits = _unreviewed_mp4_hits(str(copied))
    assert ("src/generate_clips.py", stray_default) in hits
    assert ("src/generate_clips.py", stray_replace) in hits

    stray_mime = "LEGACY_TYPE = 'video/mp4'"
    (copied / "services" / "storage.py").write_text(
        (copied / "services" / "storage.py").read_text(encoding="utf-8") + stray_mime + "\n",
        encoding="utf-8",
    )
    hits = _unreviewed_mp4_hits(str(copied))
    assert ("src/services/storage.py", stray_mime) in hits
    assert set(hits) == {
        ("src/generate_clips.py", stray_replace),
        ("src/generate_clips.py", stray_default),
        ("src/services/storage.py", stray_mime),
    }
