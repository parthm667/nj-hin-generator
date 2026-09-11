"""
High Injury Network identification service.

Implements statistical analysis to identify road segments with
significantly elevated crash rates.
"""

import json
import logging
import math
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Dict, List, Optional

from scipy import stats as scipy_stats
from sqlalchemy import and_, func, text
from sqlalchemy.orm import Session

from app.config import settings
from app.models.tables import Analysis, HINSegment, RoadSegment
from app.services.crash_service import CrashService
from app.services.coverage_service import CoverageService
from app.services.methodology import (
    CORRIDOR_ENDPOINT_TOLERANCE_METERS,
    MIN_ANALYSIS_LENGTH_MILES,
)
from app.services.version_service import current_input_version

logger = logging.getLogger(__name__)


class HINService:
    """Service for High Injury Network analysis."""

    # Two-key advisory locks avoid colliding with unrelated application locks.
    ANALYSIS_LOCK_NAMESPACE = 1212763713

    def __init__(self, db: Session):
        self.db = db
        self.crash_service = CrashService(db)

    @contextmanager
    def _municipality_analysis_lock(self, muni_id: int):
        """Serialize analyses for one municipality across internal commits."""
        bind = self.db.get_bind()
        engine = bind if hasattr(bind, "connect") else bind.engine
        lock_connection = engine.connect()
        parameters = {
            "namespace": self.ANALYSIS_LOCK_NAMESPACE,
            "muni_id": muni_id,
        }
        lock_acquired = False
        primary_error = None

        try:
            lock_connection.execute(
                text("SELECT pg_advisory_lock(:namespace, :muni_id)"),
                parameters,
            )
            lock_acquired = True
            yield
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            unlock_error = None
            discard_connection = not lock_acquired

            if lock_acquired:
                try:
                    unlocked = lock_connection.execute(
                        text("SELECT pg_advisory_unlock(:namespace, :muni_id)"),
                        parameters,
                    ).scalar_one()
                    if not unlocked:
                        discard_connection = True
                        logger.warning(
                            "Municipality analysis lock was not held for %s",
                            muni_id,
                        )
                except BaseException as exc:
                    unlock_error = exc
                    discard_connection = True

            if discard_connection:
                # A transaction rollback does not release session-level locks.
                # Invalidate the physical DBAPI connection so an uncertain lock
                # can never return to the pool on this PostgreSQL session.
                try:
                    lock_connection.invalidate()
                except BaseException:
                    logger.exception(
                        "Failed to invalidate municipality lock connection for %s",
                        muni_id,
                    )

            try:
                lock_connection.close()
            except BaseException:
                if primary_error is None and unlock_error is None:
                    raise
                logger.exception(
                    "Failed to close municipality lock connection for %s", muni_id
                )

            if unlock_error is not None:
                if primary_error is None:
                    raise unlock_error
                logger.error(
                    "Failed to unlock municipality %s while propagating the "
                    "analysis error",
                    muni_id,
                    exc_info=(
                        type(unlock_error),
                        unlock_error,
                        unlock_error.__traceback__,
                    ),
                )

    def run_analysis(
        self,
        analysis_id: int,
        muni_id: int,
        start_year: int,
        end_year: int,
        snap_distance_meters: float = None,
        significance_threshold: float = None
    ) -> Analysis:
        """
        Run complete HIN analysis for a municipality.

        Args:
            analysis_id: Existing analysis ID to update
            muni_id: Municipality ID
            start_year: Start year for analysis
            end_year: End year for analysis
            snap_distance_meters: Crash snapping distance
            significance_threshold: P-value threshold for significance

        Returns:
            Analysis object with results
        """
        # Use defaults if not provided
        if snap_distance_meters is None:
            snap_distance_meters = settings.crash_snap_distance_meters
        if significance_threshold is None:
            significance_threshold = settings.significance_threshold
        snap_distance_meters = self.crash_service.validate_snap_distance(
            snap_distance_meters
        )
        try:
            significance_threshold = float(significance_threshold)
        except (TypeError, ValueError) as exc:
            raise ValueError("Significance threshold must be finite from 0 to 1") from exc
        if not math.isfinite(significance_threshold) or not (
            0 <= significance_threshold <= 1
        ):
            raise ValueError("Significance threshold must be finite from 0 to 1")
        if start_year > end_year:
            raise ValueError("Start year must not be after end year")

        logger.info("Starting HIN analysis for municipality %s", muni_id)

        with self._municipality_analysis_lock(muni_id):
            analysis = self.db.query(Analysis).filter(
                Analysis.analysis_id == analysis_id
            ).first()
            if not analysis:
                raise ValueError(f"Analysis {analysis_id} not found")
            if analysis.muni_id != muni_id:
                raise ValueError(
                    f"Analysis {analysis_id} does not belong to municipality {muni_id}"
                )

            CoverageService(self.db).require_years_available(
                muni_id,
                start_year,
                end_year,
            )

            try:
                input_version = current_input_version(self.db)
                analysis.status = "running"
                analysis.error_message = None
                analysis.snap_distance_meters = snap_distance_meters
                analysis.significance_threshold = significance_threshold
                self.db.commit()

                logger.info("Step 1: Snapping crashes to segments")
                self.crash_service.snap_crashes_to_segments(
                    muni_id,
                    snap_distance_meters,
                    force_resnap=True,
                )

                logger.info("Step 2: Calculating segment statistics")
                segment_stats = self.calculate_all_segment_stats(
                    muni_id,
                    start_year,
                    end_year,
                )

                logger.info("Step 3: Running statistical tests")
                self.identify_significant_segments(
                    analysis.analysis_id,
                    segment_stats,
                    start_year,
                    end_year,
                    significance_threshold,
                )

                logger.info("Step 4: Grouping into corridors")
                self.group_into_corridors(analysis.analysis_id)

                logger.info("Step 5: Adding equity overlay")
                self.add_equity_overlay(analysis.analysis_id)

                logger.info("Step 6: Calculating summary")
                self.update_analysis_summary(analysis)

                if current_input_version(self.db) != input_version:
                    raise ValueError('Source data changed during analysis; rerun after ingestion completes')
                analysis.input_version = input_version
                analysis.status = "completed"
                analysis.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
                self.db.commit()
                logger.info("Analysis %s completed successfully", analysis.analysis_id)
                return analysis
            except Exception as exc:
                logger.error("Analysis failed: %s", exc)
                self.db.rollback()
                failed_analysis = self.db.query(Analysis).filter(
                    Analysis.analysis_id == analysis_id
                ).first()
                if failed_analysis:
                    failed_analysis.status = "failed"
                    failed_analysis.error_message = 'Analysis could not complete. Please try again later.'
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
        rows = self.db.execute(
            text(
                """
                SELECT
                    segment.segment_id,
                    segment.length_miles,
                    segment.road_class,
                    COUNT(crash.crash_id)::integer AS total_crashes,
                    COUNT(crash.crash_id) FILTER (
                        WHERE crash.severity = 'fatal'
                    )::integer AS fatal_crashes,
                    COUNT(crash.crash_id) FILTER (
                        WHERE crash.severity = 'serious_injury'
                    )::integer AS serious_injury_crashes,
                    COUNT(crash.crash_id) FILTER (
                        WHERE crash.severity = 'minor_injury'
                    )::integer AS minor_injury_crashes,
                    COUNT(crash.crash_id) FILTER (
                        WHERE crash.severity = 'property_damage'
                    )::integer AS property_damage_crashes,
                    COUNT(crash.crash_id) FILTER (
                        WHERE crash.ped_involved IS TRUE
                    )::integer AS ped_crashes,
                    COUNT(crash.crash_id) FILTER (
                        WHERE crash.bike_involved IS TRUE
                    )::integer AS bike_crashes,
                    COALESCE(SUM(CASE crash.severity
                        WHEN 'fatal' THEN :fatal_weight
                        WHEN 'serious_injury' THEN :serious_weight
                        WHEN 'minor_injury' THEN :minor_weight
                        WHEN 'injury_unknown' THEN :unknown_injury_weight
                        WHEN 'property_damage' THEN :property_weight
                        ELSE CASE WHEN crash.crash_id IS NULL THEN 0 ELSE 1 END
                    END), 0)::integer AS severity_score
                FROM road_segments AS segment
                LEFT JOIN crashes AS crash
                  ON crash.segment_id = segment.segment_id
                 AND crash.crash_date >= :start_date
                 AND crash.crash_date < :end_date
                WHERE segment.muni_id = :muni_id
                GROUP BY
                    segment.segment_id,
                    segment.length_miles,
                    segment.road_class
                ORDER BY segment.segment_id
                """
            ),
            {
                "muni_id": muni_id,
                "start_date": datetime(start_year, 1, 1),
                "end_date": datetime(end_year + 1, 1, 1),
                "fatal_weight": self.crash_service.severity_weights["fatal"],
                "serious_weight": self.crash_service.severity_weights[
                    "serious_injury"
                ],
                "minor_weight": self.crash_service.severity_weights["minor_injury"],
                "unknown_injury_weight": 3,
                "property_weight": self.crash_service.severity_weights[
                    "property_damage"
                ],
            },
        ).mappings().all()
        logger.info("Calculated stats for %s segments", len(rows))
        return [dict(row) for row in rows]

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

        # Calculate count-based, leave-one-out baseline rates per segment.
        baseline_rates = self.calculate_baseline_rates(segment_stats, years_of_data)

        logger.info(f"Baseline rates: {baseline_rates}")

        # Replace any prior results when an analysis is explicitly re-run.
        self.db.query(HINSegment).filter(
            HINSegment.analysis_id == analysis_id
        ).delete(synchronize_session=False)

        hin_segments = []
        for stats in segment_stats:
            segment_id = stats['segment_id']
            length_miles = stats['length_miles']
            severity_score = stats['severity_score']
            observed_crashes = stats['total_crashes']

            # Skip segments with no length
            if length_miles <= 0:
                continue

            # Calculate crash rate (crashes per mile per year)
            crash_rate = observed_crashes / (length_miles * years_of_data)

            is_eligible = length_miles >= MIN_ANALYSIS_LENGTH_MILES
            baseline_rate = baseline_rates.get(segment_id)

            # Ineligible fragments and segments without reference exposure are
            # retained for descriptive output but cannot be significant.
            expected_crashes = (
                baseline_rate * length_miles * years_of_data
                if is_eligible and baseline_rate is not None
                else 0.0
            )

            if is_eligible and expected_crashes > 0:
                # Survival probability is numerically stable in the upper tail.
                p_value = float(
                    scipy_stats.poisson.sf(observed_crashes - 1, expected_crashes)
                )
            else:
                p_value = 1.0

            # Determine if significant
            is_significant = (
                p_value < significance_threshold and
                observed_crashes >= 3
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

            hin_segments.append(hin_segment)

        self.db.bulk_save_objects(hin_segments)
        self.db.commit()
        significant_count = sum(segment.is_significant for segment in hin_segments)
        logger.info(f"Identified {significant_count} significant segments")

    def calculate_baseline_rates(
        self,
        segment_stats: List[Dict],
        years_of_data: int
    ) -> Dict[int, Optional[float]]:
        """
        Calculate leave-one-out count baselines for eligible segments.

        Args:
            segment_stats: List of segment statistics
            years_of_data: Number of years in analysis

        Returns:
            Mapping of segment ID to its reference crashes-per-mile-year rate.
        """
        class_totals = defaultdict(lambda: {"crashes": 0, "miles": 0.0})
        overall_crashes = 0
        overall_miles = 0.0
        eligible_stats = []

        for stats in segment_stats:
            length_miles = float(stats["length_miles"])
            if length_miles < MIN_ANALYSIS_LENGTH_MILES:
                continue
            crash_count = int(stats["total_crashes"])
            road_class = stats["road_class"]
            eligible_stats.append(stats)
            class_totals[road_class]["crashes"] += crash_count
            class_totals[road_class]["miles"] += length_miles
            overall_crashes += crash_count
            overall_miles += length_miles

        baseline_rates: Dict[int, Optional[float]] = {}
        for stats in eligible_stats:
            length_miles = float(stats["length_miles"])
            crash_count = int(stats["total_crashes"])
            class_total = class_totals[stats["road_class"]]
            reference_miles = class_total["miles"] - length_miles
            reference_crashes = class_total["crashes"] - crash_count

            if reference_miles <= 0:
                reference_miles = overall_miles - length_miles
                reference_crashes = overall_crashes - crash_count

            baseline_rates[stats["segment_id"]] = (
                reference_crashes / (reference_miles * years_of_data)
                if reference_miles > 0
                else None
            )

        return baseline_rates

    def group_into_corridors(self, analysis_id: int):
        """
        Group same-road HIN segments by metric endpoint connectivity.

        Args:
            analysis_id: Analysis ID
        """
        logger.info("Grouping segments into corridors")

        corridor_count = self.db.execute(
            text(
                """
                WITH RECURSIVE nodes AS MATERIALIZED (
                    SELECT
                        hin.hin_id,
                        segment.segment_id,
                        CASE
                            WHEN NULLIF(BTRIM(segment.sri), '') IS NOT NULL
                                THEN 'sri:' || UPPER(BTRIM(segment.sri))
                            WHEN NULLIF(BTRIM(segment.road_name), '') IS NOT NULL
                                THEN 'name:' || REGEXP_REPLACE(
                                    UPPER(BTRIM(segment.road_name)),
                                    '\\s+', ' ', 'g'
                                )
                            ELSE 'segment:' || segment.segment_id::text
                        END AS road_key,
                        INITCAP(
                            COALESCE(
                                NULLIF(BTRIM(segment.road_name), ''),
                                NULLIF(BTRIM(segment.sri), ''),
                                'Unnamed'
                            )
                        ) AS corridor_name,
                        ST_StartPoint(segment.geom) AS start_point,
                        ST_EndPoint(segment.geom) AS end_point
                    FROM hin_segments AS hin
                    JOIN road_segments AS segment
                      ON segment.segment_id = hin.segment_id
                    WHERE hin.analysis_id = :analysis_id
                      AND hin.is_significant IS TRUE
                ),
                edges AS MATERIALIZED (
                    SELECT
                        left_node.segment_id AS source_segment_id,
                        right_node.segment_id AS target_segment_id
                    FROM nodes AS left_node
                    JOIN nodes AS right_node
                      ON left_node.segment_id < right_node.segment_id
                     AND left_node.road_key = right_node.road_key
                     AND (
                          ST_DWithin(
                              left_node.start_point::geography,
                              right_node.start_point::geography,
                              :endpoint_tolerance_meters
                          )
                       OR ST_DWithin(
                              left_node.start_point::geography,
                              right_node.end_point::geography,
                              :endpoint_tolerance_meters
                          )
                       OR ST_DWithin(
                              left_node.end_point::geography,
                              right_node.start_point::geography,
                              :endpoint_tolerance_meters
                          )
                       OR ST_DWithin(
                              left_node.end_point::geography,
                              right_node.end_point::geography,
                              :endpoint_tolerance_meters
                          )
                     )
                ),
                directed_edges AS (
                    SELECT source_segment_id, target_segment_id FROM edges
                    UNION ALL
                    SELECT target_segment_id, source_segment_id FROM edges
                ),
                reach (root_segment_id, segment_id) AS (
                    SELECT segment_id, segment_id FROM nodes
                    UNION
                    SELECT reach.root_segment_id, edge.target_segment_id
                    FROM reach
                    JOIN directed_edges AS edge
                      ON edge.source_segment_id = reach.segment_id
                ),
                components AS (
                    SELECT
                        segment_id,
                        MIN(root_segment_id) AS root_segment_id
                    FROM reach
                    GROUP BY segment_id
                ),
                component_names AS (
                    SELECT
                        component.root_segment_id,
                        MIN(node.corridor_name) AS corridor_name
                    FROM components AS component
                    JOIN nodes AS node
                      ON node.segment_id = component.segment_id
                    GROUP BY component.root_segment_id
                ),
                grouped AS (
                    SELECT
                        node.hin_id,
                        component_name.corridor_name,
                        DENSE_RANK() OVER (
                            ORDER BY component.root_segment_id
                        )::integer
                            AS corridor_id
                    FROM components AS component
                    JOIN nodes AS node
                      ON node.segment_id = component.segment_id
                    JOIN component_names AS component_name
                      ON component_name.root_segment_id = component.root_segment_id
                ),
                updated AS (
                    UPDATE hin_segments AS hin
                    SET
                        corridor_id = grouped.corridor_id,
                        corridor_name = grouped.corridor_name
                    FROM grouped
                    WHERE hin.hin_id = grouped.hin_id
                    RETURNING hin.corridor_id
                )
                SELECT COUNT(DISTINCT corridor_id)::integer AS corridor_count
                FROM updated
                """
            ),
            {
                "analysis_id": analysis_id,
                "endpoint_tolerance_meters": CORRIDOR_ENDPOINT_TOLERANCE_METERS,
            },
        ).scalar_one()
        self.db.commit()
        logger.info("Created %s corridors", corridor_count)

    def add_equity_overlay(self, analysis_id: int):
        """
        Overlay HIN segments with equity/vulnerability data.

        Args:
            analysis_id: Analysis ID
        """
        logger.info("Adding equity overlay")

        vulnerable_count = self.db.execute(
            text(
                """
                WITH equity AS MATERIALIZED (
                    SELECT
                        hin.hin_id,
                        tract.svi_score,
                        tract.svi_percentile
                    FROM hin_segments AS hin
                    JOIN road_segments AS segment
                      ON segment.segment_id = hin.segment_id
                    LEFT JOIN LATERAL (
                        SELECT census.svi_score, census.svi_percentile
                        FROM census_tracts AS census
                        WHERE ST_Intersects(census.geom, segment.geom)
                        ORDER BY census.tract_id
                        LIMIT 1
                    ) AS tract ON TRUE
                    WHERE hin.analysis_id = :analysis_id
                ),
                updated AS (
                    UPDATE hin_segments AS hin
                    SET
                        avg_svi_score = equity.svi_score,
                        in_vulnerable_tract = equity.svi_percentile > 75
                    FROM equity
                    WHERE hin.hin_id = equity.hin_id
                    RETURNING hin.in_vulnerable_tract
                )
                SELECT COUNT(*) FILTER (
                    WHERE in_vulnerable_tract IS TRUE
                )::integer AS vulnerable_count
                FROM updated
                """
            ),
            {"analysis_id": analysis_id},
        ).scalar_one()
        self.db.commit()
        logger.info("Flagged %s segments in vulnerable tracts", vulnerable_count)

    def update_analysis_summary(self, analysis: Analysis):
        """
        Calculate and update summary statistics for an analysis.

        Args:
            analysis: Analysis object to update
        """
        summary = self.db.execute(
            text(
                """
                WITH crash_summary AS (
                    SELECT
                        COUNT(*)::integer AS total_crashes,
                        CASE WHEN COUNT(total_killed)=COUNT(*)
                            THEN COALESCE(SUM(total_killed),0) END AS total_fatalities,
                        CASE WHEN COUNT(total_injured)=COUNT(*)
                            THEN COALESCE(SUM(total_injured),0) END AS total_injuries
                    FROM crashes
                    WHERE muni_id = :muni_id
                      AND crash_date >= :start_date
                      AND crash_date < :end_date
                ),
                hin_summary AS (
                    SELECT
                        COUNT(*)::integer AS hin_segment_count,
                        COALESCE(SUM(segment.length_miles), 0)::double precision
                            AS hin_miles
                    FROM hin_segments AS hin
                    JOIN road_segments AS segment
                      ON segment.segment_id = hin.segment_id
                    WHERE hin.analysis_id = :analysis_id
                      AND hin.is_significant IS TRUE
                )
                SELECT * FROM crash_summary CROSS JOIN hin_summary
                """
            ),
            {
                "muni_id": analysis.muni_id,
                "analysis_id": analysis.analysis_id,
                "start_date": datetime(analysis.start_year, 1, 1),
                "end_date": datetime(analysis.end_year + 1, 1, 1),
            },
        ).one()
        analysis.total_crashes = summary.total_crashes
        analysis.total_fatalities = summary.total_fatalities
        analysis.total_injuries = summary.total_injuries
        analysis.hin_segment_count = summary.hin_segment_count
        analysis.hin_miles = summary.hin_miles
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
        query = self.db.query(
            HINSegment,
            RoadSegment,
            func.ST_AsGeoJSON(RoadSegment.geom).label("geometry"),
        ).join(RoadSegment).filter(
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
        for hin_seg, segment, geometry_json in hin_segments:
            geometry = (
                json.loads(geometry_json)
                if isinstance(geometry_json, str)
                else geometry_json
            )
            feature = {
                'type': 'Feature',
                'geometry': geometry,
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
