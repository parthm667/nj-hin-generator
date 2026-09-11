from typing import List, Optional, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False
    )

    # Database
    database_url: str
    database_host: str = "localhost"
    database_port: int = 5432
    database_name: str = "nj_hin_db"
    database_user: str = "user"
    database_password: str = "password"

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_reload: bool = False
    cors_origins: str = "http://localhost:3000,http://localhost:5173"
    cors_origin_regex: Optional[str] = None
    cors_allow_credentials: bool = True
    app_environment: Literal['local', 'production'] = 'local'
    anonymous_rate_limit_secret: str = 'local-development-only-rate-key'
    max_pending_analyses: int = Field(default=20, ge=1, le=100)
    max_client_active_analyses: int = Field(default=2, ge=1, le=10)
    analysis_create_limit: int = Field(default=5, ge=1, le=100)
    analysis_create_window_seconds: int = Field(default=600, ge=1, le=86400)
    export_request_limit: int = Field(default=20, ge=1, le=100)
    export_window_seconds: int = Field(default=60, ge=1, le=86400)
    worker_max_attempts: int = Field(default=3, ge=1, le=3)
    worker_statement_timeout_ms: int = Field(default=300000, ge=1000, le=900000)
    worker_lock_timeout_ms: int = Field(default=5000, ge=100, le=30000)
    worker_job_timeout_seconds: int = Field(default=900, ge=15, le=3600)

    # Data Sources
    nj_crash_data_url: str = ""
    census_api_key: str = ""

    # Analysis Configuration
    default_analysis_years: int = 5
    crash_snap_distance_meters: float = 50.0
    segment_length_miles: float = 0.1
    significance_threshold: float = 0.05

    # Severity Weights
    weight_fatal: int = 10
    weight_serious_injury: int = 5
    weight_minor_injury: int = 3
    weight_property_damage: int = 1

    # Application
    app_name: str = "NJ High Injury Network Generator"
    app_version: str = "0.1.0"
    debug: bool = False

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: object) -> object:
        """Normalize provider-style PostgreSQL URLs for SQLAlchemy."""
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("DATABASE_URL cannot be empty")
            if value.startswith("postgres://"):
                return f"postgresql://{value.removeprefix('postgres://')}"
        return value

    @model_validator(mode="after")
    def validate_anonymous_production_settings(self) -> "Settings":
        if self.app_environment == 'production':
            if len(self.anonymous_rate_limit_secret) < 32 or self.anonymous_rate_limit_secret.startswith('local-development'):
                raise ValueError('Production requires a private ANONYMOUS_RATE_LIMIT_SECRET of at least 32 characters')
            if self.debug:
                raise ValueError('DEBUG must be false in production')
        return self

    @model_validator(mode="after")
    def reject_credentialed_wildcard_cors(self) -> "Settings":
        """Browsers cannot safely combine wildcard origins and credentials."""
        if not self.cors_allow_credentials:
            return self

        origins = self.cors_origins_list
        broad_regexes = {"*", ".*", "^.*$"}
        if "*" in origins or (
            self.cors_origin_regex
            and self.cors_origin_regex.strip() in broad_regexes
        ):
            raise ValueError("CORS wildcard origins cannot allow credentials")
        return self

    @property
    def cors_origins_list(self) -> List[str]:
        """Parse CORS origins from comma-separated string."""
        return [
            origin.strip()
            for origin in self.cors_origins.split(",")
            if origin.strip()
        ]

    @property
    def severity_weights(self) -> dict:
        """Return severity weights as a dictionary."""
        return {
            "fatal": self.weight_fatal,
            "serious_injury": self.weight_serious_injury,
            "minor_injury": self.weight_minor_injury,
            "property_damage": self.weight_property_damage
        }


settings = Settings()
