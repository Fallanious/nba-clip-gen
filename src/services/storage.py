"""S3 storage helpers for video uploads."""

import os

try:
    import boto3
except ImportError:
    boto3 = None


# Content type mapping for supported video formats
CONTENT_TYPES = {
    '.mkv': 'video/x-matroska',
    '.mp4': 'video/mp4',
    '.webm': 'video/webm',
}


def content_type_for(path):
    """
    Get the appropriate content type for a video file.
    
    Args:
        path: File path or name
        
    Returns:
        str: Content type (e.g., 'video/x-matroska')
        
    Raises:
        ValueError: If the file extension is not supported
    """
    ext = os.path.splitext(path)[1].lower()
    
    if ext not in CONTENT_TYPES:
        raise ValueError(f"Unsupported video format: {ext}")
    
    return CONTENT_TYPES[ext]


def s3_key_for_game(bbref_id, video_path):
    """
    Generate S3 key for a game video.
    
    Args:
        bbref_id: Basketball Reference game ID (e.g., '202604240PHI')
        video_path: Path to the video file
        
    Returns:
        str: S3 key (e.g., 'games/202604240PHI.mkv')
    """
    ext = os.path.splitext(video_path)[1]
    return f"games/{bbref_id}{ext}"


def upload_game(video_path, bbref_id):
    """
    Upload a game video to S3.
    
    Args:
        video_path: Path to the video file to upload
        bbref_id: Basketball Reference game ID
        
    Returns:
        str: S3 key of the uploaded file
    """
    if boto3 is None:
        raise RuntimeError("boto3 is required for S3 uploads. Install it with: pip install boto3")
    
    s3 = boto3.client('s3')
    bucket = os.environ.get('S3_BUCKET')
    
    if not bucket:
        raise ValueError("S3_BUCKET environment variable not set")
    
    key = s3_key_for_game(bbref_id, video_path)
    content_type = content_type_for(video_path)
    
    s3.upload_file(
        video_path,
        bucket,
        key,
        ExtraArgs={'ContentType': content_type}
    )
    
    return key
