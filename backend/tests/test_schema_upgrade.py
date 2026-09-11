"""Initializer concurrency is exercised against a fresh disposable database."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from scripts.init_schema import initialize_engine


@pytest.fixture
def fresh_schema_engine():
    configured = os.getenv('TEST_DATABASE_URL')
    if not configured:
        pytest.skip('TEST_DATABASE_URL is not configured')
    url = make_url(configured)
    assert 'test' in url.database.lower()
    name = 'nj_hin_schema_test_' + uuid.uuid4().hex
    admin = create_engine(url.set(database='postgres'), isolation_level='AUTOCOMMIT')
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_engine(url.set(database=name))
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as conn:
            # Exact name was generated/created above; no existing database is used.
            conn.execute(text(f'DROP DATABASE "{name}"'))
        admin.dispose()


def test_parallel_initializers_serialize_before_any_ddl(fresh_schema_engine):
    engine = fresh_schema_engine
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: initialize_engine(engine), range(2)))
    with engine.connect() as conn:
        assert conn.scalar(text('SELECT COUNT(*) FROM schema_revisions')) == 2
        assert conn.scalar(text('SELECT COUNT(*) FROM dataset_revisions')) == 4
        assert conn.scalar(text('SELECT COUNT(*) FROM analysis_jobs')) == 0


def test_initializer_refuses_active_worker_before_creating_tables(fresh_schema_engine):
    engine = fresh_schema_engine
    with engine.connect() as owner:
        owner.execute(text('SELECT pg_advisory_lock(1212763714,1)'))
        try:
            with pytest.raises(RuntimeError, match='Stop the analysis worker'):
                initialize_engine(engine)
            assert owner.scalar(text("SELECT to_regclass('public.analyses')")) is None
        finally:
            owner.execute(text('SELECT pg_advisory_unlock(1212763714,1)'))
