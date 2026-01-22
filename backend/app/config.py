from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False
    )

    # Database
    database_url: str = "postgresql://user:password@localhost:5432/nj_hin_db"
    database_host: str = "localhost"
    database_port: int = 5432
    database_name: str = "nj_hin_db"
    database_user: str = "user"
    database_password: str = "password"

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_reload: bool = True
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

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
    debug: bool = True

    @property
    def cors_origins_list(self) -> List[str]:
        """Parse CORS origins from comma-separated string."""
        return [origin.strip() for origin in self.cors_origins.split(",")]

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
