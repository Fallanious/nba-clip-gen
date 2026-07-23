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
import json
import re
from datetime import datetime
import requests


def extract_frame_at_time(video_path, time_seconds, output_path):
    """Extract a single frame at specific timestamp."""
    cmd = [
        'ffmpeg',
        '-ss', str(time_seconds),
        '-i', video_path,
        '-vframes', '1',
        '-q:v', '2',
        output_path,
        '-y'
    ]
    subprocess.run(cmd, check=True, stderr=subprocess.PIPE)


def analyze_frame_scoreboard(image_path, model='llama3.2-vision'):
    """Send frame to Ollama to read scoreboard information."""
    with open(image_path, 'rb') as f:
        image_data = base64.b64encode(f.read()).decode('utf-8')
    
    prompt = """This is a frame from an NBA basketball broadcast between two teams.

Look at the bottom of the screen for a scoreboard overlay. The scoreboard shows the score, quarter, and time remaining.

Respond with ONLY a JSON object in this exact format:
{
  "quarter": <number 1-4>,
  "score": "<team1_score>-<team2_score>",
  "time": "<minutes>:<seconds>"
}

CRITICAL RULES:
- Only include fields you can CLEARLY READ from the actual scoreboard in THIS specific frame
- If the scoreboard is not visible or you cannot read it with certainty, respond with: {}
- DO NOT make up values or use placeholder data
- DO NOT include any text outside the JSON object
- The score format should be two numbers separated by a dash (Celtics score first, 76ers second)
- The time format should be minutes:seconds

If you cannot see a scoreboard clearly in this frame, respond with an empty JSON object: {}"""
    
    payload = {
        "model": model,
        "prompt": prompt,
        "images": [image_data],
        "stream": False
    }
    
    try:
        response = requests.post(
            'http://localhost:11434/api/generate',
            json=payload,
            timeout=60
        )
        
        if response.status_code == 200:
            result = response.json()
            return result.get('response', '')
        else:
            return None
    except Exception as e:
        print(f"Error analyzing frame: {e}")
        return None


def parse_scoreboard_response(response_text):
    """Parse LLM JSON response to extract quarter, time, and score."""
    if not response_text:
        return None
    
    try:
        # Try to find JSON in the response
        # Sometimes the LLM adds extra text, so extract just the JSON part
        json_start = response_text.find('{')
        json_end = response_text.rfind('}') + 1
        
        if json_start == -1 or json_end == 0:
            print(f"  Warning: No JSON found in response")
            return None
        
        json_str = response_text[json_start:json_end]
        data = json.loads(json_str)
        
        # Check if empty JSON (no scoreboard visible)
        if not data or len(data) == 0:
            print(f"  Info: LLM indicates no scoreboard visible in this frame")
            return None
        
        # Normalize the time format to include .0
        if 'time' in data and data['time']:
            time_parts = data['time'].split(':')
            if len(time_parts) == 2:
                data['time'] = f"{time_parts[0]}:{time_parts[1]}.0"
        
        # Return data only if we have at least quarter
        if data.get('quarter'):
            return data
        else:
            print(f"  Warning: Incomplete data (missing quarter)")
            return None
            
    except json.JSONDecodeError as e:
        print(f"  Warning: Failed to parse JSON: {e}")
        print(f"  Response was: {response_text[:200]}")
        return None
    except Exception as e:
        print(f"  Warning: Error parsing response: {e}")
        return None


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


def merge_play_group(plays):
    """Merge a group of plays into one combined play."""
    if len(plays) == 1:
        play = plays[0].copy()
        play['is_compound'] = False
        play['play_count'] = 1
        play['primary_action'] = extract_action_type(play['description'])
        return play
    
    # Combine descriptions with arrow
    descriptions = [p['description'] for p in plays]
    combined_desc = " → ".join(descriptions)
    
    # Get compound label if available, otherwise use primary action
    compound_label = get_compound_label(descriptions)
    primary = pick_primary_action(plays)
    primary_action = compound_label if compound_label else extract_action_type(primary['description'])
    
    return {
        'quarter': plays[0]['quarter'],
        'time': plays[0]['time'],  # Use earliest time
        'team': primary['team'],
        'score': plays[-1]['score'],  # Use final score
        'description': combined_desc,
        'is_compound': True,
        'play_count': len(plays),
        'primary_action': primary_action,
        'original_plays': plays,  # Keep originals for reference
    }


def condense_plays(plays, time_window=5):
    """
    Merge plays that happen within time_window seconds of each other.
    Creates compound descriptions and assigns compound labels.
    """
    if not plays:
        return plays
    
    condensed = []
    current_group = [plays[0]]
    
    for play in plays[1:]:
        prev = current_group[0]  # Compare to first play in group
        
        # Check if same quarter and within time window
        same_quarter = play['quarter'] == prev['quarter']
        prev_time = time_to_seconds(prev['time'])
        play_time = time_to_seconds(play['time'])
        # Game clock counts DOWN, so prev_time > play_time means play is later
        time_diff = prev_time - play_time
        
        if same_quarter and 0 <= time_diff <= time_window:
            # Add to current group
            current_group.append(play)
        else:
            # Finalize current group and start new one
            condensed.append(merge_play_group(current_group))
            current_group = [play]
    
    # Don't forget the last group
    condensed.append(merge_play_group(current_group))
    
    return condensed




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
    parser.add_argument('--output', '-o', help='Output JSON file path')
    
    args = parser.parse_args()
    
    # Validate inputs
    if not os.path.exists(args.video):
        print(f"Error: Video file not found: {args.video}")
        sys.exit(1)
    
    if not os.path.exists(args.playbyplay):
        print(f"Error: Play-by-play file not found: {args.playbyplay}")
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
    
    # Load play-by-play data
    with open(args.playbyplay, 'r') as f:
        raw_plays = json.load(f)
    
    print(f"Loaded {len(raw_plays)} plays from play-by-play data")
    
    # Condense plays to prevent clips that overlap / cover the same action
    # Ex. player gets offensive rebound and putback layup = 1 clip, not 3
    plays = condense_plays(raw_plays, time_window=5)
    compound_count = sum(1 for p in plays if p.get('is_compound'))
    print(f"Condensed to {len(plays)} distinct plays ({compound_count} compound plays)")
    print(f"Will match first {args.max_plays} plays")
    if args.player_name:
      new_plays_by_player = []
      print(f"Filtering for plays involving: {args.player_name}")
      # shrink plays into a condensed version that only includes plays with the player
      for idx, play in enumerate(plays):
        play = plays[idx]
        if play['description'].find(args.player_name) != -1:
          new_plays_by_player.append(play)
      plays = new_plays_by_player
    
    # Get video duration
    cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
           '-of', 'default=noprint_wrappers=1:nokey=1', args.video]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    video_duration = float(result.stdout.strip())
    
    print(f"Video duration: {video_duration:.2f}s")
    print(f"Starting from: {args.start_time}s")
    print(f"Sampling every {args.sample_interval}s")
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
        extract_frame_at_time(args.video, current_time, frame_path)
        
        # Analyze frame
        print(f"  Analyzing scoreboard...")
        response = analyze_frame_scoreboard(frame_path)
        
        if response:
            print(f"  LLM Response: {response}")
            frame_data = parse_scoreboard_response(response)
            
            if frame_data and frame_data.get('quarter'):
                print(f"  Quarter: {frame_data['quarter']}, Time: {frame_data.get('time', 'N/A')}, Score: {frame_data.get('score', 'N/A')}")
                
                match_idx, matched_play = find_next_matching_play(frame_data, plays, next_play_idx, player_name=args.player_name)
                
                if matched_play:
                    print(f"  ✓ MATCH FOUND (Play #{match_idx + 1}): {matched_play['description']}")
                    
                    # because the play has to have already happened by the time the play by play data matches we use the match time as the end of hte clip
                    # and add the buffer to the beginning
                    clip_start = max(0, current_time - args.buffer)
                    clip_end = min(video_duration, current_time + 1)
                    
                    matched_plays.append({
                        'play_index': match_idx,
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
                        'play_count': matched_play.get('play_count', 1),
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
    
    # Print summary
    print("\nMATCHED PLAYS:")
    for i, play in enumerate(matched_plays, 1):
        compound_marker = " [COMPOUND]" if play.get('is_compound') else ""
        print(f"{i}. Q{play['quarter']} {play['game_time']} | {play['score']} | {play['team']}{compound_marker}")
        print(f"   Action: {play['primary_action']}")
        print(f"   {play['description']}")
        print(f"   Video: {play['video_start']:.1f}s - {play['video_end']:.1f}s")


if __name__ == "__main__":
    main()

