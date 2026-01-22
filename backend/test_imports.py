#!/usr/bin/env python3
"""
Test script to verify all imports work correctly.
This tests the code without requiring a database connection.
"""

import sys
import os

# Add parent directory to path so backend module can be imported
backend_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(backend_dir)
sys.path.insert(0, parent_dir)

print("=" * 60)
print("Testing NJ HIN Generator Imports")
print("=" * 60)

# Test 1: Config
print("\n[1/6] Testing config...")
try:
    from backend.app.config import settings
    print(f"   ✓ Config loaded: {settings.app_name}")
except Exception as e:
    print(f"   ✗ Config failed: {e}")
    sys.exit(1)

# Test 2: Schemas (no DB required)
print("\n[2/6] Testing Pydantic schemas...")
try:
    from backend.app.models.schemas import (
        AnalysisConfig,
        MunicipalityResponse,
        CrashBase,
        AnalysisStatus
    )

    # Test year validation
    try:
        config = AnalysisConfig(start_year=2020, end_year=2019)
        print("   ✗ Year validation failed - should have raised error")
    except ValueError as e:
        print(f"   ✓ Year validation works: {str(e)}")

    # Test valid config
    config = AnalysisConfig(start_year=2017, end_year=2021)
    print(f"   ✓ Valid config created: {config.start_year}-{config.end_year}")

except Exception as e:
    print(f"   ✗ Schemas failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 3: Database models (may fail if DB not available)
print("\n[3/6] Testing database models...")
try:
    # Override database URL to use in-memory SQLite for testing
    os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
    from backend.app.models.tables import Municipality, RoadSegment, Crash
    print("   ✓ Database models imported")
except Exception as e:
    print(f"   ⚠ Database models import failed (expected if no DB): {e}")
    # This is OK for now

# Test 4: Main app structure
print("\n[4/6] Testing FastAPI app structure...")
try:
    # Don't actually import main as it will try to connect to DB
    # Just check the file exists and has correct structure
    main_path = os.path.join(backend_dir, 'app', 'main.py')
    with open(main_path, 'r') as f:
        content = f.read()
        if '@asynccontextmanager' in content:
            print("   ✓ FastAPI lifespan context manager found")
        else:
            print("   ✗ FastAPI still using deprecated on_event")

        if 'lifespan=lifespan' in content:
            print("   ✓ Lifespan parameter properly configured")
        else:
            print("   ✗ Lifespan parameter not configured")

except Exception as e:
    print(f"   ✗ Main app check failed: {e}")

# Test 5: Critical fixes applied
print("\n[5/6] Verifying critical fixes...")
try:
    # Check database.py for text() wrapper
    db_path = os.path.join(backend_dir, 'app', 'models', 'database.py')
    with open(db_path, 'r') as f:
        db_content = f.read()
        if 'from sqlalchemy import create_engine, text' in db_content:
            print("   ✓ SQLAlchemy text() import found")
        else:
            print("   ✗ Missing text() import in database.py")

        if 'with engine.begin() as conn:' in db_content:
            print("   ✓ Using engine.begin() for auto-commit")
        else:
            print("   ⚠ Not using engine.begin()")

    # Check hin_service.py for analysis_id parameter
    hin_path = os.path.join(backend_dir, 'app', 'services', 'hin_service.py')
    with open(hin_path, 'r') as f:
        hin_content = f.read()
        if 'analysis_id: int,' in hin_content:
            print("   ✓ HINService accepts analysis_id parameter")
        else:
            print("   ✗ HINService missing analysis_id parameter")

    # Check analysis.py for error handling
    analysis_path = os.path.join(backend_dir, 'app', 'routers', 'analysis.py')
    with open(analysis_path, 'r') as f:
        analysis_content = f.read()
        if "analysis.status = 'failed'" in analysis_content:
            print("   ✓ Background task error handling implemented")
        else:
            print("   ✗ Background task error handling missing")

except Exception as e:
    print(f"   ✗ Fix verification failed: {e}")

# Test 6: Import structure
print("\n[6/6] Testing import structure...")
try:
    # These should work without DB
    from backend.app import config
    print("   ✓ Config module imports correctly")

except Exception as e:
    print(f"   ✗ Import structure failed: {e}")

print("\n" + "=" * 60)
print("Import Tests Complete!")
print("=" * 60)
print("\nNote: Some tests may show warnings if PostgreSQL is not running.")
print("This is expected for import-only testing.")
print("\nNext steps:")
print("1. Install PostgreSQL with PostGIS")
print("2. Create database and run migrations")
print("3. Test with: uvicorn app.main:app --reload")
