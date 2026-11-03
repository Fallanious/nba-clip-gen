"""Cursor-based pagination with HMAC signing."""

import base64
import hashlib
import hmac
import json
import time
from typing import Any
from config import config
from errors import ApiError

CURSOR_VERSION = 1
FILTER_HASH_HEX_LEN = 16  # SHA-256 truncated to >=16 hex
MAX_AGE_DEFAULT = 86400
# Intended clock-skew allowance: accept iat up to 30s in the future; reject at 31s.
CLOCK_SKEW_S = 30
MAX_KEY_LEN = 16
MAX_CURSOR_BYTES = 1024
PAYLOAD_KEYS = {"v", "k", "f", "iat", "kv"}


def _normalize_filters(filters: dict[str, Any]) -> str:
    """Canonical JSON: dict keys sorted, list/tuple/set values sorted.

    Clients may reorder query params between pages; hashing must not care.
    default=str so a date/UUID filter becomes invalid_cursor, not a 500.
    """
    canon = {
        k: sorted(v, key=lambda x: json.dumps(x, default=str)) if isinstance(v, (list, tuple, set)) else v
        for k, v in filters.items()
    }
    return json.dumps(canon, sort_keys=True, separators=(",", ":"), default=str)


def _filter_hash(filters: dict[str, Any]) -> str:
    """SHA-256 fingerprint of normalized filters, stored inside the signed payload."""
    digest = hashlib.sha256(_normalize_filters(filters).encode()).hexdigest()
    return digest[:FILTER_HASH_HEX_LEN]


def _key_version() -> int:
    return config.CURSOR_KEY_VERSION


def _max_age() -> int:
    return config.CURSOR_MAX_AGE_S


def _now() -> float:
    """Clock hook so tests can freeze time without patching the stdlib."""
    return time.time()


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
        "iat": int(_now()),
        "kv": _key_version(),
    }
    payload_json = json.dumps(payload, separators=(",", ":"))
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode()).decode().rstrip("=")
    return f"{payload_b64}.{_sign(payload_b64)}"


def _invalid():
    raise ApiError(code="invalid_cursor", message="Invalid cursor format", status=400) from None


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_key_elem(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    return isinstance(value, (str, int, float)) or value is None


def decode_cursor(cursor: str, filters: dict[str, Any]) -> list[Any]:
    """Decode and validate a cursor."""
    try:
        if not isinstance(cursor, str):
            _invalid()

        # Byte-length of the raw cursor (UTF-8), including multi-byte characters.
        # Reject before HMAC or JSON decode so an oversized value cannot be used
        # as a hashing/decoding oracle.
        if len(cursor.encode("utf-8")) > MAX_CURSOR_BYTES:
            _invalid()

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
        if not isinstance(payload, dict) or set(payload.keys()) != PAYLOAD_KEYS:
            _invalid()

        if not _is_int(payload.get("v")) or payload.get("v") != CURSOR_VERSION:
            _invalid()

        if not _is_int(payload.get("kv")) or payload.get("kv") != _key_version():
            _invalid()

        iat = payload.get("iat")
        # Exact int: reject bool, float (incl. NaN/Inf), numeric strings, None, negatives.
        if not _is_int(iat) or iat < 0:
            _invalid()
        now = _now()
        if iat > now + CLOCK_SKEW_S:
            _invalid()
        if now - iat > _max_age():
            raise ApiError(
                code="invalid_cursor",
                message="Cursor expired",
                status=400,
            ) from None

        if not isinstance(payload.get("f"), str) or payload.get("f") != _filter_hash(filters):
            raise ApiError(
                code="invalid_cursor",
                message="Cursor does not match current filters",
                status=400,
            ) from None

        key = payload.get("k")
        if (
            not isinstance(key, list)
            or len(key) > MAX_KEY_LEN
            or not all(_is_key_elem(item) for item in key)
        ):
            _invalid()
        return key

    except (ValueError, KeyError, TypeError, json.JSONDecodeError, AttributeError, OverflowError):
        _invalid()
