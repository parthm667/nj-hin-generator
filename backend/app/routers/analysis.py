"""
Analysis API router.

Endpoints for running HIN analysis and retrieving results.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session
from app.models.database import get_db
from app.models.tables import Analysis, Municipality
from app.models.schemas import (
    AnalysisCreate,
    AnalysisResponse,
    AnalysisDetail,
    AnalysisSummary,
    GeoJSONFeatureCollection
)
from app.services.hin_service import HINService
from app.services.crash_service import CrashService
from app.services.coverage_service import (
    AnalysisDataConflictError,
    CoverageService,
    MissingCrashDataError,
)
from app.services.pdf_service import PDFReportGenerator
from app.services.job_service import JobService, QueueFullError, ClientQuotaError
from app.config import settings
from app.services.data_quality import analysis_data_quality
from typing import List
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

router = APIRouter()


def _guard_completed_analysis_results(analysis: Analysis, db: Session) -> None:
    """Block completed results whose loaded crash records are no longer valid."""
    if analysis.status != "completed":
        return

    try:
        CoverageService(db).require_current_results(analysis)
    except AnalysisDataConflictError as exc:
        raise HTTPException(status_code=409, detail=exc.detail) from exc


@router.post("/", response_model=AnalysisResponse)
def create_analysis(
    request: AnalysisCreate,
    http_request: Request,
    db: Session = Depends(get_db)
):
    """
    Create and run a new HIN analysis.

    Args:
        request: Analysis configuration
        db: Database session

    Returns:
        Analysis object
    """
    # Validate municipality exists
    municipality = db.query(Municipality).filter(
        Municipality.muni_id == request.muni_id
    ).first()

    if not municipality:
        raise HTTPException(status_code=404, detail="Municipality not found")

    try:
        CoverageService(db).require_years_available(
            request.muni_id,
            request.config.start_year,
            request.config.end_year,
        )
    except MissingCrashDataError as exc:
        raise HTTPException(status_code=422, detail=exc.detail) from exc

    # Create analysis record
    analysis = Analysis(
        muni_id=request.muni_id,
        start_year=request.config.start_year,
        end_year=request.config.end_year,
        years_included=list(range(request.config.start_year, request.config.end_year + 1)),
        snap_distance_meters=request.config.snap_distance_meters,
        segment_length_miles=request.config.segment_length_miles,
        significance_threshold=request.config.significance_threshold,
        status='pending'
    )

    try:
        JobService(db).enqueue(
            analysis, http_request.state.anonymous_client_key,
            max_pending=settings.max_pending_analyses,
            max_client_active=settings.max_client_active_analyses,
        )
        db.commit()
        db.refresh(analysis)
    except (QueueFullError, ClientQuotaError) as exc:
        db.rollback()
        raise HTTPException(429, str(exc), headers={'Retry-After': '60'}) from exc

    logger.info(f"Created analysis {analysis.analysis_id} for municipality {request.muni_id}")

    return analysis


@router.get("/{analysis_id}", response_model=AnalysisDetail)
def get_analysis(
    analysis_id: int,
    db: Session = Depends(get_db)
):
    """
    Get details for a specific analysis.

    Args:
        analysis_id: Analysis ID
        db: Database session

    Returns:
        Analysis details
    """
    analysis = db.query(Analysis).filter(
        Analysis.analysis_id == analysis_id
    ).first()

    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")

    # Get municipality name
    municipality = db.query(Municipality).filter(
        Municipality.muni_id == analysis.muni_id
    ).first()
    assessment = CoverageService(db).assess_analysis(analysis)

    result = AnalysisDetail(
        analysis_id=analysis.analysis_id,
        muni_id=analysis.muni_id,
        created_at=analysis.created_at,
        updated_at=analysis.updated_at,
        start_year=analysis.start_year,
        end_year=analysis.end_year,
        status=analysis.status,
        error_message=analysis.error_message,
        total_crashes=analysis.total_crashes,
        total_fatalities=analysis.total_fatalities,
        total_injuries=analysis.total_injuries,
        hin_miles=analysis.hin_miles,
        hin_segment_count=analysis.hin_segment_count,
        municipality_name=municipality.name if municipality else None,
        data_status=assessment.data_status,
        data_message=assessment.data_message,
        available_years=assessment.available_years,
        missing_years=assessment.missing_years,
        input_version=analysis.input_version,
        data_quality=analysis_data_quality(db, analysis) if assessment.data_status == 'ready' and analysis.status == 'completed' else {},
    )

    return result


@router.get("/{analysis_id}/summary", response_model=AnalysisSummary)
def get_analysis_summary(
    analysis_id: int,
    db: Session = Depends(get_db)
):
    """
    Get summary statistics for an analysis.

    Args:
        analysis_id: Analysis ID
        db: Database session

    Returns:
        Summary statistics
    """
    analysis = db.query(Analysis).filter(
        Analysis.analysis_id == analysis_id
    ).first()

    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")

    if analysis.status != 'completed':
        raise HTTPException(
            status_code=400,
            detail=f"Analysis not completed (status: {analysis.status})"
        )
    _guard_completed_analysis_results(analysis, db)

    # Get crash statistics
    crash_service = CrashService(db)
    crash_summary = crash_service.get_municipality_crash_summary(
        analysis.muni_id,
        analysis.start_year,
        analysis.end_year
    )

    # Get HIN statistics
    from app.models.tables import HINSegment
    from sqlalchemy import and_

    hin_segments = db.query(HINSegment).filter(
        and_(
            HINSegment.analysis_id == analysis_id,
            HINSegment.is_significant == True
        )
    ).all()

    # Count corridors
    corridors = set(seg.corridor_id for seg in hin_segments if seg.corridor_id)

    # Calculate vulnerable tract percentage
    vulnerable_segments = [seg for seg in hin_segments if seg.in_vulnerable_tract]
    known_equity_segments = [seg for seg in hin_segments if seg.in_vulnerable_tract is not None]
    vulnerable_pct = (
        len(vulnerable_segments) / len(known_equity_segments) * 100
        if known_equity_segments else None
    )

    summary = AnalysisSummary(
        total_crashes=crash_summary['total_crashes'],
        fatal_crashes=crash_summary['fatal_crashes'],
        serious_injury_crashes=crash_summary['serious_injury_crashes'],
        minor_injury_crashes=crash_summary['minor_injury_crashes'],
        property_damage_crashes=crash_summary['property_damage_crashes'],
        ped_crashes=crash_summary['ped_crashes'],
        bike_crashes=crash_summary['bike_crashes'],
        injury_unknown_crashes=crash_summary['injury_unknown_crashes'],
        bike_involvement_unknown_crashes=crash_summary['bike_involvement_unknown_crashes'],
        total_killed=crash_summary['total_killed'],
        total_injured=crash_summary['total_injured'],
        pedestrians_killed=crash_summary['pedestrians_killed'],
        pedestrians_injured=crash_summary['pedestrians_injured'],
        casualty_counts_complete=crash_summary['casualty_counts_complete'],
        hin_miles=analysis.hin_miles or 0,
        hin_corridors=len(corridors),
        vulnerable_tract_percentage=vulnerable_pct
    )

    return summary


@router.get("/{analysis_id}/crashes")
def get_analysis_crashes(
    analysis_id: int,
    severity: str = None,
    ped_only: bool = False,
    bike_only: bool = False,
    db: Session = Depends(get_db)
):
    """
    Get crash points for an analysis as GeoJSON.

    Args:
        analysis_id: Analysis ID
        severity: Optional severity filter
        ped_only: Filter to pedestrian crashes
        bike_only: Filter to bicycle crashes
        db: Database session

    Returns:
        GeoJSON FeatureCollection
    """
    analysis = db.query(Analysis).filter(
        Analysis.analysis_id == analysis_id
    ).first()

    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")

    _guard_completed_analysis_results(analysis, db)

    crash_service = CrashService(db)
    if bike_only and not analysis_data_quality(db, analysis)['bicycle_data_available']:
        raise HTTPException(422, 'Bicycle involvement is unavailable for some selected records.')
    geojson = crash_service.get_crashes_geojson(
        muni_id=analysis.muni_id,
        start_year=analysis.start_year,
        end_year=analysis.end_year,
        severity=severity,
        ped_only=ped_only,
        bike_only=bike_only
    )

    return geojson


@router.get("/{analysis_id}/hin")
def get_analysis_hin(
    analysis_id: int,
    hin_type: str = 'general',
    db: Session = Depends(get_db)
):
    """
    Get HIN segments for an analysis as GeoJSON.

    Args:
        analysis_id: Analysis ID
        hin_type: Type of HIN (general, pedestrian, bicycle)
        db: Database session

    Returns:
        GeoJSON FeatureCollection
    """
    analysis = db.query(Analysis).filter(
        Analysis.analysis_id == analysis_id
    ).first()

    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")

    if analysis.status != 'completed':
        raise HTTPException(
            status_code=400,
            detail=f"Analysis not completed (status: {analysis.status})"
        )
    _guard_completed_analysis_results(analysis, db)

    if hin_type == 'bicycle' and not analysis_data_quality(db, analysis)['bicycle_data_available']:
        raise HTTPException(422, 'Bicycle involvement is unavailable for some selected records; bicycle HIN is not supported.')
    if hin_type not in ('general', 'pedestrian', 'bicycle'):
        raise HTTPException(422, 'Unknown HIN type')
    hin_service = HINService(db)
    geojson = hin_service.get_hin_geojson(
        analysis_id=analysis_id,
        hin_type=hin_type
    )

    return geojson


@router.get("/{analysis_id}/export/latex")
def export_analysis_latex(
    analysis_id: int,
    db: Session = Depends(get_db)
):
    """Download editable report text and its vector map as a ZIP archive."""
    analysis = db.query(Analysis).filter(Analysis.analysis_id == analysis_id).first()
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")
    if analysis.status != 'completed':
        raise HTTPException(status_code=400, detail=f"Analysis not completed (status: {analysis.status})")
    _guard_completed_analysis_results(analysis, db)
    try:
        source_archive = PDFReportGenerator(db).generate_latex_archive(analysis_id)
        filename = f"HIN_Analysis_{analysis_id}_{analysis.start_year}-{analysis.end_year}_LaTeX.zip"
        return Response(
            content=source_archive,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as exc:
        logger.error("Error generating LaTeX source for analysis %s: %s", analysis_id, exc)
        raise HTTPException(status_code=500, detail='Unable to generate the report source. Please try again later.') from exc


@router.get("/{analysis_id}/export/pdf")
def export_analysis_pdf(
    analysis_id: int,
    db: Session = Depends(get_db)
):
    """
    Export analysis results as PDF report.

    Args:
        analysis_id: Analysis ID
        db: Database session

    Returns:
        PDF file
    """
    analysis = db.query(Analysis).filter(
        Analysis.analysis_id == analysis_id
    ).first()

    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")

    if analysis.status != 'completed':
        raise HTTPException(
            status_code=400,
            detail=f"Analysis not completed (status: {analysis.status})"
        )
    _guard_completed_analysis_results(analysis, db)

    # Get municipality for filename
    municipality = db.query(Municipality).filter(
        Municipality.muni_id == analysis.muni_id
    ).first()

    try:
        # Generate PDF
        pdf_generator = PDFReportGenerator(db)
        pdf_bytes = pdf_generator.generate_report(analysis_id)

        # Create filename
        muni_name = municipality.name.replace(' ', '_') if municipality else 'Unknown'
        filename = f"HIN_Analysis_{muni_name}_{analysis.start_year}-{analysis.end_year}.pdf"

        logger.info(f"Generated PDF report for analysis {analysis_id}")

        # Return PDF
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename={filename}"
            }
        )

    except Exception as e:
        logger.error(f"Error generating PDF for analysis {analysis_id}: {e}")
        raise HTTPException(status_code=500, detail='Unable to generate the report. Please try again later.')
