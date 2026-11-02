"""Application configuration loaded from environment variables."""

import os
from typing import Optional
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Application configuration."""

    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")

    # CORS
    ALLOWED_ORIGINS: list[str] = [
        origin.strip()
        for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:5001").split(",")
        if origin.strip()
    ]

    # AWS / S3
    AWS_REGION: str = os.getenv("AWS_REGION", "us-east-2")
    S3_BUCKET: Optional[str] = os.getenv("S3_BUCKET")
    S3_CLIPS_PREFIX: str = os.getenv("S3_CLIPS_PREFIX", "clips/")
    S3_GAMES_PREFIX: str = os.getenv("S3_GAMES_PREFIX", "games/")

    # Playback
    PLAYBACK_URL_TTL: int = int(os.getenv("PLAYBACK_URL_TTL", "300"))
    PLAYBACK_FALLBACK_TTL: int = int(os.getenv("PLAYBACK_FALLBACK_TTL", "900"))
    PLAYBACK_ALLOW_FALLBACK: bool = os.getenv("PLAYBACK_ALLOW_FALLBACK", "true").lower() == "true"
    PLAYBACK_VERIFY_OBJECT: bool = os.getenv("PLAYBACK_VERIFY_OBJECT", "true").lower() == "true"

    # Security
    CURSOR_SECRET: str = os.getenv("CURSOR_SECRET", "dev-secret-change-in-production")

    # App
    APP_ENV: str = os.getenv("APP_ENV", "dev")

    # Ollama (existing)
    OLLAMA_URL: str = os.getenv("OLLAMA_URL", "http://localhost:11434")

    @classmethod
    def validate(cls):
        """Validate required configuration at startup."""
        errors = []

        if cls.PLAYBACK_URL_TTL < 60 or cls.PLAYBACK_URL_TTL > 3600:
            errors.append("PLAYBACK_URL_TTL must be 60-3600")

        if cls.PLAYBACK_FALLBACK_TTL < 60 or cls.PLAYBACK_FALLBACK_TTL > 3600:
            errors.append("PLAYBACK_FALLBACK_TTL must be 60-3600")

        if errors:
            raise ValueError(f"Configuration errors: {', '.join(errors)}")


config = Config()
