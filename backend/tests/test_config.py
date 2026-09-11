import pytest
from pydantic import ValidationError

from app.config import Settings


def test_database_url_is_required(monkeypatch):
    monkeypatch.delenv("DATABASE_URL")

    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None)


def test_legacy_postgres_url_is_normalized_for_sqlalchemy():
    settings = Settings(
        database_url="postgres://user:password@db.example.test/hin",
        _env_file=None,
    )

    assert settings.database_url == "postgresql://user:password@db.example.test/hin"


def test_production_sensitive_flags_default_to_disabled():
    settings = Settings(
        database_url="postgresql://user:password@db.example.test/hin",
        _env_file=None,
    )

    assert settings.debug is False
    assert settings.api_reload is False
    assert settings.cors_origin_regex is None


@pytest.mark.parametrize(
    ("cors_origins", "cors_origin_regex"),
    [("*", None), ("", ".*"), ("", "^.*$")],
)
def test_wildcard_cors_is_rejected_when_credentials_are_allowed(
    cors_origins,
    cors_origin_regex,
):
    with pytest.raises(ValidationError, match="wildcard"):
        Settings(
            database_url="postgresql://user:password@db.example.test/hin",
            cors_origins=cors_origins,
            cors_origin_regex=cors_origin_regex,
            cors_allow_credentials=True,
            _env_file=None,
        )


def test_empty_cors_entries_are_ignored():
    settings = Settings(
        database_url="postgresql://user:password@db.example.test/hin",
        cors_origins="https://one.example, ,https://two.example",
        _env_file=None,
    )

    assert settings.cors_origins_list == [
        "https://one.example",
        "https://two.example",
    ]
