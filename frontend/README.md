# NBA Clip Generator — Frontend

React + TypeScript UI for the NBA clip pipeline. Each section in the app maps to a CLI step in the root [README](../README.md).

## Prerequisites

- **Node.js 18+**
- **Python backend** running at http://localhost:5001 (`python3 src/app.py` from the project root)
- **Ollama** running locally (required for timestamp matching) — the status bar at the top shows whether it is online

### Ollama

```bash
brew install ollama           # or install from https://ollama.ai

ollama serve                  # skip on macOS if the Ollama app is already running
ollama pull llama3.2-vision   # first time only

ollama list                   # confirm the model is downloaded
```

Ollama listens on http://localhost:11434. See the root [README](../README.md#ollama) for more detail.

## Setup

```bash
cd frontend
npm install
```

## Running in development

Start Ollama, the Flask API, then the Vite dev server:

```bash
# Ollama (skip serve if already running)
ollama serve
ollama pull llama3.2-vision   # first time only

# From project root
python3 src/app.py

# In another terminal
cd frontend && npm run dev
```

Open http://localhost:5173. Vite proxies `/api` requests to the backend on port 5001.

## Production build

Build static assets into `frontend/dist/`. Flask serves them when you run `python3 src/app.py`:

```bash
cd frontend && npm run build
python3 ../src/app.py
```

Open http://localhost:5001.

## UI walkthrough

The app is a single page with five sections, run in order:

### 1. Download Video

Paste a YouTube URL and click **Download**. The job runs in the background; output streams into the section below. When it finishes, the video appears in dropdowns for later steps.

### 2. Scrape Play-by-Play

Select a downloaded video (or paste a Basketball Reference PBP URL directly). The scraper writes JSON/CSV to `playbyplay/`. Completed files show up in the play-by-play dropdown in step 3.

### 3. Match Timestamps

Pick a video and its matching play-by-play file. The UI can auto-suggest the PBP file based on the video filename.

Adjust matching options before starting:

- **Buffer** — seconds before/after each play (start with 3–5)
- **Sample interval** — seconds between scoreboard frame samples
- **Max plays** — cap for test runs
- **Start time** — skip intro/pre-game footage
- **Workers** — parallel matching jobs

Click **Match** and watch the job output. When done, a timestamp JSON file is written to `output/cliptimestamps/`.

### 4. Edit Labels

Select a timestamp file to load its matched plays. Each row shows the play description, quarter, score, and a primary action label.

Use this step to spot-check matches before generating clips:

- Click a play to preview that segment in the embedded video player
- Review label counts to see the action mix
- If timing looks off, go back to step 3 and adjust buffer or re-run matching

### 5. Generate Clips

Select the source video and a timestamp file, then click **Generate**. Clips are written to `output/clips/<timestamp>/` with a `clips_metadata.json` sidecar.

## Project layout

```
frontend/src/
├── api.ts              # All backend API calls
├── types.ts            # Shared TypeScript types
├── hooks/useJob.ts     # Polls /api/job/<id> for async task status
├── App.tsx             # Main page — wires sections together
└── components/         # One component per pipeline step
```

API calls live in `api.ts` only — components never call `fetch` directly.

## Scripts

| Command | Description |
|---------|-------------|
| `npm run dev` | Start Vite dev server on port 5173 |
| `npm run build` | Type-check and build to `dist/` |
| `npm run preview` | Preview the production build locally |
| `npm run lint` | Run ESLint |
