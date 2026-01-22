"""
High Injury Network identification service.

Implements statistical analysis to identify road segments with
significantly elevated crash rates.
"""

from sqlalchemy.orm import Session
from sqlalchemy import and_, func, text
from backend.app.models.tables import (
    RoadSegment, Crash, HINSegment, Analysis, CensusTract
)
from backend.app.services.crash_service import CrashService
from backend.app.config import settings
from scipy import stats
import numpy as np
import logging
from typing import List, Dict, Optional
from datetime import datetime
from collections import defaultdict

logger = logging.getLogger(__name__)


class HINService:
    """Service for High Injury Network analysis."""

    def __init__(self, db: Session):
        self.db = db
        self.crash_service = CrashService(db)

    def run_analysis(
        self,
        muni_id: int,
        start_year: int,
        end_year: int,
        snap_distance_meters: float = None,
        significance_threshold: float = None
    ) -> Analysis:
        """
        Run complete HIN analysis for a municipality.

        Args:
            muni_id: Municipality ID
            start_year: Start year for analysis
            end_year: End year for analysis
            snap_distance_meters: Crash snapping distance
            significance_threshold: P-value threshold for significance

        Returns:
            Analysis object with results
        """
        logger.info(f"Starting HIN analysis for municipality {muni_id}")

        # Use defaults if not provided
        if snap_distance_meters is None:
            snap_distance_meters = settings.crash_snap_distance_meters

        if significance_threshold is None:
            significance_threshold = settings.significance_threshold

        # Create analysis record
        analysis = Analysis(
            muni_id=muni_id,
            start_year=start_year,
            end_year=end_year,
            years_included=list(range(start_year, end_year + 1)),
            snap_distance_meters=snap_distance_meters,
            significance_threshold=significance_threshold,
            status='running'
        )

        self.db.add(analysis)
        self.db.commit()

        try:
            # Step 1: Snap crashes to segments
            logger.info("Step 1: Snapping crashes to segments")
            snapped_count = self.crash_service.snap_crashes_to_segments(
                muni_id,
                snap_distance_meters
            )

            # Step 2: Calculate segment-level statistics
            logger.info("Step 2: Calculating segment statistics")
            segment_stats = self.calculate_all_segment_stats(
                muni_id,
                start_year,
                end_year
            )

            # Step 3: Run statistical significance tests
            logger.info("Step 3: Running statistical tests")
            self.identify_significant_segments(
                analysis.analysis_id,
                segment_stats,
                start_year,
                end_year,
                significance_threshold
            )

            # Step 4: Group into corridors
            logger.info("Step 4: Grouping into corridors")
            self.group_into_corridors(analysis.analysis_id)

            # Step 5: Overlay with equity data
            logger.info("Step 5: Adding equity overlay")
            self.add_equity_overlay(analysis.analysis_id)

            # Step 6: Calculate summary statistics
            logger.info("Step 6: Calculating summary")
            self.update_analysis_summary(analysis)

            # Mark as completed
            analysis.status = 'completed'
            analysis.updated_at = datetime.utcnow()
            self.db.commit()

            logger.info(f"Analysis {analysis.analysis_id} completed successfully")

            return analysis

        except Exception as e:
            logger.error(f"Analysis failed: {e}")
            analysis.status = 'failed'
            analysis.error_message = str(e)
            self.db.commit()
            raise

    def calculate_all_segment_stats(
        self,
        muni_id: int,
        start_year: int,
        end_year: int
    ) -> List[Dict]:
        """
        Calculate crash statistics for all segments in a municipality.

        Args:
            muni_id: Municipality ID
            start_year: Start year
            end_year: End year

        Returns:
            List of dictionaries with segment statistics
        """
        # Get all segments in municipality
        segments = self.db.query(RoadSegment).filter(
            RoadSegment.muni_id == muni_id
        ).all()

        logger.info(f"Calculating stats for {len(segments)} segments")

        segment_stats = []

        for segment in segments:
            stats = self.crash_service.calculate_segment_crash_stats(
                segment.segment_id,
                start_year,
                end_year
            )

            stats['segment_id'] = segment.segment_id
            stats['length_miles'] = segment.length_miles
            stats['road_class'] = segment.road_class

            segment_stats.append(stats)

        return segment_stats

    def identify_significant_segments(
        self,
        analysis_id: int,
        segment_stats: List[Dict],
        start_year: int,
        end_year: int,
        significance_threshold: float
    ):
        """
        Identify statistically significant high-crash segments using Poisson test.

        Args:
            analysis_id: Analysis ID
            segment_stats: List of segment statistics
            start_year: Start year
            end_year: End year
            significance_threshold: P-value threshold
        """
        years_of_data = end_year - start_year + 1

        # Calculate baseline crash rate per road class
        baseline_rates = self.calculate_baseline_rates(segment_stats, years_of_data)

        logger.info(f"Baseline rates: {baseline_rates}")

        # Test each segment
        for stats in segment_stats:
            segment_id = stats['segment_id']
            length_miles = stats['length_miles']
            road_class = stats['road_class']
            severity_score = stats['severity_score']

            # Skip segments with no length
            if length_miles <= 0:
                continue

            # Calculate crash rate (crashes per mile per year)
            crash_rate = severity_score / (length_miles * years_of_data)

            # Get baseline rate for this road class
            baseline_rate = baseline_rates.get(road_class, baseline_rates.get('overall', 1.0))

            # Expected number of crashes based on baseline
            expected_crashes = baseline_rate * length_miles * years_of_data

            # Poisson test: probability of observing this many crashes or more
            # under the null hypothesis
            observed_crashes = stats['total_crashes']

            if expected_crashes > 0:
                # One-tailed test (we care about higher than expected)
                p_value = 1 - stats.poisson.cdf(observed_crashes - 1, expected_crashes)
            else:
                p_value = 1.0

            # Determine if significant
            is_significant = (
                p_value < significance_threshold and
                observed_crashes >= 3  # Minimum crash threshold
            )

            # Determine HIN type flags
            is_general_hin = is_significant
            is_ped_hin = is_significant and stats['ped_crashes'] >= 2
            is_bike_hin = is_significant and stats['bike_crashes'] >= 2

            # Create HIN segment record (even if not significant, for reference)
            hin_segment = HINSegment(
                segment_id=segment_id,
                analysis_id=analysis_id,
                crash_count_total=stats['total_crashes'],
                crash_count_fatal=stats['fatal_crashes'],
                crash_count_serious_injury=stats['serious_injury_crashes'],
                crash_count_minor_injury=stats['minor_injury_crashes'],
                crash_count_ped=stats['ped_crashes'],
                crash_count_bike=stats['bike_crashes'],
                severity_score=severity_score,
                crash_rate=crash_rate,
                expected_crashes=expected_crashes,
                p_value=p_value,
                is_significant=is_significant,
                is_general_hin=is_general_hin,
                is_ped_hin=is_ped_hin,
                is_bike_hin=is_bike_hin
            )

            self.db.add(hin_segment)

        self.db.commit()

        # Log results
        significant_count = self.db.query(HINSegment).filter(
            and_(
                HINSegment.analysis_id == analysis_id,
                HINSegment.is_significant == True
            )
        ).count()

        logger.info(f"Identified {significant_count} significant segments")

    def calculate_baseline_rates(
        self,
        segment_stats: List[Dict],
        years_of_data: int
    ) -> Dict[str, float]:
        """
        Calculate baseline crash rates by road class.

        Args:
            segment_stats: List of segment statistics
            years_of_data: Number of years in analysis

        Returns:
            Dictionary of baseline rates by road class
        """
        # Group by road class
        class_stats = defaultdict(lambda: {'total_crashes': 0, 'total_miles': 0})

        for stats in segment_stats:
            road_class = stats['road_class']
            class_stats[road_class]['total_crashes'] += stats['severity_score']
            class_stats[road_class]['total_miles'] += stats['length_miles']

        # Calculate rates
        baseline_rates = {}
        for road_class, data in class_stats.items():
            if data['total_miles'] > 0:
                rate = data['total_crashes'] / (data['total_miles'] * years_of_data)
                baseline_rates[road_class] = rate

        # Overall baseline
        total_crashes = sum(s['severity_score'] for s in segment_stats)
        total_miles = sum(s['length_miles'] for s in segment_stats)

        if total_miles > 0:
            baseline_rates['overall'] = total_crashes / (total_miles * years_of_data)

        return baseline_rates

    def group_into_corridors(self, analysis_id: int):
        """
        Group connected HIN segments into corridors.

        Args:
            analysis_id: Analysis ID
        """
        logger.info("Grouping segments into corridors")

        # Get all significant HIN segments
        hin_segments = self.db.query(HINSegment).join(RoadSegment).filter(
            and_(
                HINSegment.analysis_id == analysis_id,
                HINSegment.is_significant == True
            )
        ).all()

        if not hin_segments:
            logger.info("No significant segments to group")
            return

        # Group by road name (simplified approach)
        # In production, use network connectivity analysis
        corridors = defaultdict(list)

        for hin_seg in hin_segments:
            segment = hin_seg.segment
            road_name = segment.road_name or "Unnamed"

            # Normalize road name
            road_name_normalized = road_name.strip().title()

            corridors[road_name_normalized].append(hin_seg)

        # Assign corridor IDs and names
        corridor_id = 1
        for road_name, segments in corridors.items():
            for hin_seg in segments:
                hin_seg.corridor_id = corridor_id
                hin_seg.corridor_name = road_name

            corridor_id += 1

        self.db.commit()

        logger.info(f"Created {len(corridors)} corridors")

    def add_equity_overlay(self, analysis_id: int):
        """
        Overlay HIN segments with equity/vulnerability data.

        Args:
            analysis_id: Analysis ID
        """
        logger.info("Adding equity overlay")

        # Get all HIN segments
        hin_segments = self.db.query(HINSegment).join(RoadSegment).filter(
            HINSegment.analysis_id == analysis_id
        ).all()

        for hin_seg in hin_segments:
            # Find census tracts that intersect this segment
            # Simplified: use spatial query
            query = text("""
                SELECT tract_id, svi_score
                FROM census_tracts
                WHERE ST_Intersects(
                    geom,
                    (SELECT geom FROM road_segments WHERE segment_id = :segment_id)
                )
                LIMIT 1
            """)

            result = self.db.execute(
                query,
                {'segment_id': hin_seg.segment_id}
            ).fetchone()

            if result:
                hin_seg.avg_svi_score = result.svi_score

                # Flag as vulnerable if SVI percentile > 75
                hin_seg.in_vulnerable_tract = result.svi_score > 75

        self.db.commit()

        vulnerable_count = self.db.query(HINSegment).filter(
            and_(
                HINSegment.analysis_id == analysis_id,
                HINSegment.in_vulnerable_tract == True
            )
        ).count()

        logger.info(f"Flagged {vulnerable_count} segments in vulnerable tracts")

    def update_analysis_summary(self, analysis: Analysis):
        """
        Calculate and update summary statistics for an analysis.

        Args:
            analysis: Analysis object to update
        """
        # Get crash summary
        crash_summary = self.crash_service.get_municipality_crash_summary(
            analysis.muni_id,
            analysis.start_year,
            analysis.end_year
        )

        analysis.total_crashes = crash_summary['total_crashes']
        analysis.total_fatalities = crash_summary['fatal_crashes']
        analysis.total_injuries = (
            crash_summary['serious_injury_crashes'] +
            crash_summary['minor_injury_crashes']
        )

        # Get HIN summary
        hin_segments = self.db.query(HINSegment).join(RoadSegment).filter(
            and_(
                HINSegment.analysis_id == analysis.analysis_id,
                HINSegment.is_significant == True
            )
        ).all()

        analysis.hin_segment_count = len(hin_segments)
        analysis.hin_miles = sum(
            self.db.query(RoadSegment).get(hin_seg.segment_id).length_miles
            for hin_seg in hin_segments
        )

        self.db.commit()

    def get_hin_geojson(
        self,
        analysis_id: int,
        hin_type: str = 'general'
    ) -> Dict:
        """
        Get HIN segments as GeoJSON.

        Args:
            analysis_id: Analysis ID
            hin_type: Type of HIN ('general', 'pedestrian', 'bicycle')

        Returns:
            GeoJSON FeatureCollection
        """
        query = self.db.query(HINSegment).join(RoadSegment).filter(
            and_(
                HINSegment.analysis_id == analysis_id,
                HINSegment.is_significant == True
            )
        )

        # Filter by HIN type
        if hin_type == 'pedestrian':
            query = query.filter(HINSegment.is_ped_hin == True)
        elif hin_type == 'bicycle':
            query = query.filter(HINSegment.is_bike_hin == True)

        hin_segments = query.all()

        # Convert to GeoJSON
        features = []
        for hin_seg in hin_segments:
            segment = hin_seg.segment

            # Parse WKT geometry (simplified)
            geom_wkt = segment.geom
            # In production, use shapely or geoalchemy2 for proper parsing

            feature = {
                'type': 'Feature',
                'geometry': {
                    'type': 'LineString',
                    'coordinates': []  # Would parse from WKT
                },
                'properties': {
                    'hin_id': hin_seg.hin_id,
                    'segment_id': hin_seg.segment_id,
                    'road_name': segment.road_name,
                    'crash_count': hin_seg.crash_count_total,
                    'crash_rate': hin_seg.crash_rate,
                    'severity_score': hin_seg.severity_score,
                    'corridor_name': hin_seg.corridor_name,
                    'in_vulnerable_tract': hin_seg.in_vulnerable_tract
                }
            }
            features.append(feature)

        return {
            'type': 'FeatureCollection',
            'features': features
        }
