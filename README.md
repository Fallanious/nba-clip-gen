# NBA Video Clip Generator

A Python-based toolkit for downloading NBA game footage from YouTube, analyzing it with AI vision models, and automatically generating highlight clips matched to play-by-play data.

## Overview

This project uses computer vision (via Ollama LLMs) to read game scoreboards from video frames and match them with official play-by-play data from Basketball Reference, enabling automatic generation of individual play clips from full game footage.

## Web UI

The project includes a React frontend in [`frontend/`](frontend/) that wraps the full clip pipeline in a browser UI. It talks to the Flask API in `src/app.py` and mirrors the CLI workflow: download video, scrape play-by-play, match timestamps, review labels, and generate clips.

**Development** (hot reload, API proxied to Flask on port 5001):

```bash
# Terminal 1 — Ollama (skip if already running)
ollama serve                  # only if the server is not up

# Terminal 2 — backend
python3 src/app.py

# Terminal 3 — frontend
cd frontend && npm install && npm run dev
```

Open http://localhost:5173

## Focused Workflow Docs

- [Clip Generation and Labeling Pipeline](docs/clip-generation-labeling-pipeline.md)

## Requirements

### System Dependencies
- **Python 3.7+**
- **ffmpeg** - Required for video processing
- **Ollama** - Local LLM server for vision analysis (see setup below)

### Ollama

Install from https://ollama.ai or via Homebrew:

```bash
brew install ollama
```

Start the server (on macOS the Ollama app usually runs this automatically; use this if the API is not reachable):

```bash
ollama serve
```

Pull the vision model used for scoreboard reading:

```bash
ollama pull llama3.2-vision   # recommended
# ollama pull llava           # faster, less accurate alternative
```

Verify Ollama is running and the model is available:

```bash
ollama list
curl http://localhost:11434/api/tags
```

The backend expects Ollama at `http://localhost:11434`. The Web UI status bar shows online/offline status.

### Python Packages
```bash
pip install -r requirements.txt
```

Required packages:
- `yt-dlp` - YouTube video downloading
- `requests` - HTTP requests for Ollama API
- `beautifulsoup4` - Web scraping
- `lxml` - HTML parsing

## Workflow

### Step 1: Download Video from YouTube

Download NBA game footage as MP4 (optimized for Mac QuickTime):

```bash
python3 download_video.py "https://www.youtube.com/watch?v=VIDEO_ID"
```

**Output:** `film/VIDEO_TITLE.mp4`

**Options:**
- Videos are automatically downloaded at 1080p or lower
- Format is optimized for QuickTime playback
- Requires `ffmpeg` for best quality merging

---

### Step 2: Scrape Play-by-Play Data

Download official play-by-play data from Basketball Reference:

```bash
python3 scrape_playbyplay.py "https://www.basketball-reference.com/boxscores/pbp/GAME_ID.html"
```

**Example:**
```bash
python3 scrape_playbyplay.py "https://www.basketball-reference.com/boxscores/pbp/202511110PHI.html"
```

**Output:**
- `output/playbyplay/GAME_ID_playbyplay.json` - Structured JSON data
- `output/playbyplay/GAME_ID_playbyplay.csv` - CSV format

**Data includes:**
- Quarter
- Game time
- Score
- Team
- Play description

---

### Step 3: Match Video Timestamps to Plays

Use LLM vision to read scoreboards from video frames and match them to play-by-play data:

```bash
python3 match_play_timestamps.py \
  film/YOUR_VIDEO.mp4 \
  playbyplay/GAME_ID.json \
  --buffer 5 \
  --max-plays 10 \
  --start-time 10 \
  --sample-interval 1
```

**Output:** `output/cliptimestamps/VIDEO_timestamps_TIMESTAMP.json`

**Important Options:**
- `--buffer N` - Seconds before/after play to include in clip (default: 5)
  - **⚠️ NOTE: You will likely need to adjust this buffer!** 
  - Test with a few clips first to find the right timing
  - Increase buffer to capture more context before/after the play
  - Decrease buffer for tighter, shorter clips
- `--max-plays N` - Limit number of plays to process (for testing)
- `--start-time N` - Skip first N seconds (useful for pre-game footage)
- `--sample-interval N` - Seconds between frame samples (default: 10)
- `--model NAME` - Ollama model to use (default: llama3.2-vision)

**How it works:**
1. Samples frames at regular intervals from the video
2. Uses Ollama vision model to read scoreboard (quarter, time, score)
3. Matches scoreboard data to play-by-play entries
4. Generates timestamp ranges with buffer for each play

**Debugging:**
- Raw LLM output is printed to console
- Processed frames are saved to `output/debug_frames/TIMESTAMP/`

---

### Step 4: Generate Video Clips

Extract individual play clips from the original full-quality video:

```bash
python3 generate_clips.py \
  film/YOUR_VIDEO.mp4 \
  output/cliptimestamps/VIDEO_timestamps_TIMESTAMP.json
```

**Output:** `output/clips/TIMESTAMP/`
- Individual MP4 clips named: `clip_INDEX_TEAM_QQUARTER_TIME.mp4`
- `clips_metadata.json` - Metadata for all generated clips

**Options:**
- `--output-dir PATH` - Custom output directory
- `--no-audio` - Generate clips without audio
- `--re-encode` - Re-encode for compatibility (slower)
- `--format FORMAT` - Output format (default: mp4)

**Features:**
- Uses fast codec copy (no re-encoding by default)
- Generates descriptive filenames based on play data
- Creates organized timestamped output directories
- Includes detailed metadata JSON

---

### Step 5: Train VideoMAE Multi-Head Model (Optional)

Fine-tune a pretrained VideoMAE backbone with one classification head per
attribute (`action_type`, `shot_subtype`, `outcome`, `shot_distance_bucket`,
`assisted`, `team`):

```bash
python3 src/train_videomae.py \
  --clips-glob "output/clips/*/clips_metadata.json" \
  --epochs 30 \
  --freeze-epochs 10 \
  --batch-size 4 \
  --out-dir output/videomae_model
```

**Output:**
- `output/videomae_model/videomae_multihead.pt` - best checkpoint
- `output/videomae_model/train_report.json` - per-head macro-F1, confusion
  matrices, epoch history, and the exact label/team vocabularies used

**Predict on new clips:**

```bash
python3 src/predict_videomae.py --clip path/to/clip.mp4
python3 src/predict_videomae.py --clips-dir output/clips/20260205_130857
python3 src/predict_videomae.py \
  --metadata output/clips/20260205_130857/clips_metadata.json \
  --write-back
```

The `--write-back` flag merges predictions into each clip record as
`predicted_attributes`, `predicted_scores`, and `predicted_top3`.

---

## Complete Example Workflow

```bash
# 1. Download video
python3 download_video.py "https://www.youtube.com/watch?v=EXAMPLE_ID"

# 2. Scrape play-by-play data
python3 scrape_playbyplay.py "https://www.basketball-reference.com/boxscores/pbp/202511110PHI.html"

# 3. Match timestamps (start with small test)
python3 match_play_timestamps.py \
  film/CELTICS_vs_76ERS.mp4 \
  playbyplay/202511110PHI.json \
  --buffer 5 \
  --max-plays 5 \
  --start-time 10

# 4. Review the clips and adjust buffer if needed, then generate more
python3 match_play_timestamps.py \
  film/CELTICS_vs_76ERS.mp4 \
  playbyplay/202511110PHI.json \
  --buffer 7 \
  --max-plays 20 \
  --start-time 10

# 5. Generate clips
python3 generate_clips.py \
  film/CELTICS_vs_76ERS.mp4 \
  output/cliptimestamps/CELTICS_vs_76ERS_timestamps_*.json
```

---

## Tips & Best Practices

### Buffer Timing ⚠️
- **Start with `--buffer 5`** and generate a few test clips
- Watch the clips to see if they capture:
  - Lead-up to the play (passes, dribbling)
  - The actual play (shot, dunk, etc.)
  - Follow-through (reaction, celebrations)
- **Adjust the buffer** based on results:
  - Too short? Clips feel abrupt → Increase to 7-10 seconds
  - Too long? Extra dead time → Decrease to 3-4 seconds
- Different play types may need different buffers (fast breaks vs. set plays)

### Model Selection
- **llama3.2-vision** (recommended) - Much better at reading scoreboard text
- **llava** - Faster but less accurate for text recognition

### Processing Speed
- Use `--max-plays 5` for initial testing
- Full game processing can take 10-30+ minutes depending on:
  - Number of plays
  - Sample interval
  - Model speed

### Highlight Videos
- Use `--start-time 10` to skip intro/pre-game footage
- Highlight reels may not match chronologically with play-by-play
- The matcher handles this by finding the first valid match

### Troubleshooting
- **LLM returns empty `{}`:** Scoreboard not visible, increase `--start-time`
- **No matches found:** Check that video teams match play-by-play teams
- **Clips cut off early:** Increase `--buffer`
- **Slow processing:** Reduce `--sample-interval` or use `llava` model

---

## Output Directory Structure

```
clip/
├── frontend/                      # React UI (see frontend/README.md)
│   └── dist/                      # Production build (served by Flask)
├── film/                          # Downloaded videos
│   └── GAME_NAME.mp4
├── playbyplay/                    # Scraped play-by-play data
│   ├── GAME_ID.json
│   └── GAME_ID.csv
├── output/
│   ├── cliptimestamps/           # Matched timestamps
│   │   └── VIDEO_timestamps_TIMESTAMP.json
│   ├── clips/                    # Generated video clips
│   │   └── TIMESTAMP/
│   │       ├── clip_0_TEAM_Q1_TIME.mp4
│   │       ├── clip_1_TEAM_Q2_TIME.mp4
│   │       └── clips_metadata.json
│   └── debug_frames/             # Debug frames from matching
│       └── TIMESTAMP/
└── scripts...
```

---

## License

This project is for educational and personal use only. Respect YouTube's Terms of Service and Basketball Reference's usage policies when downloading and scraping data.

