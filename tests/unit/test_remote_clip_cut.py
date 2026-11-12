"""A remote game is seeked, then only the window is read. The signed URL stays out of the log."""

import subprocess

from generate_clips import extract_clip


def test_remote_cut_seeks_before_reading(monkeypatch, tmp_path):
    calls = []

    def run(cmd, *args, **kwargs):
        calls.append([str(part) for part in cmd])
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr("generate_clips.subprocess.run", run)
    dest = tmp_path / "clip.mp4"
    assert extract_clip("https://example.test/game.mkv?X-Amz-Signature=secret", 22, 27, str(dest), quiet=True)
    ffmpeg = next(cmd for cmd in calls if cmd[0] == "ffmpeg")
    assert ffmpeg.index("-ss") < ffmpeg.index("-t") < ffmpeg.index("-i")
    assert "-reconnect" in ffmpeg
    assert "libx264" in ffmpeg
    assert ffmpeg[ffmpeg.index("-crf") + 1] == "18"


def test_remote_failure_does_not_print_the_signed_url(monkeypatch, tmp_path, capsys):
    def run(cmd, *args, **kwargs):
        if str(cmd[0]).endswith("ffprobe"):
            return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")
        raise subprocess.CalledProcessError(
            1, cmd, stderr=b"open https://example.test/game.mkv?X-Amz-Signature=secret"
        )

    monkeypatch.setattr("generate_clips.subprocess.run", run)
    assert extract_clip("https://example.test/game.mkv?X-Amz-Signature=secret", 22, 27, str(tmp_path / "c.mp4")) is False
    logged = capsys.readouterr().out + capsys.readouterr().err
    assert "secret" not in logged
    assert "X-Amz-Signature" not in logged
