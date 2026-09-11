"""
Municipalities API router.

Endpoints for listing and retrieving municipality information.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.models.database import get_db
from app.models.tables import Analysis, Municipality, RoadSegment
from app.models.schemas import (
    MunicipalityCoverageResponse,
    MunicipalityDetail,
    MunicipalityResponse,
)
from app.services.coverage_service import CoverageService
from typing import List, Optional
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

SUMMARY_COVERAGE_NOTE = (
    "Counts include loaded, usable records only; available years and gaps do not "
    "certify source completeness."
)


def _empty_crash_summary():
    return {
        'total_crashes': 0,
        'fatal_crashes': 0,
        'serious_injury_crashes': 0,
        'minor_injury_crashes': 0,
        'injury_unknown_crashes': 0,
        'property_damage_crashes': 0,
        'ped_crashes': 0,
        'bike_crashes': 0,
        'bike_involvement_unknown_crashes': 0,
        'total_killed': None,
        'total_injured': None,
        'pedestrians_killed': None,
        'pedestrians_injured': None,
        'casualty_counts_complete': False,
        'crashes_with_segments': 0,
    }


@router.get("/", response_model=List[MunicipalityResponse])
def list_municipalities(
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
def get_municipality(
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

    from app.services.crash_service import CrashService

    coverage = CoverageService(db).get_municipality_coverage(muni_id)
    if coverage.available_years:
        crash_summary = CrashService(db).get_municipality_crash_summary(
            muni_id,
            coverage.available_years[0],
            coverage.available_years[-1],
        )
    else:
        crash_summary = _empty_crash_summary()

    road_miles = db.query(func.coalesce(func.sum(RoadSegment.length_miles), 0.0)).filter(
        RoadSegment.muni_id == muni_id
    ).scalar()
    latest_analysis = db.query(func.max(Analysis.created_at)).filter(
        Analysis.muni_id == muni_id
    ).scalar()

    result = MunicipalityDetail(
        muni_id=municipality.muni_id,
        name=municipality.name,
        county=municipality.county,
        muni_code=municipality.muni_code,
        total_crashes=crash_summary['total_crashes'],
        total_fatalities=crash_summary['total_killed'],
        total_injuries=crash_summary['total_injured'],
        road_miles=float(road_miles),
        latest_analysis=latest_analysis,
    )

    return result


@router.get("/{muni_id}/coverage", response_model=MunicipalityCoverageResponse)
def get_municipality_coverage(
    muni_id: int,
    db: Session = Depends(get_db),
):
    """Return positive loaded crash-record counts by year."""
    municipality = db.query(Municipality).filter(
        Municipality.muni_id == muni_id
    ).first()
    if not municipality:
        raise HTTPException(status_code=404, detail="Municipality not found")

    return CoverageService(db).get_municipality_coverage(muni_id).as_response()


@router.get("/{muni_id}/summary")
def get_municipality_summary(
    muni_id: int,
    start_year: Optional[int] = Query(default=None, ge=1, le=9998),
    end_year: Optional[int] = Query(default=None, ge=1, le=9998),
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

    from app.services.crash_service import CrashService

    if (start_year is None) != (end_year is None):
        raise HTTPException(
            422,
            'Provide both start_year and end_year, or omit both to use loaded years.',
        )
    if start_year is not None and start_year > end_year:
        raise HTTPException(422, 'start_year must be less than or equal to end_year')

    coverage = CoverageService(db).get_municipality_coverage(muni_id)
    if start_year is None and coverage.available_years:
        start_year = coverage.available_years[0]
        end_year = coverage.available_years[-1]

    if start_year is None:
        summary = _empty_crash_summary()
        missing_years = []
    else:
        summary = CrashService(db).get_municipality_crash_summary(
            muni_id,
            start_year,
            end_year,
        )
        missing_years = coverage.missing_years(start_year, end_year)
        if summary['total_crashes'] == 0:
            summary.update({
                'total_killed': None,
                'total_injured': None,
                'pedestrians_killed': None,
                'pedestrians_injured': None,
                'casualty_counts_complete': False,
            })

    summary['municipality_name'] = municipality.name
    summary['county'] = municipality.county
    summary['start_year'] = start_year
    summary['end_year'] = end_year
    summary['available_years'] = coverage.available_years
    summary['missing_years'] = missing_years
    summary['coverage_note'] = SUMMARY_COVERAGE_NOTE

    return summary
