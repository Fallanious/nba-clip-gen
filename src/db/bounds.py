"""Security-required video-relative bound policy. Seconds. No hidden coercion."""

from decimal import Decimal

# Documented in src/db/sql/POLICY.md. SQL VIEW play_capability must match.
# No minimum clip length: any tiny positive window (end > start) is allowed.
CLIP_MAX_DURATION_S = Decimal("120")
