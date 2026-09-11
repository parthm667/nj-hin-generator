#!/usr/bin/env python3
"""Enable PostGIS and create the application tables.

Run this one-off command before starting the API or loading data. The API does
not perform DDL during startup.
"""

import logging
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models import tables  # noqa: F401 - registers every model with Base
from app.models import jobs  # noqa: F401
from sqlalchemy import text

from app.models.database import Base, engine

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


ADDITIVE_MIGRATIONS = (
    "ALTER TABLE road_segments ADD COLUMN IF NOT EXISTS source_id varchar(200)",
    "ALTER TABLE road_segments ADD COLUMN IF NOT EXISTS sri varchar(20)",
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_segments_source_id "
        "ON road_segments (source_id)"
    ),
    "CREATE INDEX IF NOT EXISTS idx_segments_sri ON road_segments (sri)",
    (
        "CREATE INDEX IF NOT EXISTS idx_segments_geography "
        "ON road_segments USING gist ((geom::geography))"
    ),
)


def install_versions(connection):
    """Track source changes, excluding derived crash snapping assignments."""
    connection.execute(text('''CREATE TABLE IF NOT EXISTS dataset_revisions (
        dataset varchar(32) PRIMARY KEY, revision bigint NOT NULL DEFAULT 1
    )'''))
    for dataset in ('crashes', 'roads', 'boundaries', 'svi'):
        connection.execute(text('''INSERT INTO dataset_revisions(dataset)
            VALUES (:dataset) ON CONFLICT DO NOTHING'''), {'dataset': dataset})
    connection.execute(text('''CREATE OR REPLACE FUNCTION bump_dataset_revision()
        RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
          UPDATE dataset_revisions SET revision=revision+1 WHERE dataset=TG_ARGV[0];
          RETURN NULL;
        END $$'''))
    definitions = (
        ('crashes', 'crashes', 'external_id, crash_date, crash_time, severity, ped_involved, bike_involved, muni_id, geom, geocode_quality, road_name, route_number, total_killed, total_injured, pedestrians_killed, pedestrians_injured'),
        ('road_segments', 'roads', 'geom, length_miles, road_name, road_class, road_type, muni_id, sri, source_id'),
        ('municipalities', 'boundaries', 'geom, name, county, muni_code'),
        ('census_tracts', 'svi', 'geom, svi_percentile, svi_score'),
    )
    for table, dataset, columns in definitions:
        # All identifiers here are fixed application constants, never user input.
        for suffix, events in (('rows', 'INSERT OR DELETE OR TRUNCATE'), ('values', f'UPDATE OF {columns}')):
            name = f'version_{table}_{suffix}'
            connection.execute(text(f'DROP TRIGGER IF EXISTS {name} ON {table}'))
            connection.execute(text(f'''CREATE TRIGGER {name} AFTER {events} ON {table}
                FOR EACH STATEMENT EXECUTE FUNCTION bump_dataset_revision('{dataset}')'''))


CORRECTNESS_ADDITIONS = (
    'ALTER TABLE analyses ADD COLUMN IF NOT EXISTS input_version jsonb',
    'ALTER TABLE crashes ADD COLUMN IF NOT EXISTS total_killed integer CHECK (total_killed >= 0)',
    'ALTER TABLE crashes ADD COLUMN IF NOT EXISTS total_injured integer CHECK (total_injured >= 0)',
    'ALTER TABLE crashes ADD COLUMN IF NOT EXISTS pedestrians_killed integer CHECK (pedestrians_killed >= 0)',
    'ALTER TABLE crashes ADD COLUMN IF NOT EXISTS pedestrians_injured integer CHECK (pedestrians_injured >= 0)',
    'ALTER TABLE crashes ALTER COLUMN bike_involved DROP DEFAULT',
    'ALTER TABLE census_tracts ADD COLUMN IF NOT EXISTS source_year integer',
    'ALTER TABLE census_tracts ADD COLUMN IF NOT EXISTS source_name varchar(200)',
)


def upgrade_schema(connection=None) -> None:
    """Apply additions that ``create_all`` cannot add to existing tables."""
    if connection is None:
        with engine.begin() as connection:
            upgrade_schema(connection)
        return
    if connection is not None:
        connection.execute(text('''CREATE TABLE IF NOT EXISTS schema_revisions (
            revision integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now()
        )'''))
        for statement in ADDITIVE_MIGRATIONS:
            connection.execute(text(statement))
        for statement in CORRECTNESS_ADDITIONS:
            connection.execute(text(statement))
        install_versions(connection)
        connection.execute(text('''INSERT INTO schema_revisions(revision)
            VALUES (1), (2) ON CONFLICT DO NOTHING'''))


def initialize_engine(database_engine):
    """Serialize ALL DDL; refuse schema changes while a worker owns execution."""
    connection = database_engine.connect()
    try:
        # Initializers serialize separately, then acquire worker exclusion. A live
        # worker holds its session lock continuously, so fail with an operator action.
        connection.execute(text('SELECT pg_advisory_lock(1212763716,1)'))
        if not connection.scalar(text('SELECT pg_try_advisory_lock(1212763714,1)')):
            raise RuntimeError('Stop the analysis worker before applying schema upgrades')
        connection.execute(text('CREATE EXTENSION IF NOT EXISTS postgis'))
        Base.metadata.create_all(bind=connection)
        upgrade_schema(connection)
        connection.execute(text('''UPDATE analyses SET status='failed',
            error_message='Interrupted before durable queue upgrade; please rerun.'
            WHERE status IN ('pending','running') AND NOT EXISTS
            (SELECT 1 FROM analysis_jobs job WHERE job.analysis_id=analyses.analysis_id)'''))
        connection.commit()
    finally:
        # Physical close releases both session locks even when DDL fails.
        connection.invalidate()
        connection.close()


def main() -> int:
    logger.info("Applying serialized PostGIS schema initialization/upgrades")
    initialize_engine(engine)
    logger.info("Database schema initialized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
