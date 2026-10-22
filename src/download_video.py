import os
import sys
import time
import yt_dlp


def _video_saved_since(folder, started):
    """Return a non-empty video written to film during this download."""
    newest = None
    newest_mtime = started - 2
    for name in os.listdir(folder):
        if not name.endswith((".mp4", ".mkv", ".webm")):
            continue
        path = os.path.join(folder, name)
        if not os.path.isfile(path) or os.path.getsize(path) <= 0:
            continue
        mtime = os.path.getmtime(path)
        if mtime >= newest_mtime:
            newest = path
            newest_mtime = mtime
    return newest


def download_video(url, output_folder="film"):
    output_path = os.path.join(os.getcwd(), output_folder)
    
    # Create the output folder if it doesn't exist
    if not os.path.exists(output_path):
        os.makedirs(output_path)
        print(f"Created folder: {output_path}")
        
    format_string = 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best'
    
    # Configure yt-dlp options
    ydl_opts = {
        'format': format_string,
        'outtmpl': os.path.join(output_path, '%(title)s.%(ext)s'),
        'merge_output_format': 'mp4',
        'postprocessors': [{
            'key': 'FFmpegMetadata',
        }],
        # this is because of quickplayer. Need to move metadata to the front of the file
        'postprocessor_args': [
            '-movflags', 'faststart'
        ],
        'ignoreerrors': False,
    }
    
    started = time.time()
    ydl_opts['quiet'] = True
    ydl_opts['no_warnings'] = True
    ydl_opts['noprogress'] = True

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(url, download=True)
    except Exception as e:
        print(f"Download failed: {e}")
        sys.exit(1)

    saved = _video_saved_since(output_path, started)
    if not saved:
        print("Download failed: no video file was saved to film")
        sys.exit(1)
    print(f"Saved {os.path.basename(saved)}")


def check_ffmpeg():
    """Check if ffmpeg is available."""
    try:
        import shutil
        return shutil.which('ffmpeg') is not None
    except:
        return False


def main():
    """Main function to handle command-line execution."""
    if len(sys.argv) < 2:
        print("Usage: python download_youtube.py <YouTube_URL>")
        print("Example: python download_youtube.py https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        sys.exit(1)
    
    url = sys.argv[1]
    download_video(url)


if __name__ == "__main__":
    main()