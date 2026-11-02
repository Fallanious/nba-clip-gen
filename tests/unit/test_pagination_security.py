"""QA tests for PR #4 security findings (APP_ENV, iat, HMAC, size, env clamp)."""
import base64
import json
import logging
import math
import os
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


def test_truncated_signature_rejected():
    from pagination import encode_cursor, decode_cursor
    from errors import ApiError

    cur = encode_cursor(["A", 1], {})
    payload, sig = cur.rsplit(".", 1)
    with pytest.raises(ApiError) as ei:
        decode_cursor(payload + "." + sig[:32], {})
    assert ei.value.code == "invalid_cursor"
    assert ei.value.status == 400
    assert ei.value.__cause__ is None
    assert ei.value.__suppress_context__ is True


@pytest.mark.parametrize("mutate", [
    lambda payload, sig: payload + ".",
    lambda payload, sig: payload + "." + sig + "0",
    lambda payload, sig: payload + "." + sig.swapcase(),
    lambda payload, sig: payload + "." + sig + "=",
])
def test_signature_variants_rejected_via_compare_digest(mutate, monkeypatch):
    import hmac
    import pagination
    from errors import ApiError

    cur = pagination.encode_cursor(["A", 1], {})
    payload, sig = cur.rsplit(".", 1)
    called = []
    real = hmac.compare_digest

    def wrapped(*args, **kwargs):
        called.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(pagination.hmac, "compare_digest", wrapped)
    with pytest.raises(ApiError) as ei:
        pagination.decode_cursor(mutate(payload, sig), {})
    assert ei.value.code == "invalid_cursor"
    assert ei.value.status == 400
    assert called, "hmac.compare_digest must be invoked"


@pytest.mark.parametrize("env", ["dev", "test", "prod", "production", "staging"])
def test_known_app_env_boots(env, monkeypatch):
    monkeypatch.setenv("APP_ENV", env)
    monkeypatch.setenv("CURSOR_SECRET", "k" * 32)
    from config import Config
    Config().validate()
    if env == "dev":
        _make_app()


@pytest.mark.parametrize("raw", [None, "", " ", "  dev", "Prod ", "qa", "DEV"])
def test_app_env_missing_empty_or_unknown_refuses_startup(raw, monkeypatch):
    if raw is None:
        monkeypatch.delenv("APP_ENV", raising=False)
    else:
        monkeypatch.setenv("APP_ENV", raw)
    monkeypatch.setenv("CURSOR_SECRET", "k" * 32)
    from config import Config
    with pytest.raises(ValueError) as ei:
        Config().validate()
    msg = str(ei.value)
    assert "APP_ENV" in msg
    assert "k" * 32 not in msg
    with pytest.raises((ValueError, RuntimeError)) as ei2:
        _make_app()
    assert "APP_ENV" in str(ei2.value)
    assert "k" * 32 not in str(ei2.value)


def test_prod_without_cursor_secret_fails(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.delenv("CURSOR_SECRET", raising=False)
    from config import Config
    with pytest.raises(ValueError, match="CURSOR_SECRET") as ei:
        Config().validate()
    assert "xxxxxxxx" not in str(ei.value)


def test_startup_refused_when_app_env_missing(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("CURSOR_SECRET", "k" * 32)
    with pytest.raises((ValueError, RuntimeError), match="APP_ENV"):
        _make_app()


@pytest.mark.parametrize("iat", [
    float("nan"),
    float("inf"),
    float("-inf"),
    1.5,
    1.0,
    "123",
    True,
    False,
    None,
    -1,
])
def test_iat_rejected_non_int_or_negative(iat):
    from pagination import decode_cursor, _filter_hash
    from errors import ApiError

    payload = {
        "v": 1,
        "kv": 1,
        "iat": iat,
        "f": _filter_hash({}),
        "k": ["A", 1],
    }
    with pytest.raises(ApiError) as ei:
        decode_cursor(signed(payload), {})
    assert ei.value.code == "invalid_cursor"
    assert ei.value.status == 400
    assert ei.value.__cause__ is None


@pytest.mark.parametrize("iat", [1e30, 1e308])
def test_huge_iat_rejected_without_overflow(iat):
    from pagination import decode_cursor, _filter_hash
    from errors import ApiError

    payload = {
        "v": 1,
        "kv": 1,
        "iat": iat,
        "f": _filter_hash({}),
        "k": ["A", 1],
    }
    try:
        with pytest.raises(ApiError) as ei:
            decode_cursor(signed(payload), {})
    except OverflowError:
        pytest.fail("huge iat must not raise OverflowError")
    assert ei.value.code == "invalid_cursor"
    assert ei.value.status == 400


def test_expiry_at_max_age_boundary(monkeypatch):
    from pagination import decode_cursor, _filter_hash, CLOCK_SKEW_S
    from config import CURSOR_MAX_AGE_S_DEFAULT
    from errors import ApiError

    now = 1_700_000_000
    monkeypatch.setattr("pagination.time.time", lambda: float(now))
    monkeypatch.setenv("CURSOR_MAX_AGE_S", str(CURSOR_MAX_AGE_S_DEFAULT))
    fhash = _filter_hash({})
    ok = signed({
        "v": 1, "kv": 1, "iat": now - CURSOR_MAX_AGE_S_DEFAULT,
        "f": fhash, "k": ["A", 1],
    })
    assert decode_cursor(ok, {}) == ["A", 1]
    bad = signed({
        "v": 1, "kv": 1, "iat": now - CURSOR_MAX_AGE_S_DEFAULT - 1,
        "f": fhash, "k": ["A", 1],
    })
    with pytest.raises(ApiError) as ei:
        decode_cursor(bad, {})
    assert ei.value.code == "invalid_cursor"

    assert CLOCK_SKEW_S == 30
    skew_ok = signed({
        "v": 1, "kv": 1, "iat": now + 30, "f": fhash, "k": ["A", 1],
    })
    assert decode_cursor(skew_ok, {}) == ["A", 1]
    skew_bad = signed({
        "v": 1, "kv": 1, "iat": now + 31, "f": fhash, "k": ["A", 1],
    })
    with pytest.raises(ApiError) as ei:
        decode_cursor(skew_bad, {})
    assert ei.value.code == "invalid_cursor"


def test_cursor_size_limit_in_bytes(monkeypatch):
    import pagination
    from errors import ApiError

    hmac_calls = []
    decode_calls = []
    real_sign = pagination._sign
    real_b64 = pagination.base64.urlsafe_b64decode

    def sign_wrap(payload_b64):
        hmac_calls.append(payload_b64)
        return real_sign(payload_b64)

    def b64_wrap(data):
        decode_calls.append(data)
        return real_b64(data)

    monkeypatch.setattr(pagination, "_sign", sign_wrap)
    monkeypatch.setattr(pagination.base64, "urlsafe_b64decode", b64_wrap)

    exact = pagination.encode_cursor(["A", 1], {})
    assert len(exact.encode("utf-8")) <= pagination.MAX_CURSOR_BYTES
    assert pagination.decode_cursor(exact, {}) == ["A", 1]

    hmac_calls.clear()
    decode_calls.clear()
    # 1024 bytes *with* a separator so the size check passes and HMAC/decode run.
    at_limit = "x." + ("A" * (pagination.MAX_CURSOR_BYTES - 2))
    assert len(at_limit.encode("utf-8")) == 1024
    with pytest.raises(ApiError) as ei:
        pagination.decode_cursor(at_limit, {})
    assert ei.value.code == "invalid_cursor"
    assert ei.value.status == 400
    assert hmac_calls, "1024-byte cursor must pass the size check and reach HMAC"

    hmac_calls.clear()
    decode_calls.clear()
    too_big = "A" * (pagination.MAX_CURSOR_BYTES + 1)
    assert len(too_big.encode("utf-8")) == 1025
    with pytest.raises(ApiError) as ei:
        pagination.decode_cursor(too_big, {})
    assert ei.value.code == "invalid_cursor"
    assert ei.value.status == 400
    assert hmac_calls == []
    assert decode_calls == []

    hmac_calls.clear()
    decode_calls.clear()
    utf8_ok = "." + ("é" * 511) + "a"  # 1 + 1022 + 1 = 1024 bytes, has separator
    assert len(utf8_ok.encode("utf-8")) == 1024
    with pytest.raises(ApiError):
        pagination.decode_cursor(utf8_ok, {})
    assert hmac_calls, "1024-byte UTF-8 cursor must pass the size check and reach HMAC"
    hmac_calls.clear()
    decode_calls.clear()
    utf8_bad = "é" * 512 + "x"  # 1025 bytes
    assert len(utf8_bad.encode("utf-8")) == 1025
    with pytest.raises(ApiError) as ei:
        pagination.decode_cursor(utf8_bad, {})
    assert ei.value.code == "invalid_cursor"
    assert hmac_calls == []
    assert decode_calls == []


@pytest.mark.parametrize("name,raw,expected", [
    ("CURSOR_MAX_AGE_S", "0", "min"),
    ("CURSOR_MAX_AGE_S", "-9", "min"),
    ("CURSOR_MAX_AGE_S", "999999999", "max"),
    ("CURSOR_KEY_VERSION", "0", "min"),
    ("CURSOR_KEY_VERSION", "-3", "min"),
    ("CURSOR_KEY_VERSION", "9999999999", "max"),
])
def test_cursor_env_clamped(name, raw, expected, monkeypatch, caplog):
    from config import (
        Config, CURSOR_MAX_AGE_S_MIN, CURSOR_MAX_AGE_S_MAX,
        CURSOR_KEY_VERSION_MIN, CURSOR_KEY_VERSION_MAX,
    )
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CURSOR_SECRET", "s" * 40)
    monkeypatch.setenv(name, raw)
    with caplog.at_level(logging.WARNING):
        Config().validate()
    cfg = Config()
    if name == "CURSOR_MAX_AGE_S":
        got = cfg.CURSOR_MAX_AGE_S
        bound = CURSOR_MAX_AGE_S_MIN if expected == "min" else CURSOR_MAX_AGE_S_MAX
    else:
        got = cfg.CURSOR_KEY_VERSION
        bound = CURSOR_KEY_VERSION_MIN if expected == "min" else CURSOR_KEY_VERSION_MAX
    assert got == bound
    text = caplog.text
    assert name in text
    assert "clamped" in text.lower()
    assert "s" * 40 not in text
    assert "token_urlsafe" not in text


@pytest.mark.parametrize("name,raw", [
    ("CURSOR_MAX_AGE_S", "abc"),
    ("CURSOR_MAX_AGE_S", "1.5"),
    ("CURSOR_KEY_VERSION", "abc"),
    ("CURSOR_KEY_VERSION", "1.5"),
])
def test_cursor_env_non_int_rejected(name, raw, monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CURSOR_SECRET", "s" * 40)
    monkeypatch.setenv(name, raw)
    from config import Config
    with pytest.raises(ValueError, match=name) as ei:
        Config().validate()
    assert "s" * 40 not in str(ei.value)


def test_key_version_rotation_rejects_old_cursor(monkeypatch):
    from pagination import encode_cursor, decode_cursor
    from errors import ApiError

    monkeypatch.setenv("CURSOR_KEY_VERSION", "4")
    cur = encode_cursor(["A", 1], {})
    monkeypatch.setenv("CURSOR_KEY_VERSION", "5")
    with pytest.raises(ApiError) as ei:
        decode_cursor(cur, {})
    assert ei.value.code == "invalid_cursor"
    assert ei.value.__cause__ is None


def test_invalid_cursor_error_is_generic_and_unchained():
    from pagination import decode_cursor
    from errors import ApiError

    with pytest.raises(ApiError) as ei:
        decode_cursor("!!!.x", {})
    err = ei.value
    assert err.code == "invalid_cursor"
    assert err.status == 400
    assert err.__cause__ is None
    assert err.__suppress_context__ is True
    blob = err.message + json.dumps(err.to_dict())
    assert "Traceback" not in blob
    assert "During handling" not in blob
    assert "hmac" not in blob.lower()


def test_api_400_body_has_no_internal_detail():
    app = _make_app()
    with app.test_client() as client:
        r = client.get("/api/players?cursor=!!!.x")
        if r.status_code == 404:
            pytest.skip("players route not on this layer")
        assert r.status_code == 400
        body = r.get_data(as_text=True)
        data = r.get_json()
        assert data["error"]["code"] == "invalid_cursor"
        assert "Traceback" not in body
        assert "During handling" not in body
        assert "hmac" not in body.lower()
        assert "File \"" not in body
