"""
Main FastAPI application for NJ HIN Generator.
"""

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from contextlib import asynccontextmanager
from app.config import settings
from app.routers import municipalities, analysis, export
from app.models.database import get_db
from app.services.api_safeguards import RequestBodyLimit, enforce_limits
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan."""
    logger.info("Starting NJ HIN Generator API...")
    try:
        yield
    finally:
        logger.info("Shutting down NJ HIN Generator API...")


# Create FastAPI app
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Automated High Injury Network identification for New Jersey municipalities",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestBodyLimit)


# Health check endpoint
@app.get("/")
async def root():
    """Root endpoint - health check."""
    return {
        "status": "healthy",
        "app": settings.app_name,
        "version": settings.app_version
    }


@app.get("/health")
async def health_check(db: Session = Depends(get_db)):
    """Detailed health check."""
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.warning("Database health check failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable",
        )

    return {
        "status": "healthy",
        "version": settings.app_version,
        "database": "connected"
    }


# Include routers
app.include_router(
    municipalities.router,
    prefix="/api/municipalities",
    tags=["municipalities"], dependencies=[Depends(enforce_limits)]
)

app.include_router(
    analysis.router,
    prefix="/api/analysis",
    tags=["analysis"], dependencies=[Depends(enforce_limits)]
)

app.include_router(
    export.router,
    prefix="/api/export",
    tags=["export"], dependencies=[Depends(enforce_limits)]
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.api_reload
    )
