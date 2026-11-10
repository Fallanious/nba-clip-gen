#!/usr/bin/env python3
"""
Play Timestamp Matcher
Matches video frames with play-by-play data to generate clip timestamps.
"""

import argparse
import os
import sys
import subprocess
import shutil
import base64
import hashlib
import json
import re
from datetime import datetime
import requests


def video_dimensions(video_path):
    """Return width and height of the first video stream."""
    cmd = [
        'ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height',
        '-of', 'csv=p=0:s=x',
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    width, height = result.stdout.strip().split('x')
    return int(width), int(height)


def crop_box_pixels(spec, width, height):
    """Turn x,y,w,h fractions into an even pixel crop ffmpeg can encode."""
    parts = [float(part) for part in spec.split(',')]
    if len(parts) != 4:
        raise ValueError('crop needs x,y,w,h')
    x, y, w, h = parts
    if min(x, y) < 0 or w <= 0 or h <= 0 or x + w > 1.02 or y + h > 1.02:
        raise ValueError(f'crop fractions out of range: {spec}')
    px = int(round(x * width))
    py = int(round(y * height))
    pw = int(round(w * width))
    ph = int(round(h * height))
    px = max(0, min(px, width - 2))
    py = max(0, min(py, height - 2))
    pw = max(2, min(pw, width - px))
    ph = max(2, min(ph, height - py))
    px -= px % 2
    py -= py % 2
    pw -= pw % 2
    ph -= ph % 2
    return px, py, pw, ph


def extract_frame_at_time(video_path, time_seconds, output_path, crop=None):
    """Extract a single frame at specific timestamp, optionally cropped."""
    cmd = [
        'ffmpeg',
        '-ss', str(time_seconds),
        '-i', video_path,
        '-vframes', '1',
    ]
    if crop:
        x, y, w, h = crop
        cmd.extend(['-vf', f'crop={w}:{h}:{x}:{y}'])
    cmd.extend(['-q:v', '2', output_path, '-y'])
    subprocess.run(cmd, check=True, stderr=subprocess.PIPE)


FRAME_READ_TIMEOUT_S = 90
# Keep the model loaded for a match so the system prompt prefix can be reused.
MODEL_KEEP_ALIVE = "30m"


def scoreboard_system_prompt(cropped):
    """Instructions that stay the same for every frame in a match."""
    where = (
        "The image is a crop of the scoreboard from an NBA broadcast."
        if cropped else
        "The image is a frame from an NBA basketball broadcast. The scoreboard overlay is at the bottom of the screen."
    )
    return f"""{where}
Read the quarter and the time remaining.

Respond with ONLY a JSON object in this exact format:
{{
  "quarter": <number 1-4>,
  "time": "<minutes>:<seconds>"
}}

Rules:
- Only include fields you can clearly read in this image
- If the scoreboard is not visible or you cannot read it, respond with: {{}}
- Do not make up values
- Do not include any text outside the JSON object
- Time format is minutes:seconds
- Do not include the score"""


# glm-ocr transcribes the crop. The period is a small "1st"/"2nd" label,
# so we read that glyph ourselves instead of asking the model to name it.
GLM_OCR_PROMPT = "Text Recognition:"


def _is_glm_ocr(model):
    return str(model or "").startswith("glm-ocr")


def analyze_frame_scoreboard(image_path, model='glm-ocr:latest', cropped=False):
    """Send one frame to Ollama."""
    with open(image_path, 'rb') as f:
        image_data = base64.b64encode(f.read()).decode('utf-8')

    payload = {
        "model": model,
        "prompt": GLM_OCR_PROMPT if _is_glm_ocr(model) else "Read this frame and gather the scoreboard information.",
        "images": [image_data],
        "stream": False,
        "keep_alive": MODEL_KEEP_ALIVE,
    }
    if not _is_glm_ocr(model):
        payload["system"] = scoreboard_system_prompt(cropped)
        payload["think"] = False

    for attempt in (1, 2):
        try:
            response = requests.post(
                'http://localhost:11434/api/generate',
                json=payload,
                timeout=FRAME_READ_TIMEOUT_S
            )
            if response.status_code == 200:
                text = response.json().get('response', '')
                if not text:
                    print("  Ollama returned an empty response")
                return text
            print(f"  Ollama returned {response.status_code}: {response.text[:300]}")
            return None
        except requests.Timeout:
            if attempt == 1:
                print(f"  Frame read timed out after {FRAME_READ_TIMEOUT_S}s. Trying once more.")
                continue
            print(f"  Error analyzing frame: read timed out after {FRAME_READ_TIMEOUT_S}s")
            return None
        except Exception as e:
            print(f"  Error analyzing frame: {e}")
            return None


_PERIOD_WORDS = {
    "1ST": 1, "IST": 1, "LST": 1,
    "2ND": 2, "2NO": 2,
    "3RD": 3, "3RO": 3,
    "4TH": 4,
    "OT": 5, "OVERTIME": 5,
}


def normalize_quarter(value):
    """Turn 1, 'Q2', or '2nd' into 1-4. A score like 28 is not a quarter."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if 1 <= value <= 4 else None
    text = str(value).strip()
    if not text:
        return None
    compact = re.sub(r"[^A-Z0-9]", "", text.upper())
    if compact in _PERIOD_WORDS:
        return _PERIOD_WORDS[compact]
    found = re.fullmatch(r"Q?([1-4])", compact)
    if found:
        return int(found.group(1))
    return None


def normalize_clock(value):
    """Turn a game clock into M:SS.0. Rejects a bare shot-clock number."""
    if value is None:
        return None
    text = str(value).strip().replace("：", ":")
    found = re.search(r"(\d{1,2})\s*:\s*(\d{2})", text)
    if not found:
        return None
    minutes = int(found.group(1))
    seconds = int(found.group(2))
    if seconds > 59 or minutes > 12:
        return None
    return f"{minutes}:{seconds:02d}.0"


def _quarter_in_text(text):
    """Find the period label. A bare digit is a score, so it does not count."""
    pattern = re.compile(
        r"\b(?:Q\s*([1-4])|([1-4])\s*(?:st|nd|rd|th)|IST|LST|2NO|3RO|OT)\b",
        re.IGNORECASE,
    )
    found = pattern.search(text)
    if not found:
        return None
    if found.group(1):
        return int(found.group(1))
    if found.group(2):
        return int(found.group(2))
    word = found.group(0).upper()
    if word == "OT":
        return 5
    return _PERIOD_WORDS[word]


def _clock_in_text(text):
    """Prefer the game clock when a shot clock is in the same transcription."""
    found = []
    for match in re.finditer(r"(\d{1,2})\s*[:：]\s*(\d{2})", text):
        clock = normalize_clock(f"{match.group(1)}:{match.group(2)}")
        if clock:
            found.append(clock)
    if not found:
        return None
    return max(found, key=time_to_seconds)


def _json_object(response_text):
    start = response_text.find("{")
    if start == -1:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(response_text[start:])
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict):
        return data
    return None


def _scoreboard(quarter, time):
    quarter = normalize_quarter(quarter)
    clock = normalize_clock(time)
    if quarter is None or clock is None:
        return None
    return {"quarter": quarter, "time": clock}


def parse_scoreboard_response(response_text):
    """Read quarter and game clock from JSON or from plain OCR text."""
    if not response_text or not str(response_text).strip():
        return None

    clock = _clock_in_text(response_text)
    quarter = _quarter_in_text(response_text)
    data = _json_object(response_text)
    if data is not None:
        if clock is None:
            clock = normalize_clock(data.get("time"))
        if quarter is None:
            quarter = normalize_quarter(data.get("quarter"))
        if clock is None and quarter is None and data.get("quarter") in (None, "") and data.get("time") in (None, ""):
            print("  Info: no scoreboard read in this frame")
            return None

    parsed = _scoreboard(quarter, clock)
    if parsed:
        return parsed
    print("  Warning: No quarter and time found in response")
    print(f"  Response was: {response_text[:200]}")
    return None


GROUP_WINDOW_S = 5


def time_to_seconds(time_str):
    """Convert MM:SS.S format to seconds."""
    try:
        parts = time_str.replace('.0', '').split(':')
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        return 0
    except:
        return 0


def find_next_matching_play(frame_data, plays, next_play_idx, player_name=None):
    """
    Find if the frame matches the next expected play in chronological order.
    Iterate through the list of plays until we find a match. Next time this is called we should start from the following play
    
    Args:
        frame_data: Scoreboard data from frame
        plays: List of all plays
        next_play_idx: Index of the next play we're looking for (or None for first match)
        player_name: Optional player last name to filter plays (case-insensitive, complete word match)
    
    Returns:
        (index, play) if match found, else (None, None)
    """
    if not frame_data or not frame_data.get('quarter'):
        return None, None
    
    frame_quarter = int(frame_data['quarter'])
    frame_time_sec = time_to_seconds(frame_data.get('time', '0:0.0'))

    for i in range(next_play_idx,len(plays)):

      play = plays[i]

      play_time_sec = time_to_seconds(play['time'])
      play_quarter = int(play['quarter'])
      
      if play_quarter == frame_quarter and play_time_sec == frame_time_sec:
        if player_name and not re.search(rf'\b{re.escape(player_name)}\b', play['description'], re.IGNORECASE):
          continue
        return i, play

      # Play is chronologically past the frame - no point continuing
      # (Later quarter, or same quarter with lower clock = later in game)
      if play_quarter > frame_quarter or (play_quarter == frame_quarter and play_time_sec < frame_time_sec):
        break

    return None, None

def extract_action_type(description):
    """Extract the primary action type from a play description."""
    desc = description.lower()
    if 'dunk' in desc:
        return 'dunk'
    if 'layup' in desc or 'finger roll' in desc:
        return 'layup'
    if '3-pt' in desc or '3pt' in desc or 'three point' in desc:
        return 'three_pointer'
    if 'free throw' in desc:
        return 'free_throw'
    if 'jump shot' in desc or 'jumper' in desc:
        return 'midrange'
    if 'block' in desc:
        return 'block'
    if 'steal' in desc:
        return 'steal'
    if 'rebound' in desc:
        return 'rebound'
    if 'misses' in desc:
        return 'miss'
    if 'turnover' in desc:
        return 'turnover'
    return 'other'


# Map sequences of actions to compound labels
COMPOUND_LABELS = {
    ('miss', 'rebound', 'dunk'): 'putback_dunk',
    ('miss', 'rebound', 'layup'): 'putback_layup',
    ('miss', 'rebound', 'midrange'): 'putback_jumper',
    ('steal', 'dunk'): 'fastbreak_dunk',
    ('steal', 'layup'): 'fastbreak_layup',
    ('block', 'rebound'): 'block_and_recover',
}


def get_compound_label(descriptions):
    """Try to match a compound play pattern from multiple descriptions."""
    actions = tuple(extract_action_type(d) for d in descriptions)
    
    # Only exact matches - if no match, return None and fall back to primary action
    return COMPOUND_LABELS.get(actions, None)


def pick_primary_action(plays):
    """Pick the most important play from a group for the primary label."""
    # Priority: made shots > blocks/steals > missed shots > rebounds
    priority = {
        'dunk': 10,
        'layup': 9,
        'three_pointer': 8,
        'midrange': 7,
        'block': 6,
        'steal': 5,
        'rebound': 3,
        'miss': 1,
        'other': 0,
    }
    
    best_play = plays[0]
    best_score = priority.get(extract_action_type(plays[0]['description']), 0)
    
    for play in plays[1:]:
        score = priority.get(extract_action_type(play['description']), 0)
        if score > best_score:
            best_score = score
            best_play = play
    
    return best_play


def load_pbp_source(path):
    """Read, hash, and parse the same PBP bytes. Callers must not reread the file."""
    with open(path, "rb") as handle:
        data = handle.read()
    digest = hashlib.sha256(data).hexdigest()
    parsed = json.loads(data.decode("utf-8"))
    return digest, stamp_original_play_indexes(parsed)


def stamp_original_play_indexes(plays):
    """Stamp 0..n-1 on the full saved PBP list before grouping or filtering.

    This index is unique for this saved file only, not a Basketball-Reference
    event id and not stable across a revised scrape.
    """
    if not isinstance(plays, list):
        raise ValueError("PBP plays must be a list")
    stamped = []
    for index, play in enumerate(plays):
        if not isinstance(play, dict):
            raise ValueError("PBP play must be an object")
        row = dict(play)
        existing = row.get("play_index")
        if existing is not None and existing != index:
            raise ValueError("play_index does not match full-game input order")
        row["play_index"] = index
        stamped.append(row)
    return stamped


def original_member_record(play):
    """Event fields for one original PBP row. No composite group description."""
    index = play.get("play_index")
    if type(index) is not int:
        raise ValueError("original play_index must be a full-game int")
    quarter = play.get("quarter")
    time_str = play.get("time")
    team = play.get("team")
    description = play.get("description")
    if quarter is None or not time_str or not isinstance(description, str) or not description:
        raise ValueError("original PBP member is missing quarter, time, or description")
    return {
        "play_index": index,
        "quarter": quarter,
        "time": time_str,
        "team": team,
        "description": description,
        "score": play.get("score"),
    }


def original_members(play):
    """Original full-game members of a condensed group, in input order."""
    raw = play.get("original_plays")
    if isinstance(raw, list) and raw:
        return [original_member_record(item) for item in raw]
    return [original_member_record(play)]


def merge_play_group(plays):
    """Merge a group of plays into one combined play for matching only.

    original_plays keeps each event's own index/period/clock/description.
    Grouping does not invent per-player indexes. Persist expands members
    into separate clips rows that may share this group's video window.
    """
    members = [original_member_record(play) for play in plays]
    if len(plays) == 1:
        play = dict(plays[0])
        play["is_compound"] = False
        play["play_count"] = 1
        play["primary_action"] = extract_action_type(play["description"])
        play["original_plays"] = members
        play["original_play_indexes"] = [members[0]["play_index"]]
        return play

    descriptions = [p["description"] for p in plays]
    combined_desc = " → ".join(descriptions)
    compound_label = get_compound_label(descriptions)
    primary = pick_primary_action(plays)
    primary_action = compound_label if compound_label else extract_action_type(primary["description"])
    return {
        "quarter": plays[0]["quarter"],
        "time": plays[0]["time"],
        "team": primary["team"],
        "score": plays[-1]["score"],
        "description": combined_desc,
        "is_compound": True,
        "play_count": len(plays),
        "primary_action": primary_action,
        "original_plays": members,
        "original_play_indexes": [item["play_index"] for item in members],
    }


def cluster_play_groups(plays, time_window=GROUP_WINDOW_S):
    """Partition consecutive plays by same quarter and window vs group start.

    Game clock counts down, so prev_time - play_time is elapsed time in the
    cluster. Matcher condensation and persist grouping identity both use this.
    """
    if not plays:
        return []
    groups = []
    current_group = [plays[0]]
    for play in plays[1:]:
        prev = current_group[0]
        same_quarter = play["quarter"] == prev["quarter"]
        time_diff = time_to_seconds(prev["time"]) - time_to_seconds(play["time"])
        if same_quarter and 0 <= time_diff <= time_window:
            current_group.append(play)
        else:
            groups.append(current_group)
            current_group = [play]
    groups.append(current_group)
    return groups


def condense_plays(plays, time_window=GROUP_WINDOW_S):
    """
    Merge plays that happen within time_window seconds of each other.
    Creates compound descriptions and assigns compound labels.
    """
    if not plays:
        return plays
    return [merge_play_group(group) for group in cluster_play_groups(plays, time_window)]




def main():
    """Main function."""
    parser = argparse.ArgumentParser(
        description='Match video frames with play-by-play data to generate clip timestamps',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument('video', help='Video file path')
    parser.add_argument('playbyplay', help='Play-by-play JSON file path')
    parser.add_argument('--player-name', type=str, default=None, help='Last Name of the player')
    parser.add_argument('--buffer', type=int, default=5, help='Buffer seconds before/after play (default: 5)')
    parser.add_argument('--max-plays', type=int, default=5, help='Maximum number of plays to match (default: 5)')
    parser.add_argument('--sample-interval', type=int, default=10, help='Seconds between frame samples (default: 10)')
    parser.add_argument('--start-time', type=int, default=0, help='Start sampling from this time in seconds (default: 0, useful to skip pre-game footage)')
    parser.add_argument('--end-time', type=int, default=None, help='Stop sampling at this time in seconds (default: end of video)')
    parser.add_argument('--worker-id', type=int, default=None, help='Worker ID for parallel processing (used in output filename)')
    parser.add_argument('--model', default='glm-ocr:latest', help='Ollama vision model (default: qwen3-vl:4b)')
    parser.add_argument('--crop', default=None, help='Scoreboard crop as x,y,w,h fractions of the frame')
    parser.add_argument('--output', '-o', help='Output JSON file path')
    
    args = parser.parse_args()
    
    # Validate inputs
    if not os.path.exists(args.video):
        print(f"Error: Video file not found: {args.video}")
        sys.exit(1)
    
    if not os.path.exists(args.playbyplay):
        print(f"Error: Play-by-play file not found: {args.playbyplay}")
        sys.exit(1)

    # Catch a bad database target before the slow vision pass.
    # Authoritative assert_persist_ready still runs immediately before connect.
    from persist_matcher_clips import PersistRefused, assert_persist_ready

    try:
        assert_persist_ready(os.environ.get("DATABASE_URL", ""))
    except PersistRefused as exc:
        print(str(exc))
        sys.exit(1)
    
    # Check Ollama
    try:
        response = requests.get('http://localhost:11434/api/tags', timeout=5)
        if response.status_code != 200:
            print("Error: Ollama is not running")
            sys.exit(1)
    except:
        print("Error: Cannot connect to Ollama")
        sys.exit(1)
    
    # Load play-by-play data from one byte read: hash and parse those same bytes.
    pbp_hash, frozen_plays = load_pbp_source(args.playbyplay)

    print(f"Loaded {len(frozen_plays)} plays from play-by-play data")
    print("Frozen PBP source hashed at load; persist will not reread the file")

    # Stamp already happened in load_pbp_source. Group, then optional name filter.
    # Filter selects groups; it must not restamp or shrink membership.
    plays = condense_plays(frozen_plays, time_window=GROUP_WINDOW_S)
    compound_count = sum(1 for p in plays if p.get('is_compound'))
    print(f"Condensed to {len(plays)} distinct plays ({compound_count} compound plays)")
    print(f"Will match first {args.max_plays} plays")
    print(f"Vision model: {args.model}")
    if args.player_name:
        print(f"Filtering groups whose combined description includes: {args.player_name}")
        print(
            "Player filter selects entire condensed groups. Original membership "
            "is unchanged; unnamed events in a selected group stay in the group "
            "and persist as their own clips rows if the group matches. "
            "Player links are written for every stored clip, including events "
            "that do not contain the filter name."
        )
        plays = [
            play for play in plays
            if play['description'].find(args.player_name) != -1
        ]
        named = 0
        unnamed = 0
        for play in plays:
            for member in original_members(play):
                if args.player_name in member["description"]:
                    named += 1
                else:
                    unnamed += 1
        print(
            f"Selected {len(plays)} groups with {named + unnamed} original events "
            f"({unnamed} original events do not name the player and still persist if matched)."
        )
    
    # Get video duration
    cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
           '-of', 'default=noprint_wrappers=1:nokey=1', args.video]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    video_duration = float(result.stdout.strip())
    
    print(f"Video duration: {video_duration:.2f}s")
    print(f"Starting from: {args.start_time}s")
    print(f"Sampling every {args.sample_interval}s")
    crop_px = None
    if args.crop:
        frame_w, frame_h = video_dimensions(args.video)
        try:
            crop_px = crop_box_pixels(args.crop, frame_w, frame_h)
        except ValueError as exc:
            print(f"Error: {exc}")
            sys.exit(1)
        print(f"Scoreboard crop: {crop_px[0]},{crop_px[1]} {crop_px[2]}x{crop_px[3]}")
    print("="*70)
    
    # Create debug directory for frames (with timestamp)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    debug_dir = f'output/debug_frames/{timestamp}'
    os.makedirs(debug_dir, exist_ok=True)
    print(f"Debug frames will be saved to: {debug_dir}")
    print("="*70)
    
    matched_plays = []
    next_play_idx = 0  # Track which play we're looking for next (None = find first)
    
    # Determine end time for scanning
    end_time = args.end_time if args.end_time is not None else video_duration
    end_time = min(end_time, video_duration)
    
    if args.worker_id is not None:
        print(f"Worker {args.worker_id}: Processing video from {args.start_time}s to {end_time}s")
    
    # Sample frames at intervals (starting from start_time to skip pre-game)
    current_time = args.start_time
    while current_time < end_time and len(matched_plays) < args.max_plays:
        print(f"\nSampling frame at {current_time}s...")
        
        if next_play_idx == 0:
            print(f"  Looking for FIRST match (highlight video - could be any play)")
        else:
            print(f"  Looking for play #{next_play_idx + 1} (matched {len(matched_plays)}/{args.max_plays})")
        
        frame_path = os.path.join(debug_dir, f'frame_{current_time}.jpg')
        extract_frame_at_time(args.video, current_time, frame_path, crop_px)
        
        # Analyze frame
        print(f"  Analyzing scoreboard...")
        response = analyze_frame_scoreboard(frame_path, model=args.model, cropped=crop_px is not None)
        
        if response:
            print(f"  LLM Response: {response}")
            frame_data = parse_scoreboard_response(response)
            
            if frame_data and frame_data.get('quarter'):
                print(f"  Quarter: {frame_data['quarter']}, Time: {frame_data.get('time', 'N/A')}")
                
                match_idx, matched_play = find_next_matching_play(frame_data, plays, next_play_idx, player_name=args.player_name)
                
                if matched_play:
                    print(f"  ✓ MATCH FOUND (Play #{match_idx + 1}): {matched_play['description']}")
                    
                    # because the play has to have already happened by the time the play by play data matches we use the match time as the end of hte clip
                    # and add the buffer to the beginning
                    clip_start = max(0, current_time - args.buffer)
                    clip_end = min(video_duration, current_time + 1)
                    
                    members = original_members(matched_play)
                    matched_plays.append({
                        'play_index': members[0]['play_index'],
                        'original_play_indexes': [item['play_index'] for item in members],
                        'original_plays': members,
                        'team': matched_play['team'],
                        'description': matched_play['description'],
                        'quarter': matched_play['quarter'],
                        'game_time': matched_play['time'],
                        'score': matched_play['score'],
                        'video_timestamp': current_time,
                        'video_start': clip_start,
                        'video_end': clip_end,
                        'primary_action': matched_play.get('primary_action', extract_action_type(matched_play['description'])),
                        'is_compound': matched_play.get('is_compound', False),
                        'play_count': len(members),
                    })
                    
                    # Move to next play for sequential matching
                    next_play_idx = match_idx + 1
                    print(f"  Clip: {clip_start:.1f}s - {clip_end:.1f}s")
                    print(f"  Progress: {len(matched_plays)}/{args.max_plays} plays matched")
                else:
                  print(f"  No match with next expected play")
            else:
                print(f"  Could not extract complete scoreboard data")
        else:
            print(f"  Failed to analyze frame")
        
        current_time += args.sample_interval
    
    print("\n" + "="*70)
    print(f"MATCHING COMPLETE: Found {len(matched_plays)} plays")
    print("="*70)
    
    # Determine output path
    if args.output:
        output_path = args.output
    else:
        video_name = os.path.splitext(os.path.basename(args.video))[0]
        worker_suffix = f"_worker{args.worker_id}" if args.worker_id is not None else ""
        output_path = f'output/cliptimestamps/{video_name}_timestamps_{timestamp}{worker_suffix}.json'
    
    # Save results
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(matched_plays, f, indent=2)
    
    print(f"\n✓ Timestamps saved to: {output_path}")
    print(f"✓ Debug frames saved to: {debug_dir}")

    print("\nMATCHED PLAYS:")
    for i, play in enumerate(matched_plays, 1):
        compound_marker = " [COMPOUND]" if play.get('is_compound') else ""
        print(f"{i}. Q{play['quarter']} {play['game_time']} | {play['team']}{compound_marker}")
        print(f"   Action: {play['primary_action']}")
        print(f"   {play['description']}")
        print(f"   Video: {play['video_start']:.1f}s - {play['video_end']:.1f}s")

    from persist_matcher_clips import persist_matched_output

    database_url = os.environ.get("DATABASE_URL", "")
    try:
        assert_persist_ready(database_url)
        print(
            "Persisting one clips row per original member of each matched group "
            "(shared group video window). Each clip then gets its player links. "
            "A name that is not exactly one player on the two teams is skipped."
        )
        game_bbref_id = os.environ.get("MATCHER_GAME_BBREF_ID") or os.path.splitext(
            os.path.basename(args.playbyplay)
        )[0]
        report = persist_matched_output(
            matched_plays,
            database_url=database_url,
            game_bbref_id=game_bbref_id,
            pbp_hash=pbp_hash,
            original_plays=frozen_plays,
        )
        print(
            f"✓ Clips stored: {report['inserted']} inserted, "
            f"{report['noop']} already stored, "
            f"{report['players_linked']} player links"
        )
        if report["players_skipped"]:
            print(
                f"  {report['players_skipped']} names did not match one player "
                "on the two teams"
            )
    except PersistRefused as exc:
        print(str(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()

