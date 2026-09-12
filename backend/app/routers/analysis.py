"""
Analysis API router.

Endpoints for running HIN analysis and retrieving results.
"""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import Response
from sqlalchemy.orm import Session
from backend.app.models.database import get_db
from backend.app.models.tables import Analysis, Municipality
from backend.app.models.schemas import (
    AnalysisCreate,
    AnalysisResponse,
    AnalysisDetail,
    AnalysisSummary,
    GeoJSONFeatureCollection
)
from backend.app.services.hin_service import HINService
from backend.app.services.crash_service import CrashService
from backend.app.services.pdf_service import PDFReportGenerator
from typing import List
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

router = APIRouter()


def run_analysis_background(
    analysis_id: int,
    muni_id: int,
    start_year: int,
    end_year: int,
    snap_distance_meters: float,
    significance_threshold: float
):
    """
    Background task to run analysis.

    Args:
        analysis_id: Analysis ID
        muni_id: Municipality ID
        start_year: Start year
        end_year: End year
        snap_distance_meters: Crash snap distance
        significance_threshold: Significance threshold
    """
    from backend.app.models.database import SessionLocal
    import traceback

    db = SessionLocal()
    try:
        hin_service = HINService(db)
        hin_service.run_analysis(
            analysis_id=analysis_id,
            muni_id=muni_id,
            start_year=start_year,
            end_year=end_year,
            snap_distance_meters=snap_distance_meters,
            significance_threshold=significance_threshold
        )
        logger.info(f"Analysis {analysis_id} completed successfully")

    except Exception as e:
        logger.error(f"Analysis {analysis_id} failed: {e}")
        logger.error(traceback.format_exc())

        # Update analysis status to failed
        try:
            analysis = db.query(Analysis).filter(
                Analysis.analysis_id == analysis_id
            ).first()

            if analysis:
                analysis.status = 'failed'
                analysis.error_message = str(e)[:500]
                db.commit()
        except Exception as update_error:
            logger.error(f"Failed to update analysis status: {update_error}")
            db.rollback()

    finally:
        db.close()


@router.post("", response_model=AnalysisResponse)
async def create_analysis(
    request: AnalysisCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Create and run a new HIN analysis.

    Args:
        request: Analysis configuration
        background_tasks: FastAPI background tasks
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

    db.add(analysis)
    db.commit()
    db.refresh(analysis)

    # Queue analysis as background task
    background_tasks.add_task(
        run_analysis_background,
        analysis.analysis_id,
        request.muni_id,
        request.config.start_year,
        request.config.end_year,
        request.config.snap_distance_meters,
        request.config.significance_threshold
    )

    logger.info(f"Created analysis {analysis.analysis_id} for municipality {request.muni_id}")

    return analysis


@router.get("", response_model=List[AnalysisResponse])
async def list_analyses(
    muni_id: int = None,
    status: str = None,
    db: Session = Depends(get_db)
):
    """
    List all analyses.

    Args:
        muni_id: Optional filter by municipality
        status: Optional filter by status
        db: Database session

    Returns:
        List of analyses
    """
    query = db.query(Analysis)

    if muni_id:
        query = query.filter(Analysis.muni_id == muni_id)

    if status:
        query = query.filter(Analysis.status == status)

    analyses = query.order_by(Analysis.created_at.desc()).all()

    return analyses


@router.get("/{analysis_id}", response_model=AnalysisDetail)
async def get_analysis(
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
        municipality_name=municipality.name if municipality else None
    )

    return result


@router.get("/{analysis_id}/summary", response_model=AnalysisSummary)
async def get_analysis_summary(
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

    # Get crash statistics
    crash_service = CrashService(db)
    crash_summary = crash_service.get_municipality_crash_summary(
        analysis.muni_id,
        analysis.start_year,
        analysis.end_year
    )

    # Get HIN statistics
    from backend.app.models.tables import HINSegment
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
    vulnerable_pct = (
        len(vulnerable_segments) / len(hin_segments) * 100
        if hin_segments else 0
    )

    summary = AnalysisSummary(
        total_crashes=crash_summary['total_crashes'],
        fatal_crashes=crash_summary['fatal_crashes'],
        serious_injury_crashes=crash_summary['serious_injury_crashes'],
        minor_injury_crashes=crash_summary['minor_injury_crashes'],
        property_damage_crashes=crash_summary['property_damage_crashes'],
        ped_crashes=crash_summary['ped_crashes'],
        bike_crashes=crash_summary['bike_crashes'],
        hin_miles=analysis.hin_miles or 0,
        hin_corridors=len(corridors),
        vulnerable_tract_percentage=vulnerable_pct
    )

    return summary


@router.get("/{analysis_id}/crashes")
async def get_analysis_crashes(
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

    crash_service = CrashService(db)
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
async def get_analysis_hin(
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

    hin_service = HINService(db)
    geojson = hin_service.get_hin_geojson(
        analysis_id=analysis_id,
        hin_type=hin_type
    )

    return geojson


@router.delete("/{analysis_id}")
async def delete_analysis(
    analysis_id: int,
    db: Session = Depends(get_db)
):
    """
    Delete an analysis.

    Args:
        analysis_id: Analysis ID
        db: Database session

    Returns:
        Success message
    """
    analysis = db.query(Analysis).filter(
        Analysis.analysis_id == analysis_id
    ).first()

    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")

    # Delete associated HIN segments first
    from backend.app.models.tables import HINSegment

    db.query(HINSegment).filter(
        HINSegment.analysis_id == analysis_id
    ).delete()

    # Delete analysis
    db.delete(analysis)
    db.commit()

    logger.info(f"Deleted analysis {analysis_id}")

    return {"message": "Analysis deleted successfully"}


@router.get("/{analysis_id}/export/pdf")
async def export_analysis_pdf(
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
        raise HTTPException(status_code=500, detail=f"Error generating PDF: {str(e)}")
