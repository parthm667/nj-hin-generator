from fastapi.testclient import TestClient
from app.main import app


def test_shared_history_and_delete_are_not_public_api():
    with TestClient(app) as client:
        assert client.get('/api/analysis/').status_code == 405
        assert client.delete('/api/analysis/1').status_code == 405


def test_oversized_request_is_rejected_before_database_access():
    with TestClient(app) as client:
        response = client.post('/api/analysis/', content=b'x' * 17000,
                               headers={'Content-Type': 'application/json'})
        assert response.status_code == 413


def test_production_requires_private_stable_rate_limit_key():
    from app.config import Settings
    import pytest
    with pytest.raises(ValueError):
        Settings(database_url='postgresql://localhost/example', app_environment='production', _env_file=None)
    configured = Settings(database_url='postgresql://localhost/example', app_environment='production',
                          anonymous_rate_limit_secret='x' * 32, _env_file=None)
    assert configured.app_environment == 'production'
