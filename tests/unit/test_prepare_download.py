import copy
import subprocess
import pytest
import prepare_download as prep


def metadata(size=350_000_000):
    return {'format': {'duration': '1160.254694', 'size': str(size)}, 'streams': [
        {'codec_type': 'video', 'codec_name': 'av1', 'width': 1280, 'height': 720, 'r_frame_rate': '60000/1001'},
        {'codec_type': 'audio', 'codec_name': 'aac', 'bit_rate': '47999'},
    ]}


def test_budget_reserves_audio_and_container():
    assert 1_186_000 < prep.video_budget(metadata()) < 1_188_000
    info = metadata()
    info['format']['duration'] = 'nan'
    with pytest.raises(ValueError):
        prep.video_budget(info)


def test_small_av1_is_unchanged(tmp_path, monkeypatch):
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'original')
    monkeypatch.setattr(prep, 'probe', lambda _: metadata())
    assert prep.prepare_download(str(source), 'unused', lambda *a, **kw: pytest.fail('Unnecessary encode')) == str(source)
    assert source.read_bytes() == b'original'


@pytest.mark.parametrize('mode', ['success', 'oversize', 'failure', 'invalid'])
def test_encoding_validates_output_and_preserves_source(tmp_path, monkeypatch, mode):
    source, dest = tmp_path / 'source.mp4', tmp_path / 'encoded.mp4'
    source.write_bytes(b'original')
    monkeypatch.setattr(prep, 'TARGET_BYTES', 5)
    monkeypatch.setattr(prep, 'video_budget', lambda _: 1187000)
    calls = []
    def run(cmd, timeout):
        calls.append(cmd)
        if mode == 'failure':
            raise subprocess.CalledProcessError(1, cmd)
        dest.write_bytes(b'123456' if mode == 'oversize' else b'1234')
    def probe(path):
        info = copy.deepcopy(metadata())
        if path == str(dest) and mode == 'invalid':
            info['streams'][0]['r_frame_rate'] = '30/1'
        return info
    monkeypatch.setattr(prep, 'probe', probe)
    if mode == 'success':
        assert prep.prepare_download(str(source), str(dest), run) == str(dest)
    else:
        with pytest.raises((RuntimeError, subprocess.CalledProcessError)):
            prep.prepare_download(str(source), str(dest), run)
    assert source.read_bytes() == b'original'
    assert len(calls) == (2 if mode == 'oversize' else 1)
    assert calls[0][calls[0].index('-c:a') + 1] == 'copy'
    assert '-r' not in calls[0]
