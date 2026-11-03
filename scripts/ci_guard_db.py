#!/usr/bin/env python3
"""Fail closed: CI DATABASE_URL must target the expected Neon dev endpoint.

Requires NEON_DEV_ENDPOINT_ID (from GitHub vars.NEON_DEV_ENDPOINT_ID), the
endpoint id of Neon branch br-odd-smoke-b465tvf8. Refuses to run if that var
is unset or empty. If CI=true, NEON_DEV_REGION is required and must match the
host region. If CI is not true and NEON_DEV_REGION is unset, any Neon region
is accepted. If it is set to an empty string, refuse to run. If it is set to
a value, the host region label must match it (us-east-2, also aws-us-east-2).
Never prints credentials.

Accepted hosts (and the pooled variant; cell label is optional):
  ep-<id>[.c-<n>].<region>.aws.neon.tech
  ep-<id>-pooler[.c-<n>].<region>.aws.neon.tech
Lookalikes such as ep-<id>.evil.example are rejected.
A trailing-dot host is rejected. Matching is case-insensitive.
The pooled form uses a hyphenated ``-pooler`` suffix on the first label, not
a ``.pooler`` DNS label.

The URL is parsed with psycopg.conninfo.conninfo_to_dict so query overrides
cannot hide a production target. A port in the URL authority (for example
``:5433`` or ``:5432``) is rejected, as are query ``host``, ``hostaddr``,
``service``, ``options``, ``port``, ``user``, and ``dbname`` parameters,
and any duplicate query key. Only sslmode, channel_binding,
connect_timeout, and application_name are allowed as extra parameters.
sslmode must be require or verify-full. Host, endpoint, and region are
checked before sslmode.
"""
from __future__ import annotations

import os
import re
import sys
from urllib.parse import parse_qsl, urlparse

from psycopg.conninfo import conninfo_to_dict

# ep-<id>[-pooler].[c-<n>.]<region>.aws.neon.tech
# Cell digits are [0-9] so they stay ASCII even if re.ASCII is dropped.
# re.ASCII is still required for the region's trailing \d+.
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
_PG_SCHEMES = frozenset({"postgres", "postgresql"})


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
    """Return (endpoint_id, region) or None if the host is not an accepted Neon host.

    Matching is case-insensitive. A trailing-dot host is rejected (no rstrip).
    """
    host = (host or "").strip().lower()
    match = _NEON_HOST.fullmatch(host)
    if not match:
        return None
    return _endpoint_from_label(match.group("label")), match.group("region")


def _host_endpoint_id(host: str) -> str | None:
    parsed = _parse_neon_host(host)
    return None if parsed is None else parsed[0]


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def main() -> int:
    if "NEON_DEV_ENDPOINT_ID" not in os.environ:
        return _fail("NEON_DEV_ENDPOINT_ID is required")
    expected = os.environ.get("NEON_DEV_ENDPOINT_ID", "").strip().lower()
    if not expected:
        return _fail("NEON_DEV_ENDPOINT_ID is empty")

    if "DATABASE_URL" not in os.environ or not (os.environ.get("DATABASE_URL") or "").strip():
        return _fail("DATABASE_URL is empty")
    url = os.environ.get("DATABASE_URL") or ""

    try:
        parsed_url = urlparse(url)
    except Exception:
        return _fail("DATABASE_URL is invalid")
    scheme = (parsed_url.scheme or "").lower()
    if scheme and scheme not in _PG_SCHEMES:
        return _fail("DATABASE_URL scheme must be postgresql")

    try:
        info = conninfo_to_dict(url)
    except Exception:
        return _fail("DATABASE_URL is invalid")

    query_keys = [k.lower() for k, _ in parse_qsl(parsed_url.query, keep_blank_values=True)]
    if len(query_keys) != len(set(query_keys)):
        return _fail("DATABASE_URL query has duplicate keys")
    if set(query_keys) & _FORBIDDEN_OVERRIDES:
        return _fail("DATABASE_URL override parameters are not allowed")

    extra = set(k.lower() for k in info) - _IDENTITY_KEYS
    if extra - _ALLOWED_PARAMS:
        return _fail("DATABASE_URL parameters are not allowed")

    host = str(info.get("host") or "").lower()
    if not host:
        return _fail("DATABASE_URL host is required")

    parsed = _parse_neon_host(host)
    if parsed is None:
        return _fail("DATABASE_URL host is not an accepted Neon host")
    if parsed[0] != expected:
        return _fail("DATABASE_URL host does not match NEON_DEV_ENDPOINT_ID")

    in_ci = os.environ.get("CI") == "true"
    if in_ci and "NEON_DEV_REGION" not in os.environ:
        return _fail("NEON_DEV_REGION is required")
    if in_ci or "NEON_DEV_REGION" in os.environ:
        raw_region = os.environ.get("NEON_DEV_REGION", "")
        if not raw_region.strip():
            return _fail("NEON_DEV_REGION is empty")
        if _canonical_region(parsed[1]) != _canonical_region(raw_region):
            return _fail("DATABASE_URL host region does not match NEON_DEV_REGION")

    sslmode = str(info.get("sslmode") or "").strip().lower()
    if sslmode not in _ALLOWED_SSLMODES:
        return _fail("DATABASE_URL sslmode must be require or verify-full")
    return 0


if __name__ == "__main__":
    sys.exit(main())
