"""QA review tests for pagination (PR #4)."""
import base64
import datetime
import json
import os
import re
import sys
import time

import pytest

sys.path.insert(0, "src")
os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("CURSOR_SECRET", "12345678901234567890123456789012")


def signed(obj):
    import pagination
    b = base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    return b + "." + pagination._sign(b)


def _make_app():
    try:
        from tests.conftest import _make_app as factory
        return factory()
    except ImportError:
        from app import create_app
        return create_app()


def test_list_filter_order_invariant():
    from pagination import encode_cursor, decode_cursor

    c = encode_cursor(["A", 1], {"roles": ["shot", "assist"]})
    assert decode_cursor(c, {"roles": ["assist", "shot"]}) == ["A", 1]
    assert decode_cursor(c, {"roles": ("assist", "shot")}) == ["A", 1]
    assert decode_cursor(c, {"roles": {"shot", "assist"}}) == ["A", 1]


def test_filter_hash_handles_dates_and_none():
    from pagination import _filter_hash

    assert _filter_hash({"d": datetime.date(2026, 1, 3)}) == _filter_hash({"d": "2026-01-03"})
    assert _filter_hash({"x": None})
    assert _filter_hash({"x": None}) != _filter_hash({})


def test_distinct_lists_still_differ():
    from pagination import _filter_hash

    assert _filter_hash({"roles": ["shot"]}) != _filter_hash({"roles": ["shot", "assist"]})


@pytest.mark.parametrize("cur", [
    "é.abc",
    "abc.é",
    "%%%.sig",
    ".",
    "",
    "a.b.c",
    "\x00.\x00",
    "A" * 10000 + ".x",
])
def test_non_ascii_or_garbage_anywhere_is_invalid_cursor(cur):
    from pagination import decode_cursor
    from errors import ApiError

    with pytest.raises(ApiError) as ei:
        decode_cursor(cur, {})
    assert ei.value.code == "invalid_cursor"
    assert ei.value.status == 400


@pytest.mark.parametrize("env", ["prod", "staging", "test"])
def test_create_app_fails_without_secret_outside_dev(env, monkeypatch):
    monkeypatch.setenv("APP_ENV", env)
    monkeypatch.delenv("CURSOR_SECRET", raising=False)
    with pytest.raises((ValueError, RuntimeError), match="CURSOR_SECRET"):
        _make_app()


def test_create_app_rejects_short_secret_outside_dev(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("CURSOR_SECRET", "short")
    with pytest.raises((ValueError, RuntimeError), match="at least 32 bytes"):
        _make_app()


def test_dev_fallback_only_in_dev(monkeypatch):
    from pagination import encode_cursor, decode_cursor
    from errors import ApiError

    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("CURSOR_SECRET", raising=False)
    _make_app()
    c = encode_cursor(["A", 1], {})
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("CURSOR_SECRET", "p" * 40)
    with pytest.raises(ApiError) as ei:
        decode_cursor(c, {})
    assert ei.value.code == "invalid_cursor"


def test_validate_reports_missing_secret_clearly(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.delenv("CURSOR_SECRET", raising=False)
    from config import Config

    with pytest.raises(ValueError) as ei:
        Config().validate()
    assert "CURSOR_SECRET" in str(ei.value)
    assert not isinstance(ei.value, (KeyError, AttributeError))


def test_tampered_payload_rejected():
    from pagination import encode_cursor, decode_cursor
    from errors import ApiError

    cur = encode_cursor(["A", 1], {})
    payload, sig = cur.rsplit(".", 1)
    flipped = ("B" if payload[0] != "B" else "C") + payload[1:]
    with pytest.raises(ApiError) as ei:
        decode_cursor(flipped + "." + sig, {})
    assert ei.value.code == "invalid_cursor"


def test_tampered_signature_rejected():
    from pagination import encode_cursor, decode_cursor
    from errors import ApiError

    cur = encode_cursor(["A", 1], {})
    payload, sig = cur.rsplit(".", 1)
    last = "0" if sig[-1] != "0" else "1"
    with pytest.raises(ApiError) as ei:
        decode_cursor(payload + "." + sig[:-1] + last, {})
    assert ei.value.code == "invalid_cursor"


def test_wrong_secret_rejected(monkeypatch):
    from pagination import encode_cursor, decode_cursor
    from errors import ApiError

    monkeypatch.setenv("CURSOR_SECRET", "a" * 32)
    cur = encode_cursor(["A", 1], {})
    monkeypatch.setenv("CURSOR_SECRET", "b" * 32)
    with pytest.raises(ApiError) as ei:
        decode_cursor(cur, {})
    assert ei.value.code == "invalid_cursor"


def test_signature_is_full_length_hex():
    from pagination import encode_cursor

    cur = encode_cursor(["A", 1], {})
    _, sig = cur.rsplit(".", 1)
    assert re.fullmatch(r"[0-9a-f]{64}", sig)


def test_future_iat_rejected():
    from pagination import decode_cursor, _filter_hash
    from errors import ApiError

    payload = {
        "v": 1,
        "kv": 1,
        "iat": time.time() + 10**9,
        "f": _filter_hash({}),
        "k": ["A", 1],
    }
    with pytest.raises(ApiError) as ei:
        decode_cursor(signed(payload), {})
    assert ei.value.code == "invalid_cursor"


def test_iat_boundary(monkeypatch):
    from pagination import decode_cursor, _filter_hash, MAX_AGE_DEFAULT
    from errors import ApiError

    now = 1_700_000_000
    monkeypatch.setattr("pagination.time.time", lambda: float(now))
    fhash = _filter_hash({})
    ok = signed({"v": 1, "kv": 1, "iat": now - MAX_AGE_DEFAULT, "f": fhash, "k": ["A", 1]})
    assert decode_cursor(ok, {}) == ["A", 1]
    bad = signed({"v": 1, "kv": 1, "iat": now - MAX_AGE_DEFAULT - 1, "f": fhash, "k": ["A", 1]})
    with pytest.raises(ApiError) as ei:
        decode_cursor(bad, {})
    assert ei.value.code == "invalid_cursor"


def test_bool_iat_rejected():
    from pagination import decode_cursor, _filter_hash
    from errors import ApiError

    payload = {"v": 1, "kv": 1, "iat": True, "f": _filter_hash({}), "k": ["A", 1]}
    with pytest.raises(ApiError) as ei:
        decode_cursor(signed(payload), {})
    assert ei.value.code == "invalid_cursor"


def test_no_padding_in_cursor():
    from pagination import encode_cursor

    assert "=" not in encode_cursor(["A", 1], {"q": "x"})


@pytest.mark.parametrize("payload", [[1, 2], "str", 123, None, True])
def test_signed_non_dict_payload_is_invalid_cursor(payload):
    from pagination import decode_cursor
    from errors import ApiError

    with pytest.raises(ApiError) as ei:
        decode_cursor(signed(payload), {})
    assert ei.value.code == "invalid_cursor"
    assert ei.value.status == 400
    assert not isinstance(ei.value, AttributeError)


@pytest.mark.parametrize("k", [
    "notalist",
    {},
    None,
    [[1]],
    [{"a": 1}],
    list(range(1000)),
])
def test_signed_bad_key_shape_is_invalid_cursor(k):
    from pagination import decode_cursor, _filter_hash
    from errors import ApiError

    payload = {
        "v": 1,
        "kv": 1,
        "iat": int(time.time()),
        "f": _filter_hash({}),
        "k": k,
    }
    with pytest.raises(ApiError) as ei:
        decode_cursor(signed(payload), {})
    assert ei.value.code == "invalid_cursor"


def test_invalid_cursor_not_chained():
    from pagination import decode_cursor
    from errors import ApiError

    with pytest.raises(ApiError) as ei:
        decode_cursor("!!!.x", {})
    assert ei.value.__cause__ is None
    assert ei.value.__suppress_context__ is True
