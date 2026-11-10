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


def presign_get_url(key, expires_in=None):
    """A short-lived GET URL for one object. The URL is a credential. Do not log it."""
    if boto3 is None:
        raise RuntimeError("boto3 is required for S3. Install it with: pip install boto3")
    bucket = os.environ.get("S3_BUCKET")
    if not bucket:
        raise RuntimeError("S3_BUCKET is not set")
    if not isinstance(key, str) or not key or key.startswith("/") or ".." in key.split("/"):
        raise ValueError("s3 key is not usable")
    expires = int(expires_in if expires_in is not None else os.environ.get("PLAYBACK_URL_TTL", "300"))
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-2"
    client = boto3.client("s3", region_name=region)
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires,
    )


def upload_game(video_path, bbref_id, progress=None):
    """
    Upload a game video to S3 in multipart chunks.

    Args:
        video_path: Path to the video file to upload
        bbref_id: Basketball Reference game ID (validated format)
        progress: Optional callback. boto3 calls it with the size of each chunk sent.

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

    # upload_file splits large games into parts and sends several parts at once.
    # A private bucket does not need CloudFront for this step. CloudFront only
    # helps later, when many people watch the same object.
    from boto3.s3.transfer import TransferConfig
    kwargs = {
        "ExtraArgs": {"ContentType": content_type},
        "Config": TransferConfig(
            multipart_threshold=64 * 1024 * 1024,
            multipart_chunksize=64 * 1024 * 1024,
            max_concurrency=4,
            use_threads=True,
        ),
    }
    if progress is not None:
        kwargs["Callback"] = progress

    s3.upload_file(video_path, bucket, key, **kwargs)

    return key
