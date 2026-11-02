"""S3 playback URL generation service."""

from datetime import datetime, timedelta, timezone
from typing import Optional
import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from config import config
from errors import ApiError


def _get_s3_client():
    """Get boto3 S3 client."""
    if not config.S3_BUCKET or not config.AWS_REGION:
        raise ApiError(
            code="storage_unavailable",
            message="Storage service unavailable",
            status=503
        )
    
    try:
        return boto3.client('s3', region_name=config.AWS_REGION)
    except NoCredentialsError:
        raise ApiError(
            code="storage_unavailable",
            message="Storage service unavailable",
            status=503
        )


def _s3_client():
    """Public accessor for S3 client (for test mocking)."""
    return _get_s3_client()


def _verify_object(s3_client, bucket: str, key: str) -> None:
    """Verify object exists in S3."""
    if not config.PLAYBACK_VERIFY_OBJECT:
        return

    try:
        s3_client.head_object(Bucket=bucket, Key=key)
    except ClientError as e:
        if e.response['Error']['Code'] == '404':
            raise ApiError(
                code="media_missing",
                message="Media file not found",
                status=404
            )
        else:
            raise ApiError(
                code="storage_error",
                message="Storage service error",
                status=502
            )


def generate_presigned_url(
    s3_key: str,
    ttl: int,
    bucket: Optional[str] = None
) -> tuple[str, datetime]:
    """Generate a presigned URL for an S3 object.
    
    Returns: (url, expires_at)
    """
    if not bucket:
        bucket = config.S3_BUCKET

    if not bucket:
        raise ApiError(
            code="storage_unavailable",
            message="Storage service unavailable",
            status=503
        )

    s3_client = _s3_client()

    # Verify object exists (if configured)
    _verify_object(s3_client, bucket, s3_key)

    # Generate presigned URL
    url = s3_client.generate_presigned_url(
        'get_object',
        Params={
            'Bucket': bucket,
            'Key': s3_key,
            'ResponseContentType': 'video/mp4',
            'ResponseCacheControl': 'private, max-age=300',
        },
        ExpiresIn=ttl
    )

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)

    return url, expires_at


def get_playback_url(clip_data: dict, mode: Optional[str] = None) -> dict:
    """Get playback URL for a clip.
    
    Args:
        clip_data: Result from playback_target query
        mode: 'clip', 'fallback', or 'auto' (default)
    
    Returns:
        Dictionary with url, mode, expires_at, ttl_s, and optional start_s/end_s
    """
    if not mode or mode == 'auto':
        # Auto mode: prefer clip, fallback to game if allowed
        if clip_data['status'] == 'uploaded' and clip_data['s3_key']:
            mode = 'clip'
        elif config.PLAYBACK_ALLOW_FALLBACK and clip_data['game_key']:
            mode = 'fallback'
        else:
            # Clip not ready and no fallback
            raise ApiError(
                code="clip_not_ready",
                message="Clip is not ready for playback",
                status=409,
                details={
                    "status": clip_data['status'],
                    "fallback_available": False
                }
            )

    if mode == 'clip':
        if clip_data['status'] != 'uploaded' or not clip_data['s3_key']:
            raise ApiError(
                code="clip_not_ready",
                message="Clip is not ready for playback",
                status=409,
                details={
                    "status": clip_data['status'],
                    "fallback_available": config.PLAYBACK_ALLOW_FALLBACK and bool(clip_data['game_key'])
                }
            )

        ttl = config.PLAYBACK_URL_TTL
        key = clip_data['s3_key']
        url, expires_at = generate_presigned_url(key, ttl)

        return {
            "clip_id": str(clip_data['id']),
            "mode": "clip",
            "url": url,
            "mime": "video/mp4",
            "expires_at": expires_at.isoformat(),
            "ttl_s": ttl
        }

    elif mode == 'fallback':
        if not config.PLAYBACK_ALLOW_FALLBACK:
            raise ApiError(
                code="forbidden",
                message="Fallback mode not allowed",
                status=403
            )

        if not clip_data['game_key']:
            raise ApiError(
                code="clip_not_ready",
                message="Game footage not available",
                status=409,
                details={
                    "status": clip_data['status'],
                    "fallback_available": False
                }
            )

        ttl = config.PLAYBACK_FALLBACK_TTL
        key = clip_data['game_key']
        url, expires_at = generate_presigned_url(key, ttl)

        return {
            "clip_id": str(clip_data['id']),
            "mode": "fallback",
            "url": url,
            "mime": "video/mp4",
            "start_s": float(clip_data['video_start_s']),
            "end_s": float(clip_data['video_end_s']),
            "expires_at": expires_at.isoformat(),
            "ttl_s": ttl
        }

    else:
        raise ApiError(
            code="invalid_param",
            message="Invalid mode",
            status=400,
            details={"param": "mode"}
        )
