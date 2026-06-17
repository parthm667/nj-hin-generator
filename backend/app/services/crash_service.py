"""
Crash analysis service.

Handles crash-to-segment assignment, severity weighting,
and crash statistics calculation.
"""

from sqlalchemy.orm import Session
from sqlalchemy import text, and_, or_, func
from geoalchemy2.functions import ST_Distance, ST_ClosestPoint
from backend.app.models.tables import Crash, RoadSegment, Municipality
from backend.app.config import settings
import logging
from typing import List, Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class CrashService:
    """Service for crash data operations."""

    def __init__(self, db: Session):
        self.db = db
        self.severity_weights = settings.severity_weights

    def snap_crashes_to_segments(
        self,
        muni_id: int,
        snap_distance_meters: float = None,
        force_resnap: bool = False
    ) -> int:
        """
        Snap crash points to nearest road segments.

        Args:
            muni_id: Municipality ID
            snap_distance_meters: Maximum distance for snapping (default from settings)
            force_resnap: If True, re-snap already assigned crashes

        Returns:
            Number of crashes successfully snapped
        """
        if snap_distance_meters is None:
            snap_distance_meters = settings.crash_snap_distance_meters

        logger.info(f"Snapping crashes to road segments (max distance: {snap_distance_meters}m)")

        # Query for unassigned crashes (or all if force_resnap)
        query = self.db.query(Crash).filter(Crash.muni_id == muni_id)

        if not force_resnap:
            query = query.filter(Crash.segment_id.is_(None))

        crashes = query.all()

        if not crashes:
            logger.info("No crashes to snap")
            return 0

        snapped_count = 0

        for crash in crashes:
            # Find nearest segment using spatial query
            nearest_segment = self.find_nearest_segment(
                crash,
                muni_id,
                snap_distance_meters
            )

            if nearest_segment:
                crash.segment_id = nearest_segment['segment_id']
                crash.snap_distance = nearest_segment['distance']
                snapped_count += 1

                if snapped_count % 100 == 0:
                    self.db.commit()
                    logger.info(f"Snapped {snapped_count} crashes...")

        self.db.commit()
        logger.info(f"Snapped {snapped_count} of {len(crashes)} crashes")

        return snapped_count

    def find_nearest_segment(
        self,
        crash: Crash,
        muni_id: int,
        max_distance_meters: float
    ) -> Optional[Dict]:
        """
        Find the nearest road segment to a crash point.

        Args:
            crash: Crash object
            muni_id: Municipality ID to search within
            max_distance_meters: Maximum distance threshold

        Returns:
            Dictionary with segment_id and distance, or None
        """
        # Use PostGIS spatial query. Reference the crash geometry from the
        # crashes table by id so we don't have to bind a WKBElement (which
        # SQLAlchemy can't pass as a plain parameter).
        query = text("""
            WITH crash_point AS (
                SELECT geom FROM crashes WHERE crash_id = :crash_id
            )
            SELECT
                rs.segment_id AS segment_id,
                ST_Distance(
                    (SELECT geom FROM crash_point)::geography,
                    rs.geom::geography
                ) as distance
            FROM road_segments rs
            WHERE rs.muni_id = :muni_id
            ORDER BY rs.geom <-> (SELECT geom FROM crash_point)
            LIMIT 1
        """)

        result = self.db.execute(
            query,
            {
                'crash_id': crash.crash_id,
                'muni_id': muni_id
            }
        ).fetchone()

        if result and result.distance <= max_distance_meters:
            return {
                'segment_id': result.segment_id,
                'distance': result.distance
            }

        return None

    def get_crash_severity_weight(self, severity: str) -> int:
        """
        Get the weight for a crash severity level.

        Args:
            severity: Severity level (fatal, serious_injury, minor_injury, property_damage)

        Returns:
            Severity weight
        """
        return self.severity_weights.get(severity, 1)

    def calculate_segment_crash_stats(
        self,
        segment_id: int,
        start_year: int,
        end_year: int
    ) -> Dict:
        """
        Calculate crash statistics for a road segment.

        Args:
            segment_id: Road segment ID
            start_year: Start year for analysis
            end_year: End year for analysis

        Returns:
            Dictionary with crash statistics
        """
        # Query crashes for this segment in date range
        crashes = self.db.query(Crash).filter(
            and_(
                Crash.segment_id == segment_id,
                Crash.crash_date >= datetime(start_year, 1, 1),
                Crash.crash_date < datetime(end_year + 1, 1, 1)
            )
        ).all()

        # Calculate statistics
        stats = {
            'total_crashes': len(crashes),
            'fatal_crashes': sum(1 for c in crashes if c.severity == 'fatal'),
            'serious_injury_crashes': sum(1 for c in crashes if c.severity == 'serious_injury'),
            'minor_injury_crashes': sum(1 for c in crashes if c.severity == 'minor_injury'),
            'property_damage_crashes': sum(1 for c in crashes if c.severity == 'property_damage'),
            'ped_crashes': sum(1 for c in crashes if c.ped_involved),
            'bike_crashes': sum(1 for c in crashes if c.bike_involved),
        }

        # Calculate severity-weighted score
        severity_score = sum(
            self.get_crash_severity_weight(c.severity) for c in crashes
        )

        stats['severity_score'] = severity_score

        return stats

    def get_municipality_crash_summary(self, muni_id: int, start_year: int, end_year: int) -> Dict:
        """
        Get summary crash statistics for a municipality.

        Args:
            muni_id: Municipality ID
            start_year: Start year
            end_year: End year

        Returns:
            Dictionary with summary statistics
        """
        crashes = self.db.query(Crash).filter(
            and_(
                Crash.muni_id == muni_id,
                Crash.crash_date >= datetime(start_year, 1, 1),
                Crash.crash_date < datetime(end_year + 1, 1, 1)
            )
        ).all()

        summary = {
            'total_crashes': len(crashes),
            'fatal_crashes': sum(1 for c in crashes if c.severity == 'fatal'),
            'serious_injury_crashes': sum(1 for c in crashes if c.severity == 'serious_injury'),
            'minor_injury_crashes': sum(1 for c in crashes if c.severity == 'minor_injury'),
            'property_damage_crashes': sum(1 for c in crashes if c.severity == 'property_damage'),
            'ped_crashes': sum(1 for c in crashes if c.ped_involved),
            'bike_crashes': sum(1 for c in crashes if c.bike_involved),
            'crashes_with_segments': sum(1 for c in crashes if c.segment_id is not None)
        }

        return summary

    def get_crashes_geojson(
        self,
        muni_id: int,
        start_year: int = None,
        end_year: int = None,
        severity: str = None,
        ped_only: bool = False,
        bike_only: bool = False
    ) -> Dict:
        """
        Get crash points as GeoJSON for mapping.

        Args:
            muni_id: Municipality ID
            start_year: Optional start year filter
            end_year: Optional end year filter
            severity: Optional severity filter
            ped_only: If True, only pedestrian-involved crashes
            bike_only: If True, only bicycle-involved crashes

        Returns:
            GeoJSON FeatureCollection
        """
        query = self.db.query(Crash).filter(Crash.muni_id == muni_id)

        # Apply filters
        if start_year:
            query = query.filter(Crash.crash_date >= datetime(start_year, 1, 1))

        if end_year:
            query = query.filter(Crash.crash_date < datetime(end_year + 1, 1, 1))

        if severity:
            query = query.filter(Crash.severity == severity)

        if ped_only:
            query = query.filter(Crash.ped_involved == True)

        if bike_only:
            query = query.filter(Crash.bike_involved == True)

        crashes = query.all()

        # Convert to GeoJSON
        features = []
        for crash in crashes:
            # Parse WKT geometry to get coordinates
            geom_wkt = crash.geom
            # Simplified parsing - in production use shapely or geoalchemy2
            # Example: "SRID=4326;POINT(-74.123 40.456)"
            coords_str = geom_wkt.split('POINT(')[1].rstrip(')')
            lon, lat = map(float, coords_str.split())

            feature = {
                'type': 'Feature',
                'geometry': {
                    'type': 'Point',
                    'coordinates': [lon, lat]
                },
                'properties': {
                    'crash_id': crash.crash_id,
                    'date': crash.crash_date.isoformat(),
                    'severity': crash.severity,
                    'ped_involved': crash.ped_involved,
                    'bike_involved': crash.bike_involved,
                    'road_name': crash.road_name
                }
            }
            features.append(feature)

        return {
            'type': 'FeatureCollection',
            'features': features
        }
