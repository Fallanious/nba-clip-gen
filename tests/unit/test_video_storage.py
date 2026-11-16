import pytest


@pytest.mark.parametrize('extension', ['mp4', 'mkv', 'webm'])
def test_upload_preserves_original_bytes_and_extension(monkeypatch, tmp_path, extension):
    import upload_game as upload
    src = tmp_path / f'76ERS at BULLS ｜ FULL GAME HIGHLIGHTS ｜ November 8, 2026.{extension}'
    src.write_bytes(b'original video and audio')
    monkeypatch.setattr(upload, 'resolve_film_file', lambda _: str(src))
    monkeypatch.setattr(upload, 'probe_video', lambda _: dict(height=1080, size=386_000_000, has_audio=True))
    uploaded = []
    monkeypatch.setattr(upload, 'upload_game', lambda path, *a, **kw: uploaded.append((path, src.read_bytes())))
    monkeypatch.setattr(upload, 'get_video_duration', lambda _: 1006.708)
    monkeypatch.setattr(upload, 'direct_database_url', lambda _: 'unused')
    saved = []
    monkeypatch.setattr(upload, 'save_uploaded_game', lambda **kw: saved.append(kw) or 'game')
    def no_conversion(*args, **kwargs):
        raise AssertionError('Upload must not invoke a media conversion')
    monkeypatch.setattr('subprocess.Popen', no_conversion)
    assert upload.prepare_and_upload(str(src)) == 0
    assert uploaded == [(str(src), b'original video and audio')]
    assert saved[0]['s3_key'].endswith('.' + extension)
    assert list(tmp_path.iterdir()) == [src]


def test_unreadable_video_is_not_uploaded(monkeypatch, tmp_path):
    import upload_game as upload
    src = tmp_path / '76ERS at BULLS ｜ FULL GAME HIGHLIGHTS ｜ November 8, 2026.mp4'
    monkeypatch.setattr(upload, 'resolve_film_file', lambda _: str(src))
    monkeypatch.setattr(upload, 'probe_video', lambda _: None)
    monkeypatch.setattr(upload, 'upload_game', lambda *a, **kw: pytest.fail('Invalid video uploaded'))
    assert upload.prepare_and_upload(str(src)) == 1
