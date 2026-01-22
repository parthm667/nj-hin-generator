"""
Export API router.

Endpoints for generating and downloading reports.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session
from backend.app.models.database import get_db
from backend.app.models.tables import Analysis
from backend.app.models.schemas import ReportRequest
import logging
import io

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/{analysis_id}/pdf")
async def generate_pdf_report(
    analysis_id: int,
    request: ReportRequest = None,
    db: Session = Depends(get_db)
):
    """
    Generate PDF report for an analysis.

    Args:
        analysis_id: Analysis ID
        request: Report configuration
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

    # TODO: Implement PDF generation
    # This is a placeholder that would call report_service.py

    logger.info(f"Generating PDF report for analysis {analysis_id}")

    # Placeholder response
    raise HTTPException(
        status_code=501,
        detail="PDF generation not yet implemented. See backend/app/services/report_service.py"
    )


@router.get("/{analysis_id}/csv")
async def export_csv(
    analysis_id: int,
    data_type: str = 'crashes',
    db: Session = Depends(get_db)
):
    """
    Export data as CSV.

    Args:
        analysis_id: Analysis ID
        data_type: Type of data to export (crashes, hin_segments, corridors)
        db: Database session

    Returns:
        CSV file
    """
    analysis = db.query(Analysis).filter(
        Analysis.analysis_id == analysis_id
    ).first()

    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")

    # TODO: Implement CSV export
    # This is a placeholder

    logger.info(f"Exporting {data_type} CSV for analysis {analysis_id}")

    raise HTTPException(
        status_code=501,
        detail="CSV export not yet implemented"
    )


@router.get("/{analysis_id}/geojson")
async def export_geojson(
    analysis_id: int,
    layer: str = 'hin',
    db: Session = Depends(get_db)
):
    """
    Export spatial data as GeoJSON file.

    Args:
        analysis_id: Analysis ID
        layer: Layer to export (hin, crashes, corridors)
        db: Database session

    Returns:
        GeoJSON file
    """
    analysis = db.query(Analysis).filter(
        Analysis.analysis_id == analysis_id
    ).first()

    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")

    from backend.app.services.hin_service import HINService
    from backend.app.services.crash_service import CrashService

    if layer == 'hin':
        hin_service = HINService(db)
        geojson = hin_service.get_hin_geojson(analysis_id)
    elif layer == 'crashes':
        crash_service = CrashService(db)
        geojson = crash_service.get_crashes_geojson(
            analysis.muni_id,
            analysis.start_year,
            analysis.end_year
        )
    else:
        raise HTTPException(status_code=400, detail="Invalid layer type")

    # Return as downloadable file
    import json

    json_str = json.dumps(geojson, indent=2)

    return StreamingResponse(
        io.BytesIO(json_str.encode()),
        media_type="application/geo+json",
        headers={
            "Content-Disposition": f"attachment; filename=analysis_{analysis_id}_{layer}.geojson"
        }
    )
