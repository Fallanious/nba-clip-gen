"""Cursor-based pagination with HMAC signing."""

import base64
import hashlib
import hmac
import json
from typing import Any, Optional
from config import config
from errors import ApiError


def _normalize_filters(filters: dict[str, Any]) -> str:
    """Normalize filters to a canonical string for hashing."""
    # Sort keys and convert to JSON
    normalized = json.dumps(filters, sort_keys=True, separators=(',', ':'))
    return normalized


def encode_cursor(key: list[Any], filters: dict[str, Any]) -> str:
    """Encode a cursor with HMAC signature."""
    # Normalize filters to hash
    filters_str = _normalize_filters(filters)
    filters_hash = hashlib.sha1(filters_str.encode()).hexdigest()[:8]

    # Build cursor payload
    payload = {
        "v": 1,
        "k": key,
        "f": filters_hash
    }

    # Encode to base64
    payload_json = json.dumps(payload, separators=(',', ':'))
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode()).decode().rstrip('=')

    # Add HMAC signature
    signature = hmac.new(
        config.CURSOR_SECRET.encode(),
        payload_b64.encode(),
        hashlib.sha256
    ).hexdigest()[:16]

    return f"{payload_b64}.{signature}"


def decode_cursor(cursor: str, filters: dict[str, Any]) -> list[Any]:
    """Decode and validate a cursor."""
    try:
        # Split payload and signature
        if '.' not in cursor:
            raise ValueError("Invalid cursor format")

        payload_b64, signature = cursor.rsplit('.', 1)

        # Verify HMAC
        expected_signature = hmac.new(
            config.CURSOR_SECRET.encode(),
            payload_b64.encode(),
            hashlib.sha256
        ).hexdigest()[:16]

        if not hmac.compare_digest(signature, expected_signature):
            raise ValueError("Invalid cursor signature")

        # Decode base64 (add padding if needed)
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += '=' * padding

        payload_json = base64.urlsafe_b64decode(payload_b64).decode()
        payload = json.loads(payload_json)

        # Validate version
        if payload.get("v") != 1:
            raise ValueError("Unsupported cursor version")

        # Validate filters hash
        filters_str = _normalize_filters(filters)
        filters_hash = hashlib.sha1(filters_str.encode()).hexdigest()[:8]

        if payload.get("f") != filters_hash:
            raise ApiError(
                code="invalid_cursor",
                message="Cursor does not match current filters",
                status=400
            )

        return payload.get("k", [])

    except (ValueError, KeyError, json.JSONDecodeError) as e:
        raise ApiError(
            code="invalid_cursor",
            message=f"Invalid cursor: {str(e)}",
            status=400
        )
