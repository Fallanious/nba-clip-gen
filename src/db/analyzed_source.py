"""Bind matcher provenance to exact local bytes consumed. No S3, no DB lookup."""

from __future__ import annotations

import hashlib
import os
from typing import Any, Mapping

from db.capability import FIXTURE_PREFIX

IMMUTABLE_IDENTITY_KINDS = frozenset({"content_sha256", "object_version_id"})


class AnalyzedSourceError(ValueError):
    pass


def hash_local_file(path: str) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    stat = os.stat(path)
    mtime_ns = getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))
    return digest.hexdigest(), size, int(mtime_ns)


def capture_local_analyzed_source(
    *,
    path: str,
    key: str,
    matcher_run_id: str,
) -> dict[str, Any]:
    if not isinstance(key, str) or not key.strip() or key != key.strip():
        raise AnalyzedSourceError("analyzed source key must be a nonempty canonical token")
    if not key.startswith(FIXTURE_PREFIX):
        raise AnalyzedSourceError("this slice allows fixtures/ keys only")
    if not isinstance(matcher_run_id, str) or not matcher_run_id.strip() or matcher_run_id != matcher_run_id.strip():
        raise AnalyzedSourceError("matcher_run_id must be a nonempty canonical token")
    digest, size, mtime_ns = hash_local_file(path)
    return {
        "analyzed_source_key": key,
        "analyzed_identity_kind": "content_sha256",
        "analyzed_source_identity": f"sha256:{digest}",
        "input_sha256": digest,
        "input_size": size,
        "input_mtime_ns": mtime_ns,
        "matcher_run_id": matcher_run_id,
    }


def assert_local_input_unmutated(path: str, captured: Mapping[str, Any]) -> None:
    digest, size, _mtime_ns = hash_local_file(path)
    if digest != captured.get("input_sha256") or size != captured.get("input_size"):
        raise AnalyzedSourceError("local input mutated after analysis")


def matcher_output_document(
    *,
    plays: list[Any],
    analyzed_source: Mapping[str, Any],
    matcher_version: str,
) -> dict[str, Any]:
    return {
        "analyzed_source": dict(analyzed_source),
        "matcher_run_id": analyzed_source["matcher_run_id"],
        "matcher_version": matcher_version,
        "plays": list(plays),
    }
