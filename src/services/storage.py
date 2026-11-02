"""S3 storage helpers for video uploads."""

import os
import re

try:
    import boto3
except ImportError:
    boto3 = None


# Content type mapping for supported video formats
CONTENT_TYPES = {
    ".mkv": "video/x-matroska",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
}

# ASCII digits only; \d would also match Unicode Nd characters.
BBREF_ID_RE = re.compile(r"[0-9]{9}[A-Z]{3}")


def _validate_bbref_id(bbref_id):
    """
    Validate Basketball Reference game ID format.

    Format: 9 ASCII digits followed by 3 uppercase letters (e.g., 202604240PHI)
    """
    if not bbref_id:
        raise ValueError("bbref_id cannot be empty")

    if not isinstance(bbref_id, str):
        raise ValueError(f"bbref_id must be a string, got {type(bbref_id).__name__}")

    if not BBREF_ID_RE.fullmatch(bbref_id):
        raise ValueError(
            f"Invalid bbref_id format: '{bbref_id}'. "
            f"Must be 9 digits followed by 3 uppercase letters (e.g., 202604240PHI)"
        )

    return bbref_id


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
        bbref_id: Basketball Reference game ID (validated format: 9 digits + 3 uppercase letters)
        video_path: Path to the video file

    Returns:
        str: S3 key (e.g., 'games/202604240PHI.mkv')

    Raises:
        ValueError: If bbref_id format is invalid or the extension is unsupported
    """
    bbref_id = _validate_bbref_id(bbref_id)
    ext = os.path.splitext(video_path)[1].lower()
    if ext not in CONTENT_TYPES:
        raise ValueError(f"Unsupported video format: {ext}")
    return f"games/{bbref_id}{ext}"


def upload_game(video_path, bbref_id):
    """
    Upload a game video to S3.

    Args:
        video_path: Path to the video file to upload
        bbref_id: Basketball Reference game ID (validated format)

    Returns:
        str: S3 key of the uploaded file

    Raises:
        ValueError: If bbref_id format is invalid
        RuntimeError: If boto3 is not installed
    """
    if boto3 is None:
        raise RuntimeError("boto3 is required for S3 uploads. Install it with: pip install boto3")

    bbref_id = _validate_bbref_id(bbref_id)

    s3 = boto3.client("s3")
    bucket = os.environ.get("S3_BUCKET")

    if not bucket:
        raise ValueError("S3_BUCKET environment variable not set")

    key = s3_key_for_game(bbref_id, video_path)
    content_type = content_type_for(video_path)

    s3.upload_file(
        video_path,
        bucket,
        key,
        ExtraArgs={"ContentType": content_type},
    )

    return key
