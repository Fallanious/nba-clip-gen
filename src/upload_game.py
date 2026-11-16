#!/usr/bin/env python3
"""Upload a local game unchanged to the private S3 bucket."""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from persist_matcher_clips import PersistRefused, direct_database_url, save_uploaded_game
from routes.videos import TEAM_CODES, game_output_name
from services.storage import s3_key_for_game, upload_game
from utils.video import get_video_duration, probe_video, resolve_film_file


def _progress(total_bytes):
    sent = 0
    total_mb = max(total_bytes // (1024 * 1024), 1)
    step = max(total_mb // 20, 8)
    next_at = 0

    def callback(chunk):
        nonlocal sent, next_at
        sent += chunk
        done = min(sent // (1024 * 1024), total_mb)
        if done >= next_at or sent >= total_bytes:
            print(f"Progress: {done}/{total_mb} MB uploaded", flush=True)
            next_at = done + step

    return callback


def prepare_and_upload(video_arg):
    path = resolve_film_file(video_arg)
    try:
        _, parsed = game_output_name(os.path.basename(path), os.path.splitext(path)[1])
    except ValueError as exc:
        print(f"Error: {exc}")
        return 1

    info = probe_video(path)
    if info is None:
        print("Error: ffprobe could not read this video.")
        return 1

    bbref_id = parsed["bbref_id"]
    away_code = TEAM_CODES.get(parsed["away_team"])
    if not away_code:
        print(f"Error: {parsed['away_team']} is not a known team.")
        return 1
    upload_path = path
    try:
        print("Uploading original file without conversion.", flush=True)
        size = os.path.getsize(upload_path)
        key = s3_key_for_game(bbref_id, upload_path)
        print(f"Uploading {key} ({size / (1024 * 1024):.0f} MB) in 64 MB parts.", flush=True)
        upload_game(upload_path, bbref_id, progress=_progress(size))
        print(f"✓ UPLOAD COMPLETE: {key}", flush=True)
        game_id = save_uploaded_game(
            database_url=direct_database_url(os.environ.get("DATABASE_URL", "")),
            bbref_id=bbref_id,
            game_date=parsed["date"],
            home_code=parsed["home_code"],
            away_code=away_code,
            s3_key=key,
            duration_s=get_video_duration(upload_path),
        )
        print(f"✓ Game stored: id {game_id}, s3_key {key}", flush=True)
        return 0
    except (ValueError, RuntimeError, PersistRefused) as exc:
        print(f"Error: {exc}")
        return 1


def main():
    parser = argparse.ArgumentParser(description="Upload a film-folder game to S3")
    parser.add_argument("video", help="Path under film/, for example film/game.mkv")
    args = parser.parse_args()
    try:
        code = prepare_and_upload(args.video)
    except ValueError as exc:
        print(f"Error: {exc}")
        code = 1
    return code


if __name__ == "__main__":
    sys.exit(main())
