import os
import sys
import yt_dlp


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
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            print(f"Downloading video from: {url}")
            print(f"Saving to: {output_path}")
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            print(f"Download completed! Video saved as: {os.path.basename(filename)}")
    except Exception as e:
        print(f"Error downloading video: {e}")
        sys.exit(1)


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