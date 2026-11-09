#!/usr/bin/env python3
"""Prepare a local game and upload it to the private S3 bucket."""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from persist_matcher_clips import PersistRefused, direct_database_url, save_uploaded_game
from routes.videos import TEAM_CODES, game_output_name
from services.storage import s3_key_for_game, upload_game
from utils.video import (
    get_video_duration,
    is_upload_ready,
    probe_video,
    resolve_film_file,
    transcode_to_mkv,
)


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


def _converted_path(path, parsed_name):
    """Put the 720p file beside the source, under the same game title."""
    folder = os.path.dirname(path)
    final_path = os.path.join(folder, parsed_name)
    if os.path.realpath(final_path) == os.path.realpath(path):
        work_name, _parsed = game_output_name(os.path.basename(path), ".converting.mkv")
        return os.path.join(folder, work_name), final_path
    return final_path, final_path


def prepare_and_upload(video_arg):
    path = resolve_film_file(video_arg)
    try:
        mkv_name, parsed = game_output_name(os.path.basename(path), ".mkv")
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
    work_path = None
    named = None
    try:
        if is_upload_ready(info):
            print(f"Already 720p MKV ({info['height']}px). Uploading as-is.", flush=True)
            named = path
        else:
            height = info["height"] or "unknown"
            work_path, upload_path = _converted_path(path, mkv_name)
            print(f"Converting {height}px video to 720p MKV: {os.path.basename(upload_path)}", flush=True)
            mode = transcode_to_mkv(path, work_path, info["height"])
            kept_name, kept = game_output_name(os.path.basename(work_path), os.path.splitext(work_path)[1])
            if kept["bbref_id"] != bbref_id or kept_name != os.path.basename(work_path):
                raise ValueError("Converted filename lost the teams or the date.")
            if os.path.realpath(work_path) != os.path.realpath(upload_path):
                os.replace(work_path, upload_path)
                work_path = None
            named = upload_path
            print(f"Convert finished ({mode}). Game id {bbref_id}.", flush=True)

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
        if (
            work_path
            and os.path.exists(work_path)
            and os.path.realpath(work_path) != os.path.realpath(path)
            and (named is None or os.path.realpath(work_path) != os.path.realpath(named))
        ):
            os.remove(work_path)
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
