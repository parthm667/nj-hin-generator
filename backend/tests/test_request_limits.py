import importlib
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.requests import Request

from test_jobs import jobs_engine


def test_global_rejection_does_not_create_rotating_client_rows(jobs_engine):
    from app.services.api_safeguards import enforce_limits
    from fastapi import HTTPException
    with Session(jobs_engine) as db:
        db.execute(text("INSERT INTO request_limits VALUES ('read','global',clock_timestamp(),2400)"))
        db.commit()
        for index in range(3):
            incoming = Request({'type':'http','method':'GET','path':'/api/municipalities/',
                                'client':(f'192.0.2.{index}',1234),'headers':[]})
            with pytest.raises(HTTPException) as error:
                enforce_limits(incoming, db)
            assert error.value.status_code == 429
        assert db.scalar(text('SELECT COUNT(*) FROM request_limits')) == 1


def request(peer, forwarded="198.51.100.1"):
    return Request({"type": "http", "client": (peer, 1234), "headers": [(b"x-forwarded-for", forwarded.encode())]})


def test_client_key_ignores_forged_forwarding_and_changes_with_secret():
    assert importlib.util.find_spec("app.services.request_limits"), "Shared request limiter is missing"
    from app.services.request_limits import client_key_for_request
    first = client_key_for_request(request("192.0.2.1"), "secret-a")
    assert len(first) == 64 and "192.0.2.1" not in first
    assert first == client_key_for_request(request("192.0.2.1", "203.0.113.2"), "secret-a")
    assert first != client_key_for_request(request("192.0.2.2"), "secret-a")
    assert first != client_key_for_request(request("192.0.2.1"), "secret-b")
    with pytest.raises(ValueError):
        client_key_for_request(request("192.0.2.1"), "")


def test_shared_limit_allows_exact_capacity_and_resets_expired_window(jobs_engine):
    from app.services.request_limits import consume_request_limit, RequestLimitExceeded
    for _ in range(5):
        with Session(jobs_engine) as db:
            consume_request_limit(db, "a", "create", 5, 600)
            db.commit()
    with Session(jobs_engine) as db, pytest.raises(RequestLimitExceeded) as error:
        consume_request_limit(db, "a", "create", 5, 600)
    assert 1 <= error.value.retry_after <= 600
    with jobs_engine.begin() as connection:
        connection.execute(text("UPDATE request_limits SET window_start=clock_timestamp()-interval '601 seconds'"))
    with Session(jobs_engine) as db:
        consume_request_limit(db, "a", "create", 5, 600)
        db.commit()
    with jobs_engine.connect() as connection:
        assert connection.scalar(text("SELECT count FROM request_limits")) == 1


def test_rate_limit_is_atomic_and_independent_by_scope_and_client(jobs_engine):
    from app.services.request_limits import consume_request_limit, RequestLimitExceeded
    def attempt(_):
        with Session(jobs_engine) as db:
            try:
                consume_request_limit(db, "a", "export", 3, 60)
                db.commit()
                return True
            except RequestLimitExceeded:
                db.rollback()
                return False
    with ThreadPoolExecutor(max_workers=8) as executor:
        assert sum(executor.map(attempt, range(8))) == 3
    with Session(jobs_engine) as db:
        consume_request_limit(db, "a", "read", 3, 60)
        consume_request_limit(db, "b", "export", 3, 60)
        db.commit()
