"""Pagination unit tests."""
import os
import sys
import time

import pytest

sys.path.insert(0, "src")
os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("CURSOR_SECRET", "12345678901234567890123456789012")


def test_roundtrip_and_full_hmac():
    from pagination import encode_cursor, decode_cursor, _sign

    filters = {"q": "Jalen", "roles": ["assist", "shot"]}
    cur = encode_cursor(["Smith", 123], filters)
    payload, sig = cur.rsplit(".", 1)
    assert len(sig) >= 32  # full SHA-256 hex is 64; never the old 16
    assert sig == _sign(payload)
    assert decode_cursor(cur, filters) == ["Smith", 123]


def test_sorted_list_filters_hash_the_same():
    from pagination import encode_cursor, decode_cursor

    key = ["A", 1]
    c1 = encode_cursor(key, {"roles": ["shot", "assist"]})
    # List order must not matter: clients may reorder query params between pages.
    assert decode_cursor(c1, {"roles": ["assist", "shot"]}) == key
    assert decode_cursor(c1, {"roles": ["shot", "assist"]}) == key


def test_filter_mismatch_rejected():
    from pagination import encode_cursor, decode_cursor
    from errors import ApiError

    cur = encode_cursor(["A", 1], {"q": "a"})
    with pytest.raises(ApiError) as ei:
        decode_cursor(cur, {"q": "b"})
    assert ei.value.code == "invalid_cursor"


def test_non_ascii_signature_is_400_not_500():
    from pagination import encode_cursor, decode_cursor
    from errors import ApiError

    cur = encode_cursor(["A", 1], {"q": "a"})
    payload, _ = cur.rsplit(".", 1)
    bad = payload + ".\u2603" * 8
    with pytest.raises(ApiError) as ei:
        decode_cursor(bad, {"q": "a"})
    assert ei.value.status == 400
    assert ei.value.code == "invalid_cursor"


def test_expired_cursor_rejected(monkeypatch):
    from pagination import encode_cursor, decode_cursor
    from errors import ApiError

    monkeypatch.setenv("CURSOR_MAX_AGE_S", "1")
    cur = encode_cursor(["A", 1], {"q": "a"})
    real_now = time.time()
    monkeypatch.setattr("pagination.time.time", lambda: real_now + 10)
    with pytest.raises(ApiError) as ei:
        decode_cursor(cur, {"q": "a"})
    assert "expired" in ei.value.message.lower() or ei.value.code == "invalid_cursor"


def test_key_version_mismatch(monkeypatch):
    from pagination import encode_cursor, decode_cursor
    from errors import ApiError

    monkeypatch.setenv("CURSOR_KEY_VERSION", "1")
    cur = encode_cursor(["A", 1], {"q": "a"})
    monkeypatch.setenv("CURSOR_KEY_VERSION", "2")
    with pytest.raises(ApiError) as ei:
        decode_cursor(cur, {"q": "a"})
    assert ei.value.code == "invalid_cursor"


def test_filter_fingerprint_is_sha256_not_sha1():
    from pagination import _filter_hash, _normalize_filters
    h = _filter_hash({"q": "x"})
    assert len(h) >= 16
    import hashlib
    expected = hashlib.sha256(_normalize_filters({"q": "x"}).encode()).hexdigest()[:16]
    assert h == expected


def test_date_filter_does_not_500():
    """default=str so date values hash instead of TypeError -> 500."""
    from datetime import date
    from pagination import encode_cursor, decode_cursor

    filters = {"date_from": date(2026, 1, 1)}
    cur = encode_cursor(["A", 1], filters)
    assert decode_cursor(cur, filters) == ["A", 1]


def test_malformed_signed_payload_is_invalid_cursor(monkeypatch):
    """A signed payload that is not a dict, or whose k is not a list, is 400."""
    import json, base64, hmac, hashlib
    from pagination import decode_cursor, _sign
    from errors import ApiError
    from config import config

    def signed(obj):
        payload_json = json.dumps(obj, separators=(",", ":"))
        payload_b64 = base64.urlsafe_b64encode(payload_json.encode()).decode().rstrip("=")
        return f"{payload_b64}.{_sign(payload_b64)}"

    with pytest.raises(ApiError) as ei:
        decode_cursor(signed([1, 2, 3]), {"q": "a"})
    assert ei.value.code == "invalid_cursor"
    assert ei.value.status == 400

    bad_k = {"v": 1, "k": "not-a-list", "f": "x", "iat": int(time.time()), "kv": 1}
    with pytest.raises(ApiError) as ei:
        decode_cursor(signed(bad_k), {"q": "a"})
    assert ei.value.code == "invalid_cursor"


def test_decode_errors_are_not_chained():
    from pagination import decode_cursor
    from errors import ApiError

    with pytest.raises(ApiError) as ei:
        decode_cursor("not-a-cursor", {"q": "a"})
    assert ei.value.__cause__ is None


def test_validate_requires_secret_outside_dev(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("CURSOR_SECRET", raising=False)
    import importlib
    import config as config_module
    importlib.reload(config_module)
    with pytest.raises(ValueError):
        config_module.config.validate()
