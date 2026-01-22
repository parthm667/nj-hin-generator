"""
Municipalities API router.

Endpoints for listing and retrieving municipality information.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from backend.app.models.database import get_db
from backend.app.models.tables import Municipality
from backend.app.models.schemas import MunicipalityResponse, MunicipalityDetail
from typing import List
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/", response_model=List[MunicipalityResponse])
async def list_municipalities(
    county: str = None,
    db: Session = Depends(get_db)
):
    """
    List all municipalities.

    Args:
        county: Optional filter by county name
        db: Database session

    Returns:
        List of municipalities
    """
    query = db.query(Municipality)

    if county:
        query = query.filter(Municipality.county.ilike(f"%{county}%"))

    municipalities = query.order_by(Municipality.name).all()

    return municipalities


@router.get("/{muni_id}", response_model=MunicipalityDetail)
async def get_municipality(
    muni_id: int,
    db: Session = Depends(get_db)
):
    """
    Get details for a specific municipality.

    Args:
        muni_id: Municipality ID
        db: Database session

    Returns:
        Municipality details
    """
    municipality = db.query(Municipality).filter(
        Municipality.muni_id == muni_id
    ).first()

    if not municipality:
        raise HTTPException(status_code=404, detail="Municipality not found")

    # Add statistics
    # TODO: Calculate actual statistics from crashes and segments
    result = MunicipalityDetail(
        muni_id=municipality.muni_id,
        name=municipality.name,
        county=municipality.county,
        muni_code=municipality.muni_code,
        total_crashes=0,  # Placeholder
        total_fatalities=0,
        total_injuries=0,
        road_miles=0.0,
        latest_analysis=None
    )

    return result


@router.get("/{muni_id}/summary")
async def get_municipality_summary(
    muni_id: int,
    db: Session = Depends(get_db)
):
    """
    Get summary statistics for a municipality.

    Args:
        muni_id: Municipality ID
        db: Database session

    Returns:
        Summary statistics
    """
    municipality = db.query(Municipality).filter(
        Municipality.muni_id == muni_id
    ).first()

    if not municipality:
        raise HTTPException(status_code=404, detail="Municipality not found")

    # Calculate statistics
    from backend.app.services.crash_service import CrashService
    from datetime import datetime

    crash_service = CrashService(db)
    current_year = datetime.now().year

    summary = crash_service.get_municipality_crash_summary(
        muni_id,
        current_year - 5,
        current_year - 1
    )

    summary['municipality_name'] = municipality.name
    summary['county'] = municipality.county

    return summary
