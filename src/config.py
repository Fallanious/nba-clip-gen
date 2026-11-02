"""Application configuration loaded from environment variables."""

import logging
import os
import re
import secrets
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Explicit allow-list. Missing, empty, whitespace, or unknown values (e.g. "Prod ", "qa")
# refuse to start. "production"/"staging" stay accepted as the repo's existing set.
KNOWN_APP_ENVS = frozenset({"dev", "test", "prod", "production", "staging"})
PRODUCTION_ENVS = frozenset({"prod", "production"})

CURSOR_KEY_VERSION_MIN = 1
CURSOR_KEY_VERSION_MAX = 2_147_483_647
CURSOR_KEY_VERSION_DEFAULT = 1
CURSOR_MAX_AGE_S_MIN = 1
CURSOR_MAX_AGE_S_MAX = 604_800  # 7 days
CURSOR_MAX_AGE_S_DEFAULT = 86_400

_INT_RE = re.compile(r"[+-]?\d+$")
# Per-process random secret for APP_ENV=dev when CURSOR_SECRET is unset. Not a public constant.
_DEV_CURSOR_SECRET = secrets.token_urlsafe(32)


def _clamp_int_env(name: str, default: int, lo: int, hi: int) -> int:
    """Parse an int env var; reject non-integers; clamp out-of-range with a warning.

    The warning never includes CURSOR_SECRET or any credential.
    """
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    text = str(raw).strip()
    if not _INT_RE.fullmatch(text):
        raise ValueError(f"{name} must be an integer")
    value = int(text)
    if value < lo or value > hi:
        clamped = max(lo, min(hi, value))
        logger.warning(
            "%s out of range [%s, %s]; clamped to %s",
            name,
            lo,
            hi,
            clamped,
        )
        return clamped
    return value


class Config:
    """Application configuration - reads env vars dynamically for test compatibility."""

    @property
    def DATABASE_URL(self) -> str:
        return os.getenv("DATABASE_URL", "")

    @property
    def ALLOWED_ORIGINS(self) -> list[str]:
        return [
            origin.strip()
            for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:5001").split(",")
            if origin.strip()
        ]

    @property
    def AWS_REGION(self) -> str:
        return os.getenv("AWS_REGION", "us-east-2")

    @property
    def S3_BUCKET(self) -> Optional[str]:
        return os.getenv("S3_BUCKET")

    @property
    def S3_CLIPS_PREFIX(self) -> str:
        return os.getenv("S3_CLIPS_PREFIX", "clips/")

    @property
    def S3_GAMES_PREFIX(self) -> str:
        return os.getenv("S3_GAMES_PREFIX", "games/")

    @property
    def PLAYBACK_URL_TTL(self) -> int:
        return int(os.getenv("PLAYBACK_URL_TTL", "300"))

    @property
    def PLAYBACK_FALLBACK_TTL(self) -> int:
        return int(os.getenv("PLAYBACK_FALLBACK_TTL", "900"))

    @property
    def PLAYBACK_ALLOW_FALLBACK(self) -> bool:
        return os.getenv("PLAYBACK_ALLOW_FALLBACK", "true").lower() == "true"

    @property
    def PLAYBACK_VERIFY_OBJECT(self) -> bool:
        return os.getenv("PLAYBACK_VERIFY_OBJECT", "true").lower() == "true"

    @property
    def CURSOR_SECRET(self) -> str:
        """Required secret for cursor signing - must be at least 32 bytes."""
        secret = os.getenv("CURSOR_SECRET")
        if not secret:
            if self.APP_ENV == "dev":
                return _DEV_CURSOR_SECRET
            raise ValueError(
                "CURSOR_SECRET environment variable is required "
                "(no dev fallback outside APP_ENV=dev)"
            )
        return secret

    @property
    def APP_ENV(self) -> str:
        raw = os.getenv("APP_ENV")
        allowed = ", ".join(sorted(KNOWN_APP_ENVS))
        if raw is None or raw not in KNOWN_APP_ENVS:
            raise ValueError(
                "APP_ENV is required and must be one of: " + allowed
            )
        return raw

    @property
    def DEBUG(self) -> bool:
        """Debug mode flag - defaults to off, forced off in production."""
        if self.APP_ENV in PRODUCTION_ENVS:
            return False
        return os.getenv("DEBUG", "false").lower() == "true"

    @property
    def OLLAMA_URL(self) -> str:
        return os.getenv("OLLAMA_URL", "http://localhost:11434")

    @property
    def CURSOR_KEY_VERSION(self) -> int:
        return _clamp_int_env(
            "CURSOR_KEY_VERSION",
            CURSOR_KEY_VERSION_DEFAULT,
            CURSOR_KEY_VERSION_MIN,
            CURSOR_KEY_VERSION_MAX,
        )

    @property
    def CURSOR_MAX_AGE_S(self) -> int:
        return _clamp_int_env(
            "CURSOR_MAX_AGE_S",
            CURSOR_MAX_AGE_S_DEFAULT,
            CURSOR_MAX_AGE_S_MIN,
            CURSOR_MAX_AGE_S_MAX,
        )

    def validate(self):
        """Validate required configuration at startup."""
        errors = []

        # Fail closed on missing/unknown APP_ENV before touching secrets.
        try:
            env = self.APP_ENV
        except ValueError:
            raise

        if env != "dev" and not os.getenv("CURSOR_SECRET"):
            errors.append("CURSOR_SECRET is required when APP_ENV is not dev")
        else:
            try:
                secret = self.CURSOR_SECRET
            except ValueError as e:
                errors.append(str(e))
            else:
                if len(secret.encode("utf-8")) < 32:
                    errors.append("CURSOR_SECRET must be at least 32 bytes")

        try:
            self.CURSOR_KEY_VERSION
        except ValueError as e:
            errors.append(str(e))

        try:
            self.CURSOR_MAX_AGE_S
        except ValueError as e:
            errors.append(str(e))

        if self.PLAYBACK_URL_TTL < 60 or self.PLAYBACK_URL_TTL > 3600:
            errors.append("PLAYBACK_URL_TTL must be 60-3600")

        if self.PLAYBACK_FALLBACK_TTL < 60 or self.PLAYBACK_FALLBACK_TTL > 3600:
            errors.append("PLAYBACK_FALLBACK_TTL must be 60-3600")

        if errors:
            raise ValueError(f"Configuration errors: {', '.join(errors)}")


config = Config()
