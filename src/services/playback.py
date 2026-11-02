"""S3 playback URL generation service."""

from datetime import datetime, timedelta, timezone
from typing import Optional
import os
import boto3
from botocore.exceptions import (
    ClientError, NoCredentialsError, ReadTimeoutError, EndpointConnectionError, BotoCoreError,
)
from config import config
from errors import ApiError

# Same mapping as PR #2 services.storage.CONTENT_TYPES. Used when that module
# is not on this stack yet. Do not consult the host OS MIME database.
_MIME_BY_EXT = {
    ".mkv": "video/x-matroska",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
}


def _mime_for_key(key: str) -> str:
    """Content-Type from the object key's extension.

    Prefers services.storage.content_type_for so upload and playback agree
    once PR #2 lands. Unknown extensions are application/octet-stream.
    """
    try:
        from services.storage import content_type_for
        try:
            return content_type_for(key)
        except ValueError:
            return "application/octet-stream"
    except ImportError:
        ext = os.path.splitext(key or "")[1].lower()
        return _MIME_BY_EXT.get(ext, "application/octet-stream")


def _allow_fallback() -> bool:
    """Fallback presigns full game video; disabled in production unless explicitly enabled."""
    raw = os.getenv("PLAYBACK_ALLOW_FALLBACK")
    if config.APP_ENV == "production" and raw is None:
        return False
    if raw is None:
        return True
    return raw.lower() == "true"


def _get_s3_client():
    """Get boto3 S3 client.

    boto3.client() does not resolve credentials; NoCredentialsError is raised
    later on the first signed call. Probe credentials here so missing creds
    become 503 storage_unavailable instead of a 500.
    """
    if not config.S3_BUCKET or not config.AWS_REGION:
        raise ApiError(
            code="storage_unavailable",
            message="Storage service unavailable",
            status=503
        )

    try:
        session = boto3.session.Session(region_name=config.AWS_REGION)
        if session.get_credentials() is None:
            raise NoCredentialsError()
        return session.client("s3")
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
        error_code = e.response.get('Error', {}).get('Code', '')
        if error_code in ('404', 'NoSuchKey'):
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
    except NoCredentialsError:
        raise ApiError(
            code="storage_unavailable",
            message="Storage service unavailable",
            status=503
        )
    except BotoCoreError:
        raise ApiError(
            code="storage_error",
            message="Storage service error",
            status=502
        )


def generate_presigned_url(
    s3_key: str,
    ttl: int,
    bucket: Optional[str] = None
) -> tuple[str, datetime, str]:
    """Generate a presigned URL for an S3 object.

    Returns: (url, expires_at, mime). mime is derived once from the key and
    used for both ResponseContentType and the JSON `mime` field.
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
    content_type = _mime_for_key(s3_key)

    try:
        _verify_object(s3_client, bucket, s3_key)
        url = s3_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': bucket,
                'Key': s3_key,
                'ResponseContentType': content_type,
                'ResponseCacheControl': 'private, max-age=300',
            },
            ExpiresIn=ttl
        )
    except NoCredentialsError:
        raise ApiError(
            code="storage_unavailable",
            message="Storage service unavailable",
            status=503
        )
    except (ReadTimeoutError, EndpointConnectionError, BotoCoreError):
        raise ApiError(
            code="storage_error",
            message="Storage service error",
            status=502
        )

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)
    expires_at = expires_at.replace(tzinfo=None)

    return url, expires_at, content_type


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
        elif _allow_fallback() and clip_data['game_key']:
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
                    "fallback_available": _allow_fallback() and bool(clip_data['game_key'])
                }
            )

        ttl = config.PLAYBACK_URL_TTL
        key = clip_data['s3_key']
        url, expires_at, mime = generate_presigned_url(key, ttl)

        return {
            "clip_id": str(clip_data['id']),
            "mode": "clip",
            "url": url,
            "mime": mime,
            "expires_at": expires_at.isoformat() + "Z",
            "ttl_s": ttl
        }

    elif mode == 'fallback':
        if not _allow_fallback():
            raise ApiError(
                code="clip_not_ready",
                message="Clip is not ready for playback",
                status=409,
                details={
                    "status": clip_data['status'],
                    "fallback_available": False
                }
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

        start, end = clip_data.get("video_start_s"), clip_data.get("video_end_s")
        try:
            start_s = float(start)
            end_s = float(end)
        except (TypeError, ValueError):
            raise ApiError(
                code="clip_not_ready",
                message="Clip is not ready for playback",
                status=409,
                details={
                    "status": clip_data.get("status"),
                    "fallback_available": True,
                },
            )

        ttl = config.PLAYBACK_FALLBACK_TTL
        key = clip_data['game_key']
        url, expires_at, mime = generate_presigned_url(key, ttl)

        return {
            "clip_id": str(clip_data['id']),
            "mode": "fallback",
            "url": url,
            "mime": mime,
            "start_s": start_s,
            "end_s": end_s,
            "expires_at": expires_at.isoformat() + "Z",
            "ttl_s": ttl
        }

    else:
        raise ApiError(
            code="invalid_param",
            message="Invalid mode",
            status=400,
            details={"param": "mode"}
        )
