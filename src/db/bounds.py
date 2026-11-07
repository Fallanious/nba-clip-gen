"""Security-required video-relative bound policy. Seconds. No hidden coercion."""

from decimal import Decimal

# Documented in src/db/sql/POLICY.md. SQL VIEW play_capability must match.
CLIP_MIN_DURATION_S = Decimal("0.5")
CLIP_MAX_DURATION_S = Decimal("120")
VIDEO_SECONDS_ABS_MAX = Decimal("1e9")
