"""Application configuration loaded from environment variables."""

import os
from typing import Optional
from dotenv import load_dotenv

load_dotenv()


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
            # Allow dev fallback only in dev environment
            if self.APP_ENV == "dev":
                return "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"  # exactly 32 bytes
            raise ValueError("CURSOR_SECRET environment variable is required (no dev fallback outside APP_ENV=dev)")
        return secret

    @property
    def APP_ENV(self) -> str:
        return os.getenv("APP_ENV", "dev")

    @property
    def DEBUG(self) -> bool:
        """Debug mode flag - defaults to off, forced off in production."""
        if self.APP_ENV == "production":
            return False
        return os.getenv("DEBUG", "false").lower() == "true"

    @property
    def OLLAMA_URL(self) -> str:
        return os.getenv("OLLAMA_URL", "http://localhost:11434")

    def validate(self):
        """Validate required configuration at startup."""
        errors = []

        # Validate CURSOR_SECRET length
        if len(self.CURSOR_SECRET.encode('utf-8')) < 32:
            errors.append("CURSOR_SECRET must be at least 32 bytes")

        if self.PLAYBACK_URL_TTL < 60 or self.PLAYBACK_URL_TTL > 3600:
            errors.append("PLAYBACK_URL_TTL must be 60-3600")

        if self.PLAYBACK_FALLBACK_TTL < 60 or self.PLAYBACK_FALLBACK_TTL > 3600:
            errors.append("PLAYBACK_FALLBACK_TTL must be 60-3600")

        if errors:
            raise ValueError(f"Configuration errors: {', '.join(errors)}")


config = Config()
