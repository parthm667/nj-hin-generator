# Critical Fixes Required Before Testing

This document outlines the **4 critical bugs** that must be fixed before running the application.

---

## Fix 1: SQLAlchemy 2.0 Compatibility in database.py

**File**: `backend/app/models/database.py`

**Current Code** (Lines 45-52):
```python
def init_postgis():
    """Initialize PostGIS extension in the database."""
    with engine.connect() as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
        conn.commit()
```

**Fixed Code**:
```python
from sqlalchemy import text

def init_postgis():
    """Initialize PostGIS extension in the database."""
    with engine.begin() as conn:  # Use begin() for auto-commit
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis;"))
```

**Why**: SQLAlchemy 2.0 requires raw SQL to be wrapped in `text()`, and `begin()` provides automatic transaction commit.

---

## Fix 2: Duplicate Analysis Creation in analysis.py

**File**: `backend/app/routers/analysis.py`

**Problem**: The router creates an Analysis record, then the background task calls `hin_service.run_analysis()` which creates ANOTHER Analysis record.

**Solution 1** (Recommended): Modify HINService to accept existing analysis_id

**File**: `backend/app/services/hin_service.py`

Change the `run_analysis` signature:
```python
def run_analysis(
    self,
    analysis_id: int,  # ✅ Add this parameter
    muni_id: int,
    start_year: int,
    end_year: int,
    snap_distance_meters: float = None,
    significance_threshold: float = None
) -> Analysis:
    """Run complete HIN analysis for a municipality."""
    logger.info(f"Starting HIN analysis for municipality {muni_id}")

    # Use defaults if not provided
    if snap_distance_meters is None:
        snap_distance_meters = settings.crash_snap_distance_meters
    if significance_threshold is None:
        significance_threshold = settings.significance_threshold

    # ✅ Look up existing analysis instead of creating new
    analysis = self.db.query(Analysis).filter(
        Analysis.analysis_id == analysis_id
    ).first()

    if not analysis:
        raise ValueError(f"Analysis {analysis_id} not found")

    # Update status to running
    analysis.status = 'running'
    self.db.commit()

    try:
        # ... rest of the function remains the same
```

Then update the background task call in `analysis.py`:
```python
background_tasks.add_task(
    run_analysis_background,
    analysis.analysis_id,  # ✅ Pass the ID
    request.muni_id,
    request.config.start_year,
    request.config.end_year,
    request.config.snap_distance_meters,
    request.config.significance_threshold
)
```

---

## Fix 3: Background Task Error Handling

**File**: `backend/app/routers/analysis.py`

**Current Code** (Lines 29-64):
```python
def run_analysis_background(...):
    from backend.app.models.database import SessionLocal

    db = SessionLocal()
    try:
        hin_service = HINService(db)
        hin_service.run_analysis(...)
        logger.info(f"Analysis {analysis_id} completed")
    except Exception as e:
        logger.error(f"Analysis {analysis_id} failed: {e}")
        # ❌ Status never updated!
    finally:
        db.close()
```

**Fixed Code**:
```python
def run_analysis_background(
    analysis_id: int,
    muni_id: int,
    start_year: int,
    end_year: int,
    snap_distance_meters: float,
    significance_threshold: float
):
    """Background task to run analysis."""
    from backend.app.models.database import SessionLocal
    from backend.app.models.tables import Analysis
    import traceback

    db = SessionLocal()
    try:
        hin_service = HINService(db)
        hin_service.run_analysis(
            analysis_id=analysis_id,  # ✅ Pass the ID
            muni_id=muni_id,
            start_year=start_year,
            end_year=end_year,
            snap_distance_meters=snap_distance_meters,
            significance_threshold=significance_threshold
        )
        logger.info(f"Analysis {analysis_id} completed successfully")

    except Exception as e:
        # ✅ Update analysis status to failed
        logger.error(f"Analysis {analysis_id} failed: {e}")
        logger.error(traceback.format_exc())

        try:
            analysis = db.query(Analysis).filter(
                Analysis.analysis_id == analysis_id
            ).first()

            if analysis:
                analysis.status = 'failed'
                analysis.error_message = str(e)[:500]  # Truncate long errors
                db.commit()
        except Exception as update_error:
            logger.error(f"Failed to update analysis status: {update_error}")
            db.rollback()

    finally:
        db.close()
```

---

## Fix 4: FastAPI Lifecycle Events

**File**: `backend/app/main.py`

**Current Code** (Lines 39-55):
```python
@app.on_event("startup")  # ❌ Deprecated
async def startup_event():
    logger.info("Starting NJ HIN Generator API...")
    try:
        init_db()
        logger.info("Database initialized")
    except Exception as e:
        logger.error(f"Database initialization error: {e}")

@app.on_event("shutdown")  # ❌ Deprecated
async def shutdown_event():
    logger.info("Shutting down NJ HIN Generator API...")
```

**Fixed Code**:
```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan."""
    # Startup
    logger.info("Starting NJ HIN Generator API...")
    try:
        init_db()
        logger.info("Database initialized")
    except Exception as e:
        logger.error(f"Database initialization error: {e}")
        raise  # ✅ Prevent startup if DB fails

    yield

    # Shutdown
    logger.info("Shutting down NJ HIN Generator API...")

# Create FastAPI app
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Automated High Injury Network identification for New Jersey municipalities",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan  # ✅ Add lifespan parameter
)
```

---

## Additional Quick Wins (Optional but Recommended)

### Fix 5: Add Year Validation in schemas.py

**File**: `backend/app/models/schemas.py`

Add a validator to AnalysisConfig:
```python
from pydantic import model_validator

class AnalysisConfig(BaseModel):
    """Configuration for running an analysis."""
    start_year: int = Field(..., ge=2000, le=2030)
    end_year: int = Field(..., ge=2000, le=2030)
    snap_distance_meters: float = Field(default=50.0, ge=10, le=200)
    segment_length_miles: float = Field(default=0.1, ge=0.05, le=1.0)
    significance_threshold: float = Field(default=0.05, ge=0.01, le=0.2)

    @model_validator(mode='after')
    def validate_years(self):
        """Ensure start_year <= end_year."""
        if self.start_year > self.end_year:
            raise ValueError("start_year must be less than or equal to end_year")
        return self
```

### Fix 6: Use Proper Geometry Parsing in crash_service.py

**File**: `backend/app/services/crash_service.py`

Replace lines 242-245:
```python
# ❌ Current fragile parsing
coords_str = geom_wkt.split('POINT(')[1].rstrip(')')
lon, lat = map(float, coords_str.split())
```

With:
```python
# ✅ Use Shapely
from shapely import wkt as shapely_wkt

geom = shapely_wkt.loads(crash.geom)
lon, lat = geom.x, geom.y
```

---

## Testing These Fixes

After applying all fixes:

1. **Test database initialization**:
```bash
cd backend
python -c "from app.models.database import init_postgis, init_db; init_postgis(); init_db()"
```

2. **Test API startup**:
```bash
uvicorn app.main:app --reload
```

3. **Test analysis creation**:
```bash
curl -X POST http://localhost:8000/api/analysis \
  -H "Content-Type: application/json" \
  -d '{
    "muni_id": 1,
    "config": {
      "start_year": 2017,
      "end_year": 2021
    }
  }'
```

4. **Check analysis status**:
```bash
curl http://localhost:8000/api/analysis/1
```

---

## Checklist

- [ ] Fix 1: SQLAlchemy text() wrapper applied
- [ ] Fix 2: HINService accepts analysis_id parameter
- [ ] Fix 3: Background task updates failed status
- [ ] Fix 4: FastAPI lifespan context manager
- [ ] Tested database initialization
- [ ] Tested API startup
- [ ] Tested analysis creation
- [ ] Verified error handling works

---

## Next Steps After Fixes

1. Set up Alembic for database migrations
2. Add health check that actually pings database
3. Write tests for critical paths
4. Add transaction boundaries to analysis service
5. Implement proper geometry parsing with Shapely

These fixes address the **blocking issues** that would prevent the application from working at all. The other issues in CODE_REVIEW.md can be addressed incrementally.
