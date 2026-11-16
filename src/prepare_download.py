"""Size-targeted AV1 encoding performed only during downloads."""

import json
import math
import os
import subprocess

TARGET_BYTES = 180_000_000
SIZE_TOLERANCE = 1.02


def probe(path):
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', path],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def video_budget(info):
    duration = float(info['format']['duration'])
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('A valid duration is required for download encoding.')
    audio = [s for s in info['streams'] if s.get('codec_type') == 'audio']
    audio_rate = 0
    for stream in audio:
        rate = float(stream.get('bit_rate', 0))
        if not math.isfinite(rate) or rate <= 0:
            raise ValueError('Cannot determine audio bitrate for size-targeted encoding.')
        audio_rate += rate
    bitrate = int(TARGET_BYTES * 8 * 0.995 / duration - audio_rate)
    if bitrate <= 0:
        raise ValueError('Audio alone exceeds the download size budget.')
    return bitrate


def prepare_download(source, destination, run):
    """Keep small native AV1 files; encode larger ones before publishing atomically.

    Both paths are in a private download staging directory. The caller publishes
    only a successfully prepared file; an existing film is never partially replaced.
    """
    info = probe(source)
    video = next(s for s in info['streams'] if s.get('codec_type') == 'video')
    if video.get('codec_name') != 'av1':
        raise ValueError('Expected an AV1 download.')
    if os.path.getsize(source) <= TARGET_BYTES:
        print('AV1 download is already within 180 MB; keeping it unchanged.', flush=True)
        return source

    bitrate = video_budget(info)
    duration = float(info['format']['duration'])
    for attempt in range(2):
        print(f'Encoding AV1 download to approximately 180 MB ({bitrate / 1e6:.3f} Mbps video).', flush=True)
        run([
            'ffmpeg', '-nostdin', '-y', '-hide_banner', '-nostats',
            '-i', source, '-map', '0:v:0', '-map', '0:a?',
            '-c:v', 'libsvtav1', '-preset', '6', '-b:v', str(bitrate),
            '-svtav1-params', 'lp=4', '-pix_fmt', 'yuv420p', '-c:a', 'copy',
            '-movflags', '+faststart', '-stats_period', '30', '-progress', 'pipe:1',
            destination,
        ], timeout=7200)
        encoded = probe(destination)
        stream = next(s for s in encoded['streams'] if s.get('codec_type') == 'video')
        if (stream.get('codec_name') != 'av1'
                or (stream.get('width'), stream.get('height')) != (video.get('width'), video.get('height'))
                or stream.get('r_frame_rate') != video.get('r_frame_rate')
                or abs(float(encoded['format']['duration']) - duration) > 1):
            raise RuntimeError('Encoded download failed codec, resolution, frame rate, or duration validation.')
        size = os.path.getsize(destination)
        if 0 < size <= TARGET_BYTES * SIZE_TOLERANCE:
            print(f'AV1 download ready: {size / 1e6:.1f} MB.', flush=True)
            return destination
        bitrate = int(bitrate * TARGET_BYTES / max(size, 1) * 0.98)
    raise RuntimeError('Encoded download exceeds the 180 MB target tolerance.')
