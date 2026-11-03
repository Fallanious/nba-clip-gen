#!/usr/bin/env python3
"""Fail closed: CI DATABASE_URL must target the expected Neon dev endpoint.

Requires NEON_DEV_ENDPOINT_ID (from GitHub vars.NEON_DEV_ENDPOINT_ID), the
endpoint id of Neon branch br-odd-smoke-b465tvf8. Refuses to run if that var
is unset or does not match the URL host. If NEON_DEV_REGION (vars.NEON_DEV_REGION)
is set, the host region label must match it (us-east-2, also aws-us-east-2);
if unset, any Neon region is accepted.
Never prints credentials.

Accepted hosts (and the pooled variant; cell label is optional):
  ep-<id>[.c-<n>].<region>.aws.neon.tech
  ep-<id>-pooler[.c-<n>].<region>.aws.neon.tech
Lookalikes such as ep-<id>.evil.example are rejected.
The pooled form uses a hyphenated ``-pooler`` suffix on the first label, not
a ``.pooler`` DNS label.

The URL is parsed with psycopg.conninfo.conninfo_to_dict so query overrides
cannot hide a production target. A port in the URL authority (for example
``:5433`` or ``:5432``) is rejected, as are query ``host``, ``hostaddr``,
``service``, ``options``, ``port``, ``user``, and ``dbname`` parameters,
and any duplicate query key. Only sslmode, channel_binding,
connect_timeout, and application_name are allowed as extra parameters.
sslmode must be require or verify-full.
"""
from __future__ import annotations

import os
import re
import sys
from urllib.parse import parse_qsl, urlparse

from psycopg.conninfo import conninfo_to_dict

# ep-<id>[-pooler].[c-<n>.]<region>.aws.neon.tech
_NEON_HOST = re.compile(
    r"^"
    r"(?P<label>ep-[a-z0-9-]+)"
    r"(?:\.(?P<cell>c-[1-9][0-9]{0,3}))?"
    r"\.(?P<region>[a-z]{2}-[a-z0-9]+-\d+)"
    r"\.aws\.neon\.tech"
    r"$",
    re.ASCII,
)
_POOLER_SUFFIX = "-pooler"
_ALLOWED_PARAMS = frozenset({
    "sslmode",
    "channel_binding",
    "connect_timeout",
    "application_name",
})
_IDENTITY_KEYS = frozenset({"user", "password", "dbname", "host"})
_FORBIDDEN_OVERRIDES = frozenset({
    "host",
    "hostaddr",
    "service",
    "options",
    "port",
    "user",
    "dbname",
})
_ALLOWED_SSLMODES = frozenset({"require", "verify-full"})


def _endpoint_from_label(label: str) -> str:
    """Map compute or pooled first-label to the endpoint id.

    Only a hyphenated ``-pooler`` suffix is stripped (``ep-<id>-pooler``).
    A ``.pooler`` DNS label is not accepted.
    """
    if label.endswith(_POOLER_SUFFIX) and len(label) > len(_POOLER_SUFFIX):
        return label[: -len(_POOLER_SUFFIX)]
    return label


def _canonical_region(value: str) -> str:
    """Region label only: us-east-2, also accepting aws-us-east-2."""
    region = (value or "").strip().lower()
    if region.startswith("aws-"):
        return region[len("aws-"):]
    return region


def _parse_neon_host(host: str) -> tuple[str, str] | None:
    """Return (endpoint_id, region) or None if the host is not an accepted Neon host."""
    host = (host or "").lower().rstrip(".")
    match = _NEON_HOST.fullmatch(host)
    if not match:
        return None
    return _endpoint_from_label(match.group("label")), match.group("region")


def _host_endpoint_id(host: str) -> str | None:
    parsed = _parse_neon_host(host)
    return None if parsed is None else parsed[0]


def _query_key_list(url: str) -> list[str]:
    parsed = urlparse(url)
    if not parsed.scheme:
        return []
    return [k.lower() for k, _ in parse_qsl(parsed.query, keep_blank_values=True)]


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def main() -> int:
    expected = (os.environ.get("NEON_DEV_ENDPOINT_ID") or "").strip().lower()
    if not expected:
        return _fail("NEON_DEV_ENDPOINT_ID is required")

    url = os.environ.get("DATABASE_URL") or ""
    if not url:
        return _fail("DATABASE_URL is required")

    try:
        info = conninfo_to_dict(url)
    except Exception:
        return _fail("DATABASE_URL is invalid")

    query_keys = _query_key_list(url)
    if len(query_keys) != len(set(query_keys)):
        return _fail("DATABASE_URL parameters are not allowed")
    if set(query_keys) & _FORBIDDEN_OVERRIDES:
        return _fail("DATABASE_URL parameters are not allowed")

    extra = set(k.lower() for k in info) - _IDENTITY_KEYS
    if extra - _ALLOWED_PARAMS:
        return _fail("DATABASE_URL parameters are not allowed")

    sslmode = str(info.get("sslmode") or "").strip().lower()
    if sslmode not in _ALLOWED_SSLMODES:
        return _fail("DATABASE_URL sslmode must be require or verify-full")

    host = str(info.get("host") or "").lower()
    if not host:
        return _fail("DATABASE_URL host is required")

    parsed = _parse_neon_host(host)
    if parsed is None or parsed[0] != expected:
        return _fail("DATABASE_URL host does not match NEON_DEV_ENDPOINT_ID")

    expected_region = (os.environ.get("NEON_DEV_REGION") or "").strip().lower()
    if expected_region and _canonical_region(parsed[1]) != _canonical_region(expected_region):
        return _fail("DATABASE_URL host region does not match NEON_DEV_REGION")
    return 0


if __name__ == "__main__":
    sys.exit(main())
