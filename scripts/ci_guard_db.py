#!/usr/bin/env python3
"""Fail closed: CI DATABASE_URL must target the expected Neon dev endpoint.

Requires NEON_DEV_ENDPOINT_ID (from GitHub vars.NEON_DEV_ENDPOINT_ID), the
endpoint id of Neon branch br-odd-smoke-b465tvf8. Refuses to run if that var
is unset or does not match the URL host. Never prints credentials.
"""
from __future__ import annotations

import os
import sys
from urllib.parse import urlparse


def _endpoint_id(host: str) -> str:
    """First DNS label of a Neon host is the endpoint id (ep-...)."""
    return (host or "").split(".")[0].lower()


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

    got = _endpoint_id(host)
    if got != expected and not host.startswith(expected + "."):
        print("DATABASE_URL host does not match NEON_DEV_ENDPOINT_ID", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
