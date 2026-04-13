"""Application configuration loaded from environment / .env file."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)


class Settings:
    # Use DATABASE_URL from env; fall back to local dev Postgres
    _raw_db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql://app:P@ssw0rd!2026@db.internal:5432/ecommerce",
    )
    DATABASE_URL: str = _raw_db_url if _raw_db_url.startswith("sqlite") else "sqlite:///./dev.db"
    STRIPE_KEY: str = os.environ.get("STRIPE_KEY", "sk_live_4eC39HqLyjWDarjtT1zdp7dc")
    JWT_SECRET: str = os.environ.get("JWT_SECRET", "dev-secret-change-me")
    SENTRY_DSN: str = os.environ.get("SENTRY_DSN", "")
    APP_ENV: str = os.environ.get("APP_ENV", "development")
    CORS_ORIGINS: list[str] = os.environ.get("CORS_ORIGINS", "*").split(",")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    RATE_LIMIT_PER_MINUTE: int = 60


settings = Settings()
