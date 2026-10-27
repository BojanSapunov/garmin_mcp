"""Application settings, read from environment variables (and .env locally)."""

from datetime import date
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"  # local | production
    database_url: str = "postgresql+psycopg://garmin:garmin@localhost:5433/garmin"
    # In Azure the connection comes in parts, because the password is a Key Vault reference.
    # When POSTGRES_HOST is set these take precedence over DATABASE_URL.
    postgres_host: str = ""
    postgres_user: str = ""
    postgres_password: str = ""
    postgres_db: str = "garmin"

    garmin_api_key: str = ""
    garmin_key_expires: date | None = None  # shown on the dashboard so the key gets renewed in time

    google_client_id: str = ""
    google_client_secret: str = ""
    allowed_email: str = ""  # the single user allowed to sign in
    session_secret: str = "dev-only-change-me"

    # Skip Google sign-in when running locally before the OAuth client exists. Refused outside local.
    auth_dev_bypass: bool = False

    @property
    def is_local(self) -> bool:
        return self.app_env == "local"

    @property
    def sqlalchemy_url(self) -> str | URL:
        if not self.postgres_host:
            return self.database_url
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password,  # URL.create handles escaping
            host=self.postgres_host,
            port=5432,
            database=self.postgres_db,
            query={"sslmode": "require"},
        )

    def validate_for_startup(self) -> None:
        if not self.is_local:
            missing = [n for n in ("google_client_id", "google_client_secret", "allowed_email", "garmin_api_key")
                       if not getattr(self, n)]
            if missing:
                raise RuntimeError(f"missing settings: {', '.join(missing)}")
            if self.auth_dev_bypass:
                raise RuntimeError("AUTH_DEV_BYPASS is only allowed when APP_ENV=local")
            if self.session_secret == "dev-only-change-me" or len(self.session_secret) < 32:
                raise RuntimeError("SESSION_SECRET must be a random string of at least 32 characters")


@lru_cache
def get_settings() -> Settings:
    return Settings()
