"""Cursor-based pagination with HMAC signing."""

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any
from config import config
from errors import ApiError

CURSOR_VERSION = 1
FILTER_HASH_HEX_LEN = 16  # SHA-256 truncated to >=16 hex
MAX_AGE_DEFAULT = 86400


def _normalize_filters(filters: dict[str, Any]) -> str:
    """Normalize filters to a canonical string for hashing."""
    return json.dumps(filters, sort_keys=True, separators=(",", ":"))


def _filter_hash(filters: dict[str, Any]) -> str:
    """SHA-256 fingerprint of normalized filters, stored inside the signed payload."""
    digest = hashlib.sha256(_normalize_filters(filters).encode()).hexdigest()
    return digest[:FILTER_HASH_HEX_LEN]


def _key_version() -> int:
    return int(os.getenv("CURSOR_KEY_VERSION", "1"))


def _max_age() -> int:
    return int(os.getenv("CURSOR_MAX_AGE_S", str(MAX_AGE_DEFAULT)))


def _sign(payload_b64: str) -> str:
    """Full SHA-256 HMAC hex (64 chars)."""
    return hmac.new(
        config.CURSOR_SECRET.encode(),
        payload_b64.encode(),
        hashlib.sha256,
    ).hexdigest()


def encode_cursor(key: list[Any], filters: dict[str, Any]) -> str:
    """Encode a cursor with HMAC signature, issued-at, and key version."""
    payload = {
        "v": CURSOR_VERSION,
        "k": key,
        "f": _filter_hash(filters),
        "iat": int(time.time()),
        "kv": _key_version(),
    }
    payload_json = json.dumps(payload, separators=(",", ":"))
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode()).decode().rstrip("=")
    return f"{payload_b64}.{_sign(payload_b64)}"


def _invalid():
    raise ApiError(code="invalid_cursor", message="Invalid cursor format", status=400)


def decode_cursor(cursor: str, filters: dict[str, Any]) -> list[Any]:
    """Decode and validate a cursor."""
    try:
        if "." not in cursor:
            _invalid()

        payload_b64, signature = cursor.rsplit(".", 1)

        expected = _sign(payload_b64)
        try:
            ok = hmac.compare_digest(
                signature.encode("ascii"),
                expected.encode("ascii"),
            )
        except (TypeError, UnicodeEncodeError, ValueError):
            _invalid()
        if not ok:
            _invalid()

        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64_pad = payload_b64 + "=" * padding
        else:
            payload_b64_pad = payload_b64

        payload = json.loads(base64.urlsafe_b64decode(payload_b64_pad).decode())

        if payload.get("v") != CURSOR_VERSION:
            _invalid()

        if payload.get("kv") != _key_version():
            _invalid()

        iat = payload.get("iat")
        if not isinstance(iat, (int, float)):
            _invalid()
        if time.time() - float(iat) > _max_age():
            raise ApiError(
                code="invalid_cursor",
                message="Cursor expired",
                status=400,
            )

        if payload.get("f") != _filter_hash(filters):
            raise ApiError(
                code="invalid_cursor",
                message="Cursor does not match current filters",
                status=400,
            )

        return payload.get("k", [])

    except ApiError:
        raise
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        _invalid()
