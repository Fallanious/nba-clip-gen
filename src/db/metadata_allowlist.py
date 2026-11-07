"""Recursive canaries: public metadata must not carry keys, URLs, or internal refs."""

from __future__ import annotations

from typing import Any

from db.capability import CAPABILITY_PUBLIC_FIELDS

BROWSE_METADATA_FIELDS = frozenset({
    "play_id",
    "game_id",
    "game_date",
    "play_index",
    "period",
    "game_clock",
    "description",
    "action_type",
    "shot_subtype",
    "outcome",
    "can_create",
    "reason",
})

FORBIDDEN_KEY_SUBSTR = (
    "s3",
    "url",
    "uri",
    "bucket",
    "presign",
    "etag",
    "source_version",
    "matched_source",
    "matcher_run",
    "video_start",
    "video_end",
    "video_timestamp",
    "password",
    "secret",
    "token",
    "dsn",
    "database_url",
)

FORBIDDEN_VALUE_PREFIXES = (
    "http://",
    "https://",
    "s3://",
    "postgresql://",
    "postgres://",
)


class MetadataLeak(ValueError):
    pass


def _key_forbidden(key: str) -> bool:
    lowered = key.lower()
    if lowered in {"play_id", "game_id"}:
        return False
    return any(part in lowered for part in FORBIDDEN_KEY_SUBSTR)


def assert_no_private_payload(value: Any, *, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, inner in value.items():
            if not isinstance(key, str) or _key_forbidden(key):
                raise MetadataLeak(f"forbidden key at {path}.{key!r}")
            if isinstance(inner, str) and inner.lower().startswith(FORBIDDEN_VALUE_PREFIXES):
                raise MetadataLeak(f"forbidden value at {path}.{key}")
            assert_no_private_payload(inner, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for i, inner in enumerate(value):
            assert_no_private_payload(inner, path=f"{path}[{i}]")
        return
    if isinstance(value, str) and value.lower().startswith(FORBIDDEN_VALUE_PREFIXES):
        raise MetadataLeak(f"forbidden value at {path}")


def assert_browse_row(row: dict[str, Any]) -> None:
    extra = set(row) - BROWSE_METADATA_FIELDS
    if extra:
        raise MetadataLeak(f"browse row extra fields: {sorted(extra)}")
    assert_no_private_payload(row)


def assert_capability_row(row: dict[str, Any]) -> None:
    extra = set(row) - CAPABILITY_PUBLIC_FIELDS - {"play_id"}
    if extra:
        raise MetadataLeak(f"capability extra fields: {sorted(extra)}")
    assert_no_private_payload({k: v for k, v in row.items() if k != "play_id"})
