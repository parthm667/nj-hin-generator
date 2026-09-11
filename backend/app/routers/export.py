"""
Export API router.

Endpoints for generating and downloading reports.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session
from app.models.database import get_db
from app.models.tables import Analysis
from app.models.schemas import ReportRequest
from app.services.coverage_service import (
    AnalysisDataConflictError,
    CoverageService,
)
import logging
import io
import csv
from typing import Literal

logger = logging.getLogger(__name__)

router = APIRouter()


def csv_cell(value):
    if value is None:
        return ''
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
        return "'" + value
    return value


@router.post("/{analysis_id}/pdf")
def generate_pdf_report(
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

    from app.routers.analysis import export_analysis_pdf
    return export_analysis_pdf(analysis_id, db)


@router.get("/{analysis_id}/csv")
def export_csv(
    analysis_id: int,
    data_type: Literal['crashes', 'hin_segments'] = 'crashes',
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

    if analysis.status != 'completed':
        raise HTTPException(409, 'Analysis is not completed')
    try:
        CoverageService(db).require_current_results(analysis)
    except AnalysisDataConflictError as exc:
        raise HTTPException(409, exc.detail) from exc
    from app.services.crash_service import CrashService
    from app.services.hin_service import HINService
    if data_type == 'crashes':
        collection = CrashService(db).get_crashes_geojson(analysis.muni_id, analysis.start_year, analysis.end_year)
        fields = ['crash_id', 'date', 'severity', 'ped_involved', 'bike_involved',
                  'total_killed', 'total_injured', 'pedestrians_killed', 'pedestrians_injured',
                  'road_name', 'geocode_quality', 'longitude', 'latitude']
    else:
        collection = HINService(db).get_hin_geojson(analysis_id)
        fields = ['hin_id', 'segment_id', 'road_name', 'crash_count', 'crash_rate',
                  'severity_score', 'corridor_name', 'in_vulnerable_tract']
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    for feature in collection['features']:
        row = dict(feature['properties'])
        if data_type == 'crashes':
            row['longitude'], row['latitude'] = feature['geometry']['coordinates'][:2]
        writer.writerow({name: csv_cell(value) for name, value in row.items()})
    return StreamingResponse(iter([output.getvalue()]), media_type='text/csv', headers={
        'Content-Disposition': f'attachment; filename=analysis_{analysis_id}_{data_type}.csv',
        'X-Content-Type-Options': 'nosniff',
    })


@router.get("/{analysis_id}/geojson")
def export_geojson(
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

    if analysis.status != 'completed':
        raise HTTPException(409, 'Analysis is not completed')
    try:
        CoverageService(db).require_current_results(analysis)
    except AnalysisDataConflictError as exc:
        raise HTTPException(status_code=409, detail=exc.detail) from exc

    from app.services.hin_service import HINService
    from app.services.crash_service import CrashService

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
