from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker
from geoalchemy2 import Geometry
from app.config import settings

# Create SQLAlchemy engine
engine = create_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=5,
    pool_timeout=10,
    connect_args={
        'connect_timeout': 10,
        'options': '-c statement_timeout=30000 -c lock_timeout=5000',
    },
)

# Create SessionLocal class
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create Base class
Base = declarative_base()


def get_db():
    """
    Dependency function to get database session.

    Yields:
        Database session
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """
    Initialize database tables.
    Called explicitly by scripts/init_schema.py, not during API startup.
    """
    Base.metadata.create_all(bind=engine)


def init_postgis():
    """
    Initialize PostGIS extension in the database.
    Must be run with appropriate database privileges.
    """
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis;"))
