"""Eligibility evaluation. Default denied. Not an authz grant. Not media access."""

from __future__ import annotations

from decimal import Decimal
from math import isfinite
from typing import Any, Mapping, Optional

from db.bounds import CLIP_MAX_DURATION_S

REASON_FOOTAGE = "footage_not_matched"
REASON_TIMING = "video_timing_unavailable"
PROVENANCE_NONE = "none"
PROVENANCE_CLOCK = "game_clock_derived"
PROVENANCE_LEGACY = "unvalidated_numeric"
PROVENANCE_ATTESTED = "matcher_attested"
SOURCE_ORIGINAL = "original_private"
FIXTURE_PREFIX = "fixtures/"
LIVE_OBJECT_PREFIXES = ("games/", "clips/")

CAPABILITY_PUBLIC_FIELDS = frozenset({"can_create", "reason"})
ALLOWED_REASONS = frozenset({REASON_FOOTAGE, REASON_TIMING})


class CapabilityTypeError(ValueError):
    """Wrong type for a video-second field. No coercion is applied."""


def _as_decimal(value: Any, *, field: str) -> Decimal:
    """Accept int/float/Decimal only. Reject bool, str, None, NaN, Inf."""
    if isinstance(value, bool) or value is None:
        raise CapabilityTypeError(f"{field}: bool/None are not video seconds")
    if isinstance(value, str):
        raise CapabilityTypeError(f"{field}: strings are not video seconds")
    if isinstance(value, float):
        if not isfinite(value):
            raise CapabilityTypeError(f"{field}: non-finite")
        return Decimal(str(value))
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise CapabilityTypeError(f"{field}: non-finite")
        return value
    raise CapabilityTypeError(f"{field}: unsupported type {type(value).__name__}")


def parse_optional_seconds(value: Any, *, field: str) -> Optional[Decimal]:
    if value is None:
        return None
    return _as_decimal(value, field=field)


def original_private_source_ok(row: Mapping[str, Any]) -> bool:
    key = row.get("s3_key")
    kind = row.get("source_kind")
    version = row.get("source_version")
    if not isinstance(key, str) or not key.strip() or key != key.strip():
        return False
    lowered = key.lower()
    if lowered.startswith("http://") or lowered.startswith("https://") or lowered.startswith("s3://"):
        return False
    if kind != SOURCE_ORIGINAL:
        return False
    if not isinstance(version, str) or not version.strip() or version != version.strip():
        return False
    return True


def live_media_access_permitted(row: Mapping[str, Any]) -> bool:
    """Live bucket/object access. Independent of can_create. Default denied.

    Fictional rows and fixtures/ keys never grant live media, even if a later
    fixture runner is enabled and timing eligibility is true.
    """
    if row.get("is_fictional") is True:
        return False
    key = row.get("s3_key")
    if not isinstance(key, str) or not key.strip():
        return False
    if key.startswith(FIXTURE_PREFIX):
        return False
    lowered = key.lower()
    if lowered.startswith("http://") or lowered.startswith("https://") or lowered.startswith("s3://"):
        return False
    if any(key.startswith(prefix) for prefix in LIVE_OBJECT_PREFIXES):
        return original_private_source_ok(row)
    return False


def evaluate_capability(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return public capability. Ignores game_clock. Never echoes refs.

    can_create is timing/source-identity eligibility, not live media access.
    """
    if not original_private_source_ok(row):
        return {"can_create": False, "reason": REASON_FOOTAGE}

    provenance = row.get("timing_provenance") or PROVENANCE_NONE
    if provenance == PROVENANCE_NONE:
        return {"can_create": False, "reason": REASON_FOOTAGE}
    if provenance in (PROVENANCE_CLOCK, PROVENANCE_LEGACY):
        return {"can_create": False, "reason": REASON_TIMING}
    if provenance != PROVENANCE_ATTESTED:
        return {"can_create": False, "reason": REASON_TIMING}

    try:
        start = parse_optional_seconds(row.get("video_start_s"), field="video_start_s")
        end = parse_optional_seconds(row.get("video_end_s"), field="video_end_s")
        duration = parse_optional_seconds(row.get("duration_s"), field="duration_s")
    except CapabilityTypeError:
        return {"can_create": False, "reason": REASON_TIMING}

    matched = row.get("matched_source_version")
    current = row.get("source_version")
    if not isinstance(matched, str) or matched != current:
        return {"can_create": False, "reason": REASON_TIMING}

    if start is None or end is None or duration is None:
        return {"can_create": False, "reason": REASON_TIMING}
    if duration <= 0:
        return {"can_create": False, "reason": REASON_TIMING}
    if start < 0:
        return {"can_create": False, "reason": REASON_TIMING}
    if end <= start:
        return {"can_create": False, "reason": REASON_TIMING}
    if start >= duration or end > duration:
        return {"can_create": False, "reason": REASON_TIMING}
    if (end - start) > CLIP_MAX_DURATION_S:
        return {"can_create": False, "reason": REASON_TIMING}

    return {"can_create": True, "reason": None}


def public_capability(row: Mapping[str, Any]) -> dict[str, Any]:
    result = evaluate_capability(row)
    if set(result.keys()) != CAPABILITY_PUBLIC_FIELDS:
        raise RuntimeError("capability shape leaked extra fields")
    if result["can_create"] is True and result["reason"] is not None:
        raise RuntimeError("eligible capability must have null reason")
    if result["can_create"] is False and result["reason"] not in ALLOWED_REASONS:
        raise RuntimeError("ineligible capability must use a public reason")
    return result
