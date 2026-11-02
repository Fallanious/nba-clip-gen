#!/usr/bin/env python3
"""Fail closed: CI DATABASE_URL must target the expected Neon dev endpoint.

Requires NEON_DEV_ENDPOINT_ID (from GitHub vars.NEON_DEV_ENDPOINT_ID), the
endpoint id of Neon branch br-odd-smoke-b465tvf8. Refuses to run if that var
is unset or does not match the URL host. Never prints credentials.

Accepted hosts (and the pooled variant):
  ep-<id>.<region>.aws.neon.tech
  ep-<id>-pooler.<region>.aws.neon.tech
Lookalikes such as ep-<id>.evil.example are rejected.
"""
from __future__ import annotations

import os
import re
import sys
from urllib.parse import urlparse

# ep-<id>[-pooler].<region>.aws.neon.tech  (region like us-east-1)
_NEON_HOST = re.compile(
    r"^"
    r"(?P<label>ep-[a-z0-9-]+)"
    r"\.(?P<region>[a-z]{2}-[a-z0-9]+-\d+)"
    r"\.aws\.neon\.tech"
    r"$"
)


def _endpoint_from_label(label: str) -> str:
    """Map compute or pooled first-label to the endpoint id."""
    if label.endswith("-pooler"):
        return label[: -len("-pooler")]
    return label


def _host_endpoint_id(host: str) -> str | None:
    host = (host or "").lower().rstrip(".")
    match = _NEON_HOST.fullmatch(host)
    if not match:
        return None
    return _endpoint_from_label(match.group("label"))


def main() -> int:
    expected = (os.environ.get("NEON_DEV_ENDPOINT_ID") or "").strip().lower()
    if not expected:
        print("NEON_DEV_ENDPOINT_ID is required", file=sys.stderr)
        return 1

    url = os.environ.get("DATABASE_URL") or ""
    if not url:
        print("DATABASE_URL is required", file=sys.stderr)
        return 1

    host = (urlparse(url).hostname or "").lower()
    if not host:
        print("DATABASE_URL host is required", file=sys.stderr)
        return 1

    got = _host_endpoint_id(host)
    if got is None or got != expected:
        print("DATABASE_URL host does not match NEON_DEV_ENDPOINT_ID", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
