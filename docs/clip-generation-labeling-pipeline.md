# Clip Generation and Labeling Pipeline

This guide is the focused workflow for creating clip datasets and labels from full-game video + play-by-play.

## What this pipeline produces

- Timestamped play matches in `output/cliptimestamps/`
- Per-play clips in `output/clips/<run_timestamp>/`
- Clip metadata in `clips_metadata.json` (used downstream for model targets)
- Optional label-organized symlinks in `output/clips_by_label/`

## Prerequisites

- Python environment with `requirements.txt` installed
- `ffmpeg` available in your shell
- Ollama running locally for scoreboard reading in timestamp matching
- Input folders:
  - full game video in `film/`
  - play-by-play JSON in `playbyplay/`

## 1) Download a source game video

```bash
python src/download_video.py "https://www.youtube.com/watch?v=VIDEO_ID"
```

Output: `film/<video_name>.mp4`

## 2) Scrape play-by-play JSON

```bash
python src/scrape_playbyplay.py "https://www.basketball-reference.com/boxscores/pbp/GAME_ID.html"
```

Output: `playbyplay/<game_id>.json` (and CSV)

## 3) Match video timestamps to plays

```bash
python src/match_play_timestamps.py \
  film/YOUR_VIDEO.mp4 \
  playbyplay/YOUR_GAME.json \
  --buffer 5 \
  --max-plays 25 \
  --start-time 10 \
  --sample-interval 10
```

Output: `output/cliptimestamps/<video>_timestamps_<timestamp>.json`

Notes:
- `--buffer` is the most important tuning knob for clip quality.
- Start with `--max-plays 5` to calibrate timing, then scale up.

## 4) Generate clips + metadata

```bash
python src/generate_clips.py \
  film/YOUR_VIDEO.mp4 \
  output/cliptimestamps/YOUR_TIMESTAMPS.json
```

Output directory:
- `output/clips/<timestamp>/clip_*.mp4`
- `output/clips/<timestamp>/clips_metadata.json`

Each entry in `clips_metadata.json` includes an `attributes` dict with `action_type`, `shot_subtype`, `outcome`, `shot_distance_bucket`, `assisted`, and `team`. These are the multi-head supervision targets consumed by VideoMAE training.

## 5) Backfill attributes on existing clip runs (optional)

If you have clip runs from before the multi-attribute schema landed, re-parse their descriptions in place:

```bash
python src/backfill_clip_attributes.py
```

This rewrites every `output/clips/*/clips_metadata.json` with an `attributes` dict per clip and prints distribution counts.

## Labeling flow and quality control

- Attributes are assigned from play description parsing in `src/utils/labels.py` (`extract_attributes`).
- The `attributes` dict on each clip record inside `clips_metadata.json` is the source-of-truth target used by downstream model training.
- If attributes are ambiguous (rare putbacks, fouls, compound plays), correct them in your metadata/review loop before training.

## Suggested quick validation loop

1. Run timestamp matching on a small sample (`--max-plays 5`).
2. Generate clips and watch them.
3. Adjust `--buffer`, `--sample-interval`, and `--start-time`.
4. Regenerate at larger scale once timing is stable.

## Common paths used by this pipeline

- `film/` - source game videos
- `playbyplay/` - scraped play-by-play JSON
- `output/cliptimestamps/` - matched play windows
- `output/clips/` - generated clips + `clips_metadata.json`
