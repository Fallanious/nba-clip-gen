import os
import pytest
from utils import video


def test_budget_tracks_duration_without_audio():
    assert video.storage_bitrate(1006.708) == 1976740
    assert video.storage_bitrate(2000) == video.storage_bitrate(1000) // 2


@pytest.mark.parametrize('duration', [None, '', 0, -1, float('nan'), float('inf')])
def test_invalid_duration_refused(duration):
    with pytest.raises(ValueError):
        video.storage_bitrate(duration)


def test_legacy_720p_with_audio_or_excess_size_is_not_ready():
    info = dict(format_name='matroska,webm', height=720, size=200_000_000, has_audio=False)
    assert video.is_upload_ready(info)
    for change in [dict(has_audio=True), dict(size=370_000_000), dict(height=1080), dict(size=0)]:
        assert not video.is_upload_ready(dict(info, **change))


def test_small_source_remuxes_video_only(monkeypatch, tmp_path):
    src = tmp_path / 'source.mp4'; src.write_bytes(b'original')
    calls = []
    monkeypatch.setattr(video, '_run_ffmpeg', lambda cmd: (calls.append(cmd) or (0, '')))
    assert video.transcode_to_mkv(str(src), str(tmp_path/'out.mkv'), 720) == 'remux'
    assert calls[0][calls[0].index('-map')+1] == '0:v:0'
    assert '-an' in calls[0] and 'copy' in calls[0]
    assert src.read_bytes() == b'original'


def test_two_pass_preserves_fps_and_cleans_stats(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(video, 'get_video_duration', lambda _: 1006.708)
    monkeypatch.setattr(video, '_run_ffmpeg', lambda cmd: (calls.append(cmd) or (0, '')))
    assert video.transcode_to_mkv('source.mp4', str(tmp_path/'out.mkv'), 1080) == 'transcode'
    assert len(calls) == 2
    for n, cmd in enumerate(calls, 1):
        assert '-r' not in cmd and '-an' in cmd and 'libx265' in cmd
        params = cmd[cmd.index('-x265-params')+1]
        assert f'pass={n}' in params
        assert not os.path.exists(os.path.dirname(params.split('stats=')[1]))
    assert calls[0][-1] == os.devnull
    assert calls[1][-1] == str(tmp_path/'out.mkv')


def test_first_pass_failure_stops_before_second(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(video, 'get_video_duration', lambda _: 1000)
    monkeypatch.setattr(video, '_run_ffmpeg', lambda cmd: (calls.append(cmd) or (1, 'encoder missing')))
    with pytest.raises(RuntimeError, match='pass 1 failed'):
        video.transcode_to_mkv('source.mp4', str(tmp_path/'out.mkv'), 1080)
    assert len(calls) == 1


def test_never_overwrites_source(tmp_path):
    src = str(tmp_path/'original.mkv')
    with pytest.raises(ValueError, match='separate'):
        video.transcode_to_mkv(src, src, 720)


@pytest.mark.parametrize('valid', [True, False])
def test_upload_validates_before_s3_and_preserves_original(monkeypatch, tmp_path, valid):
    import upload_game as upload
    src = tmp_path / '76ERS at BULLS ｜ FULL GAME HIGHLIGHTS ｜ November 8, 2026.mkv'
    src.write_bytes(b'original')
    original = dict(format_name='matroska', height=720, size=386_000_000, has_audio=True)
    encoded = dict(format_name='matroska', height=720, size=249_000_000 if valid else 300_000_000, has_audio=False)
    monkeypatch.setattr(upload, 'resolve_film_file', lambda _: str(src))
    monkeypatch.setattr(upload, 'probe_video', lambda p: original if p == str(src) else encoded)
    def encode(source, dest, height):
        from pathlib import Path
        Path(dest).write_bytes(b'encoded')
        return 'transcode'
    monkeypatch.setattr(upload, 'transcode_to_mkv', encode)
    uploaded = []
    monkeypatch.setattr(upload, 'upload_game', lambda path, *a, **kw: uploaded.append(path))
    monkeypatch.setattr(upload, 'get_video_duration', lambda _: 1006.708)
    monkeypatch.setattr(upload, 'direct_database_url', lambda _: 'unused')
    monkeypatch.setattr(upload, 'save_uploaded_game', lambda **kw: 'game')
    assert upload.prepare_and_upload(str(src)) == (0 if valid else 1)
    assert src.read_bytes() == b'original'
    assert len(uploaded) == int(valid)
    assert not list(tmp_path.glob('*.converting.mkv'))
    if valid:
        assert uploaded[0].endswith('.storage.mkv')
