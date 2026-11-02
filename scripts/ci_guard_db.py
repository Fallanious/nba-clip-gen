#!/usr/bin/env python3
"""Refuse a CI DATABASE_URL that points at a prod or default Neon branch.

No network: when NEON_BRANCH_JSON is set, read that file instead of the API.
"""
from __future__ import annotations

import json
import os
import sys
from urllib.parse import urlparse


def _host_forbidden(host: str) -> bool:
    h = (host or "").lower()
    return "prod" in h


def _branch_forbidden(data: dict) -> bool:
    return bool(data.get("default") or data.get("primary"))


def main() -> int:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        # Secret missing: skip cleanly so pytest -m integration can no-op.
        print("DATABASE_URL unset; guard ok", file=sys.stderr)
        return 0

    host = urlparse(url).hostname or ""
    if _host_forbidden(host):
        print(f"refusing host {host!r}", file=sys.stderr)
        return 1

    json_path = os.environ.get("NEON_BRANCH_JSON")
    if json_path:
        with open(json_path, encoding="utf-8") as fh:
            data = json.load(fh)
        if _branch_forbidden(data):
            print("refusing default or primary Neon branch", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
