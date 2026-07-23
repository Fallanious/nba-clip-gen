#!/usr/bin/env python3
"""
Video Clip Generator
Generates individual video clips from timestamp JSON files using ffmpeg.
"""

import argparse
import os
import sys
import subprocess
import json
import re
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.labels import extract_attributes, primary_action


def sanitize_filename(text):
    """Remove special characters from filename."""
    # Replace invalid filename characters
    text = re.sub(r'[<>:"/\\|?*]', '', text)
    # Replace spaces and colons with underscores
    text = text.replace(' ', '_').replace(':', '-')
    return text


def get_file_size(file_path):
    """Get file size in human-readable format."""
    size_bytes = os.path.getsize(file_path)
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"


def combine_clips(clip_paths, output_path):
    """
    Combine multiple video clips into one continuous video.
    
    Args:
        clip_paths: List of video file paths to combine
        output_path: Output path for combined video
    """
    # Create concat file for ffmpeg with absolute paths
    concat_file = output_path.replace('.mp4', '_concat.txt')
    
    with open(concat_file, 'w') as f:
        for clip_path in clip_paths:
            # Use absolute path and escape single quotes
            abs_path = os.path.abspath(clip_path)
            f.write(f"file '{abs_path}'\n")
    
    # Run ffmpeg concat
    cmd = [
        'ffmpeg',
        '-f', 'concat',
        '-safe', '0',
        '-i', concat_file,
        '-c', 'copy',
        output_path,
        '-y'
    ]
    
    try:
        subprocess.run(cmd, check=True, stderr=subprocess.PIPE, stdout=subprocess.PIPE)
        os.remove(concat_file)  # Clean up concat file
        return True
    except subprocess.CalledProcessError as e:
        print(f"    Error combining clips: {e}")
        if e.stderr:
            print(f"    FFmpeg error: {e.stderr.decode('utf-8')[:500]}")
        return False


def extract_clip(video_path, start_time, end_time, output_path, keep_audio=True, fast_copy=True):
    """
    Extract a clip from video using ffmpeg.
    
    Args:
        video_path: Source video file
        start_time: Start time in seconds
        end_time: End time in seconds
        output_path: Output clip path
        keep_audio: Include audio in clip
        fast_copy: Use codec copy for faster extraction (no re-encoding)
    """
    duration = end_time - start_time
    
    # Build ffmpeg command
    # Use -ss before -i for faster seeking
    cmd = [
        'ffmpeg',
        '-ss', str(start_time),
        '-t', str(duration),
        '-i', video_path,
    ]
    
    if fast_copy:
        # Fast: copy codecs without re-encoding
        cmd.extend(['-c', 'copy'])
    else:
        # Slower: re-encode for compatibility
        cmd.extend(['-c:v', 'libx264', '-c:a', 'aac'])
    
    if not keep_audio:
        cmd.extend(['-an'])  # Remove audio
    
    # Add faststart flag for web compatibility
    cmd.extend(['-movflags', '+faststart'])
    cmd.extend([output_path, '-y'])
    
    # Run ffmpeg
    try:
        subprocess.run(cmd, check=True, stderr=subprocess.PIPE, stdout=subprocess.PIPE)
        return True
    except subprocess.CalledProcessError as e:
        print(f"    Error: {e}")
        if e.stderr:
            print(f"    FFmpeg error: {e.stderr.decode('utf-8')[:200]}")
        return False


def main():
    """Main function."""
    parser = argparse.ArgumentParser(
        description='Generate video clips from timestamp JSON files',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s video.mp4 timestamps.json
  %(prog)s video.mp4 timestamps.json --output-dir output/my_clips
  %(prog)s video.mp4 timestamps.json --combined
  %(prog)s video.mp4 timestamps.json --no-audio --combined
        """
    )

    
    parser.add_argument('video', help='Source video file path')
    parser.add_argument('timestamps', help='Timestamps JSON file path')
    parser.add_argument('--output-dir', default='output/clips', help='Output directory (default: output/clips)')
    parser.add_argument('--format', default='mp4', help='Output video format (default: mp4)')
    parser.add_argument('--no-audio', action='store_true', help='Exclude audio from clips')
    parser.add_argument('--re-encode', action='store_true', help='Re-encode clips (slower but more compatible)')
    parser.add_argument('--combined', action='store_true', help='Create a combined video from all clips')
    args = parser.parse_args()
    
    # Validate inputs
    if not os.path.exists(args.video):
        print(f"Error: Video file not found: {args.video}")
        sys.exit(1)
    
    if not os.path.exists(args.timestamps):
        print(f"Error: Timestamps file not found: {args.timestamps}")
        sys.exit(1)
    
    # Load timestamps JSON
    with open(args.timestamps, 'r') as f:
        plays = json.load(f)
    
    if not plays or len(plays) == 0:
        print("Error: No plays found in timestamps JSON")
        sys.exit(1)
    
    print(f"Loaded {len(plays)} plays from timestamps")
    print(f"Source video: {args.video}")
    
    # Create output directory - always save to output/clips/{timestamp}
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    base_output_dir = os.path.join(args.output_dir, timestamp)
    os.makedirs(base_output_dir, exist_ok=True)
    print(f"Output directory: {base_output_dir}")
    
    print("="*70)
    
    # Generate clips
    generated_clips = []
    failed_clips = []
    
    for idx, play in enumerate(plays, 1):
        # Extract data
        play_index = play.get('play_index', idx - 1)
        team = play.get('team', 'Unknown')
        quarter = play.get('quarter', 0)
        game_time = play.get('game_time', '0:00').replace(':', '-').replace('.0', '')
        video_start = play.get('video_start', 0)
        video_end = play.get('video_end', 0)
        description = play.get('description', '')
        
        duration = video_end - video_start
        
        attributes = extract_attributes(description, team)
        primary = primary_action(attributes)

        filename = f"clip_{play_index}_{sanitize_filename(team)}_Q{quarter}_{game_time}.{args.format}"
        output_path = os.path.join(base_output_dir, filename)
        
        print(f"\n[{idx}/{len(plays)}] Generating: {filename}")
        print(f"  Play: {description[:60]}...")
        print(f"  Primary: {primary}  Attributes: {attributes}")
        print(f"  Time range: {video_start:.1f}s - {video_end:.1f}s ({duration:.1f}s)")
        
        # Extract clip
        success = extract_clip(
            args.video,
            video_start,
            video_end,
            output_path,
            keep_audio=not args.no_audio,
            fast_copy=not args.re_encode
        )
        
        if success and os.path.exists(output_path):
            file_size = get_file_size(output_path)
            print(f"  ✓ Generated: {file_size}")

            generated_clips.append({
                'filename': filename,
                'path': output_path,
                'primary': primary,
                'attributes': attributes,
                'play_index': play_index,
                'team': team,
                'quarter': quarter,
                'game_time': game_time,
                'description': description,
                'video_start': video_start,
                'video_end': video_end,
                'duration': duration,
                'file_size': file_size
            })
        else:
            print(f"  ✗ Failed to generate clip")
            failed_clips.append(filename)
    
    # Combine clips if requested
    combined_path = None
    if args.combined and generated_clips:
        print("\n" + "="*70)
        print("COMBINING CLIPS")
        print("="*70)
        
        # Clips are always in base_output_dir now
        clip_paths = [os.path.join(base_output_dir, clip['filename']) for clip in generated_clips]
        combined_filename = f"combined_all_clips.{args.format}"
        combined_path = os.path.join(base_output_dir, combined_filename)
        
        print(f"Combining {len(clip_paths)} clips into: {combined_filename}")
        
        if combine_clips(clip_paths, combined_path):
            file_size = get_file_size(combined_path)
            print(f"✓ Combined video created: {file_size}")
        else:
            print(f"✗ Failed to combine clips")
            combined_path = None
    
    metadata = {
        'source_video': args.video,
        'timestamps_file': args.timestamps,
        'generated_at': datetime.now().isoformat(),
        'total_clips': len(generated_clips),
        'failed_clips': len(failed_clips),
        'combined_video': os.path.basename(combined_path) if combined_path else None,
        'clips': generated_clips
    }
    
    metadata_path = os.path.join(base_output_dir, 'clips_metadata.json')
    os.makedirs(base_output_dir, exist_ok=True)
    with open(metadata_path, 'w') as f:
        json.dump(metadata, f, indent=2)
    
    # Print summary
    print("\n" + "="*70)
    print("CLIP GENERATION COMPLETE")
    print("="*70)
    print(f"✓ Successfully generated: {len(generated_clips)} clips")
    if failed_clips:
        print(f"✗ Failed: {len(failed_clips)} clips")
    if combined_path and os.path.exists(combined_path):
        print(f"✓ Combined video: {os.path.basename(combined_path)} ({get_file_size(combined_path)})")
    print(f"✓ Clips saved to: {base_output_dir}")
    print(f"✓ Metadata saved to: {metadata_path}")
    
    if generated_clips:
        from collections import Counter
        action_counts = Counter(clip['attributes']['action_type'] for clip in generated_clips)
        subtype_counts = Counter(
            clip['attributes']['shot_subtype']
            for clip in generated_clips
            if clip['attributes'].get('shot_subtype')
        )
        print("\nACTION TYPE DISTRIBUTION:")
        for action, count in sorted(action_counts.items(), key=lambda x: -x[1]):
            print(f"  {action}: {count}")
        if subtype_counts:
            print("\nSHOT SUBTYPE DISTRIBUTION:")
            for subtype, count in sorted(subtype_counts.items(), key=lambda x: -x[1]):
                print(f"  {subtype}: {count}")


if __name__ == "__main__":
    main()

