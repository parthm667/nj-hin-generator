"""
Crash analysis service.

Handles crash-to-segment assignment, severity weighting,
and crash statistics calculation.
"""

import json
import logging
import math
from datetime import datetime
from typing import Dict, Optional

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.config import settings
from app.models.tables import Crash

logger = logging.getLogger(__name__)


class CrashService:
    """Service for crash data operations."""

    def __init__(self, db: Session):
        self.db = db
        self.severity_weights = settings.severity_weights

    @staticmethod
    def validate_snap_distance(snap_distance_meters: float) -> float:
        """Return a validated distance suitable for PostGIS geography calls."""
        try:
            distance = float(snap_distance_meters)
        except (TypeError, ValueError) as exc:
            raise ValueError("Snap distance must be a finite nonnegative number") from exc

        if not math.isfinite(distance) or distance < 0:
            raise ValueError("Snap distance must be a finite nonnegative number")
        return distance

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

        snap_distance_meters = self.validate_snap_distance(snap_distance_meters)

        logger.info(f"Snapping crashes to road segments (max distance: {snap_distance_meters}m)")

        result = self.db.execute(
            text(
                """
                WITH candidates AS MATERIALIZED (
                    SELECT
                        crash.crash_id,
                        nearest.segment_id,
                        nearest.distance
                    FROM crashes AS crash
                    LEFT JOIN LATERAL (
                        SELECT
                            segment.segment_id,
                            ST_Distance(
                                crash.geom::geography,
                                segment.geom::geography
                            ) AS distance
                        FROM road_segments AS segment
                        WHERE segment.muni_id = :muni_id
                          AND ST_DWithin(
                              crash.geom::geography,
                              segment.geom::geography,
                              :snap_distance_meters
                          )
                        ORDER BY
                            ST_Distance(
                                crash.geom::geography,
                                segment.geom::geography
                            ),
                            segment.segment_id
                        LIMIT 1
                    ) AS nearest ON TRUE
                    WHERE crash.muni_id = :muni_id
                      AND (:force_resnap OR crash.segment_id IS NULL)
                ),
                updated AS (
                    UPDATE crashes AS crash
                    SET
                        segment_id = candidates.segment_id,
                        snap_distance = candidates.distance
                    FROM candidates
                    WHERE crash.crash_id = candidates.crash_id
                    RETURNING crash.segment_id
                )
                SELECT COUNT(segment_id) AS snapped_count
                FROM updated
                """
            ),
            {
                "muni_id": muni_id,
                "snap_distance_meters": snap_distance_meters,
                "force_resnap": force_resnap,
            },
        ).one()
        self.db.commit()
        snapped_count = int(result.snapped_count)
        logger.info("Snapped %s crashes", snapped_count)

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
        max_distance_meters = self.validate_snap_distance(max_distance_meters)

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
              AND ST_DWithin(
                  (SELECT geom FROM crash_point)::geography,
                  rs.geom::geography,
                  :max_distance_meters
              )
            ORDER BY
                ST_Distance(
                    (SELECT geom FROM crash_point)::geography,
                    rs.geom::geography
                ),
                rs.segment_id
            LIMIT 1
        """)

        result = self.db.execute(
            query,
            {
                'crash_id': crash.crash_id,
                'muni_id': muni_id,
                'max_distance_meters': max_distance_meters,
            }
        ).fetchone()

        if result:
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
        row = self.db.execute(
            text(
                """
                SELECT
                    COUNT(*)::integer AS total_crashes,
                    COUNT(*) FILTER (WHERE severity = 'fatal')::integer
                        AS fatal_crashes,
                    COUNT(*) FILTER (WHERE severity = 'serious_injury')::integer
                        AS serious_injury_crashes,
                    COUNT(*) FILTER (WHERE severity = 'minor_injury')::integer
                        AS minor_injury_crashes,
                    COUNT(*) FILTER (WHERE severity = 'injury_unknown')::integer
                        AS injury_unknown_crashes,
                    COUNT(*) FILTER (WHERE severity = 'property_damage')::integer
                        AS property_damage_crashes,
                    COUNT(*) FILTER (WHERE ped_involved IS TRUE)::integer
                        AS ped_crashes,
                    COUNT(*) FILTER (WHERE bike_involved IS TRUE)::integer
                        AS bike_crashes,
                    COALESCE(SUM(CASE severity
                        WHEN 'fatal' THEN :fatal_weight
                        WHEN 'serious_injury' THEN :serious_weight
                        WHEN 'minor_injury' THEN :minor_weight
                        WHEN 'property_damage' THEN :property_weight
                        ELSE 1
                    END), 0)::integer AS severity_score
                FROM crashes
                WHERE segment_id = :segment_id
                  AND crash_date >= :start_date
                  AND crash_date < :end_date
                """
            ),
            {
                "segment_id": segment_id,
                "start_date": datetime(start_year, 1, 1),
                "end_date": datetime(end_year + 1, 1, 1),
                "fatal_weight": self.severity_weights["fatal"],
                "serious_weight": self.severity_weights["serious_injury"],
                "minor_weight": self.severity_weights["minor_injury"],
                "property_weight": self.severity_weights["property_damage"],
            },
        ).mappings().one()
        return dict(row)

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
        row = self.db.execute(
            text(
                """
                SELECT
                    COUNT(*)::integer AS total_crashes,
                    COUNT(*) FILTER (WHERE severity = 'fatal')::integer
                        AS fatal_crashes,
                    COUNT(*) FILTER (WHERE severity = 'serious_injury')::integer
                        AS serious_injury_crashes,
                    COUNT(*) FILTER (WHERE severity = 'minor_injury')::integer
                        AS minor_injury_crashes,
                    COUNT(*) FILTER (WHERE severity = 'injury_unknown')::integer
                        AS injury_unknown_crashes,
                    COUNT(*) FILTER (WHERE severity = 'property_damage')::integer
                        AS property_damage_crashes,
                    COUNT(*) FILTER (WHERE ped_involved IS TRUE)::integer
                        AS ped_crashes,
                    COUNT(*) FILTER (WHERE bike_involved IS TRUE)::integer
                        AS bike_crashes,
                    COUNT(*) FILTER (WHERE bike_involved IS NULL)::integer
                        AS bike_involvement_unknown_crashes,
                    (CASE
                        WHEN COUNT(*) = COUNT(total_killed)
                        THEN COALESCE(SUM(total_killed), 0)
                        ELSE NULL
                    END)::integer AS total_killed,
                    (CASE
                        WHEN COUNT(*) = COUNT(total_injured)
                        THEN COALESCE(SUM(total_injured), 0)
                        ELSE NULL
                    END)::integer AS total_injured,
                    (CASE
                        WHEN COUNT(*) = COUNT(pedestrians_killed)
                        THEN COALESCE(SUM(pedestrians_killed), 0)
                        ELSE NULL
                    END)::integer AS pedestrians_killed,
                    (CASE
                        WHEN COUNT(*) = COUNT(pedestrians_injured)
                        THEN COALESCE(SUM(pedestrians_injured), 0)
                        ELSE NULL
                    END)::integer AS pedestrians_injured,
                    (
                        COUNT(*) = COUNT(total_killed)
                        AND COUNT(*) = COUNT(total_injured)
                        AND COUNT(*) = COUNT(pedestrians_killed)
                        AND COUNT(*) = COUNT(pedestrians_injured)
                    ) AS casualty_counts_complete,
                    COUNT(*) FILTER (WHERE segment_id IS NOT NULL)::integer
                        AS crashes_with_segments
                FROM crashes
                WHERE muni_id = :muni_id
                  AND crash_date >= :start_date
                  AND crash_date < :end_date
                """
            ),
            {
                "muni_id": muni_id,
                "start_date": datetime(start_year, 1, 1),
                "end_date": datetime(end_year + 1, 1, 1),
            },
        ).mappings().one()
        return dict(row)

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
        query = self.db.query(
            Crash,
            func.ST_AsGeoJSON(Crash.geom).label("geometry"),
        ).filter(Crash.muni_id == muni_id)

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
        for crash, geometry_json in crashes:
            geometry = (
                json.loads(geometry_json)
                if isinstance(geometry_json, str)
                else geometry_json
            )
            feature = {
                'type': 'Feature',
                'geometry': geometry,
                'properties': {
                    'crash_id': crash.crash_id,
                    'date': crash.crash_date.isoformat(),
                    'severity': crash.severity,
                    'ped_involved': crash.ped_involved,
                    'bike_involved': crash.bike_involved,
                    'total_killed': crash.total_killed,
                    'total_injured': crash.total_injured,
                    'pedestrians_killed': crash.pedestrians_killed,
                    'pedestrians_injured': crash.pedestrians_injured,
                    'road_name': crash.road_name,
                    'geocode_quality': getattr(crash, 'geocode_quality', None)
                }
            }
            features.append(feature)

        return {
            'type': 'FeatureCollection',
            'features': features
        }
