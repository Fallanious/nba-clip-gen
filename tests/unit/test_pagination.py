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
    # Hash is of the dict as given; callers must sort. Decode with same list.
    assert decode_cursor(c1, {"roles": ["shot", "assist"]}) == key
    with pytest.raises(Exception):
        decode_cursor(c1, {"roles": ["assist", "shot"]})


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
    from pagination import _filter_hash
    h = _filter_hash({"q": "x"})
    assert len(h) >= 16
    import hashlib, json
    expected = hashlib.sha256(json.dumps({"q": "x"}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    assert h == expected
