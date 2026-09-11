#!/usr/bin/env python3
"""Load NJDOT public roads and derive municipality analysis segments."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
import json
import logging
import math
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models.database import SessionLocal
from scripts.arcgis_client import ArcGISClient, ArcGISResponseError


LOGGER = logging.getLogger(__name__)
LAYER_URL = (
    "https://services.arcgis.com/HggmsDF7UJsNN1FK/arcgis/rest/services/"
    "NJDOT_Roads_Functional_Class/FeatureServer/6"
)
EXPECTED_FEATURES = 105_977
OUT_FIELDS = ("OBJECTID", "sri", "mp_start", "mp_end", "f_system_code")
METERS_PER_MILE = 1609.344


class IncompatibleRoadRefreshError(RuntimeError):
    """Existing stable IDs describe a different source or split configuration."""


@dataclass(frozen=True)
class AnalysisPath:
    source_id: str
    sri: str
    road_name: str
    road_type: str
    road_class: str
    ewkt: str


@dataclass(frozen=True)
class RoutePath:
    source_id: str
    sri: str
    mp_start: float
    mp_end: float
    ewkt: str


@dataclass(frozen=True)
class ParsedRoadFeature:
    analysis_paths: list[AnalysisPath]
    route_paths: list[RoutePath]
    analysis_rejections: list[str]
    reference_rejections: list[str]


@dataclass(frozen=True)
class BatchResult:
    routes_upserted: int
    analysis_segments_upserted: int
    paths_without_municipality: int


@dataclass
class RoadReport:
    source_count: int = 0
    fetched: int = 0
    accepted_features: int = 0
    rejected_features: int = 0
    analysis_paths: int = 0
    analysis_path_rejections: int = 0
    reference_paths: int = 0
    reference_path_rejections: int = 0
    routes_upserted: int = 0
    analysis_segments_upserted: int = 0
    paths_without_municipality: int = 0
    rejection_details: list[str] = field(default_factory=list)
    analysis_coverage_complete: bool = True
    reference_coverage_complete: bool = True
    partial_coverage_accepted: bool = False


def evaluate_coverage(report: RoadReport, *, allow_partial: bool) -> int:
    """Set explicit coverage flags and return the CLI status for a completed snapshot."""
    source_complete = (
        report.source_count > 0 and report.fetched == report.source_count
    )
    report.analysis_coverage_complete = bool(
        source_complete
        and report.analysis_segments_upserted > 0
        and not (
            report.rejected_features
            or report.analysis_path_rejections
            or report.paths_without_municipality
        )
    )
    report.reference_coverage_complete = bool(
        source_complete
        and report.routes_upserted > 0
        and not (
            report.rejected_features
            or report.analysis_path_rejections
            or report.reference_path_rejections
        )
    )
    coverage_complete = (
        report.analysis_coverage_complete and report.reference_coverage_complete
    )
    report.partial_coverage_accepted = bool(
        not coverage_complete
        and allow_partial
        and report.fetched == report.source_count
        and report.analysis_segments_upserted > 0
    )
    return 0 if coverage_complete or report.partial_coverage_accepted else 3


def _finite_number(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean is not a coordinate")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("coordinate is not finite")
    return number


def _format_number(number: float) -> str:
    return repr(number)


def _ewkt(coordinates: Sequence[tuple[float, ...]], *, measured: bool) -> str:
    values = ",".join(" ".join(_format_number(value) for value in point) for point in coordinates)
    marker = " M" if measured else ""
    return f"SRID=4326;LINESTRING{marker}({values})"


def _road_class(functional_code: Any) -> tuple[str, str]:
    try:
        code_as_float = _finite_number(functional_code)
        code = int(code_as_float)
        if code != code_as_float:
            raise ValueError
    except (TypeError, ValueError):
        return "unclassified", "functional_class_unknown"
    if 1 <= code <= 4:
        road_class = "arterial"
    elif 5 <= code <= 6:
        road_class = "collector"
    elif code == 7:
        road_class = "local"
    else:
        road_class = "unclassified"
    return road_class, f"functional_class_{code}"


class NJDOTRoadIngester:
    """Fetch and load the authoritative M-valued NJDOT road layer."""

    def __init__(
        self,
        client: ArcGISClient | None = None,
        *,
        segment_length_miles: float = 0.1,
        batch_size: int = 1000,
    ) -> None:
        if not math.isfinite(segment_length_miles) or segment_length_miles <= 0:
            raise ValueError("segment_length_miles must be positive and finite")
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self.client = client or ArcGISClient(LAYER_URL)
        self.segment_length_miles = segment_length_miles
        self.batch_size = batch_size
        self.last_report: RoadReport | None = None

    @staticmethod
    def begin_snapshot(db: Session) -> None:
        """Start tracking all path IDs seen in one complete statewide run."""
        db.execute(
            text(
                """
                CREATE TEMP TABLE IF NOT EXISTS seen_njdot_paths (
                    source_id varchar(200) PRIMARY KEY
                ) ON COMMIT DROP
                """
            )
        )
        db.execute(text("TRUNCATE seen_njdot_paths"))

    @staticmethod
    def validate_complete_snapshot(db: Session) -> None:
        """Reject a refresh that omits source paths represented by stable IDs."""
        missing_routes = db.execute(
            text(
                """
                SELECT count(*) FROM road_routes existing
                WHERE existing.source_id LIKE 'NJDOT' || chr(58) || '%'
                  AND NOT EXISTS (
                      SELECT 1 FROM seen_njdot_paths seen
                      WHERE seen.source_id = existing.source_id
                  )
                """
            )
        ).scalar_one()
        missing_segment_paths = db.execute(
            text(
                """
                SELECT count(*) FROM road_segments existing
                LEFT JOIN seen_njdot_paths seen
                  ON seen.source_id = concat(
                      split_part(existing.source_id, chr(58), 1), chr(58),
                      split_part(existing.source_id, chr(58), 2), chr(58),
                      split_part(existing.source_id, chr(58), 3)
                  )
                WHERE existing.source_id LIKE 'NJDOT' || chr(58) || '%'
                  AND seen.source_id IS NULL
                """
            )
        ).scalar_one()
        if missing_routes or missing_segment_paths:
            raise IncompatibleRoadRefreshError(
                "NJDOT snapshot omits paths represented by existing stable IDs; use a fresh database"
            )

    @staticmethod
    def parse_feature(feature: Mapping[str, Any]) -> ParsedRoadFeature:
        attributes = feature.get("attributes")
        geometry = feature.get("geometry")
        if not isinstance(attributes, Mapping) or not isinstance(geometry, Mapping):
            raise ValueError("road feature lacks attributes or geometry")
        object_id = attributes.get("OBJECTID")
        if object_id is None or str(object_id).strip() == "":
            raise ValueError("road feature lacks OBJECTID")
        sri = str(attributes.get("sri") or "").strip().upper()
        if not sri:
            raise ValueError(f"NJDOT:{object_id}: road feature lacks SRI")
        if len(sri) > 20:
            raise ValueError(f"NJDOT:{object_id}: SRI exceeds 20 characters")
        paths = geometry.get("paths")
        if not isinstance(paths, list) or not paths:
            raise ValueError(f"NJDOT:{object_id}: road feature has no paths")
        road_class, road_type = _road_class(attributes.get("f_system_code"))

        analysis_paths: list[AnalysisPath] = []
        route_paths: list[RoutePath] = []
        analysis_rejections: list[str] = []
        reference_rejections: list[str] = []
        for path_index, path in enumerate(paths):
            source_id = f"NJDOT:{object_id}:{path_index}"
            if not isinstance(path, list) or len(path) < 2:
                analysis_rejections.append(f"{source_id}: fewer than two vertices")
                continue
            try:
                xy = [(_finite_number(point[0]), _finite_number(point[1])) for point in path]
                if any(not (-180 <= lon <= 180 and -90 <= lat <= 90) for lon, lat in xy):
                    raise ValueError("longitude/latitude is outside EPSG:4326 bounds")
                if len(set(xy)) < 2:
                    raise ValueError("path has no length")
            except (IndexError, TypeError, ValueError) as exc:
                analysis_rejections.append(f"{source_id}: invalid XY path: {exc}")
                continue

            analysis_paths.append(
                AnalysisPath(
                    source_id=source_id,
                    sri=sri,
                    road_name=sri,
                    road_type=road_type,
                    road_class=road_class,
                    ewkt=_ewkt(xy, measured=False),
                )
            )

            try:
                xym = [
                    (lon, lat, _finite_number(point[2]))
                    for (lon, lat), point in zip(xy, path, strict=True)
                ]
                measures = [point[2] for point in xym]
                deltas = [right - left for left, right in zip(measures, measures[1:])]
                if any(delta > 0 for delta in deltas) and any(delta < 0 for delta in deltas):
                    raise ValueError("M values reverse direction within the path")
            except (IndexError, TypeError, ValueError):
                reference_rejections.append(f"{source_id}: missing or invalid M values")
                continue
            route_paths.append(
                RoutePath(
                    source_id=source_id,
                    sri=sri,
                    mp_start=min(measures),
                    mp_end=max(measures),
                    ewkt=_ewkt(xym, measured=True),
                )
            )

        return ParsedRoadFeature(
            analysis_paths,
            route_paths,
            analysis_rejections,
            reference_rejections,
        )

    def load_batch(
        self,
        db: Session,
        analysis_paths: Iterable[AnalysisPath],
        route_paths: Iterable[RoutePath],
    ) -> BatchResult:
        analysis_paths = list(analysis_paths)
        route_paths = list(route_paths)
        db.execute(
            text(
                """
                CREATE TEMP TABLE IF NOT EXISTS seen_njdot_paths (
                    source_id varchar(200) PRIMARY KEY
                ) ON COMMIT DROP
                """
            )
        )
        db.execute(
            text(
                """
                CREATE TEMP TABLE IF NOT EXISTS staged_road_paths (
                    source_id varchar(200) PRIMARY KEY,
                    sri varchar(20) NOT NULL,
                    road_name varchar(200) NOT NULL,
                    road_type varchar(50) NOT NULL,
                    road_class varchar(20) NOT NULL,
                    geom geometry(LINESTRING, 4326) NOT NULL
                ) ON COMMIT DROP
                """
            )
        )
        db.execute(
            text(
                """
                CREATE TEMP TABLE IF NOT EXISTS staged_road_routes (
                    source_id varchar(200) PRIMARY KEY,
                    sri varchar(20) NOT NULL,
                    mp_start double precision NOT NULL,
                    mp_end double precision NOT NULL,
                    geom geometry(LINESTRINGM, 4326) NOT NULL
                ) ON COMMIT DROP
                """
            )
        )
        db.execute(
            text(
                """
                CREATE TEMP TABLE IF NOT EXISTS staged_road_segments (
                    source_id varchar(200) PRIMARY KEY,
                    sri varchar(20) NOT NULL,
                    road_name varchar(200) NOT NULL,
                    road_type varchar(50) NOT NULL,
                    road_class varchar(20) NOT NULL,
                    length_miles double precision NOT NULL,
                    muni_id integer NOT NULL,
                    geom geometry(LINESTRING, 4326) NOT NULL
                ) ON COMMIT DROP
                """
            )
        )
        db.execute(text("TRUNCATE staged_road_paths"))
        db.execute(text("TRUNCATE staged_road_routes"))
        db.execute(text("TRUNCATE staged_road_segments"))
        if not analysis_paths:
            return BatchResult(0, 0, 0)
        analysis_payload = json.dumps(
            [
                {
                    "source_id": path.source_id,
                    "sri": path.sri,
                    "road_name": path.road_name,
                    "road_type": path.road_type,
                    "road_class": path.road_class,
                    "geom": path.ewkt,
                }
                for path in analysis_paths
            ],
            separators=(",", ":"),
        )
        db.execute(
            text(
                """
                INSERT INTO staged_road_paths
                    (source_id, sri, road_name, road_type, road_class, geom)
                SELECT source_id, sri, road_name, road_type, road_class,
                       ST_Force2D(ST_GeomFromEWKT(geom))
                FROM jsonb_to_recordset(CAST(:records AS jsonb)) AS record(
                    source_id text, sri text, road_name text, road_type text,
                    road_class text, geom text
                )
                """
            ),
            {"records": analysis_payload},
        )
        db.execute(
            text(
                """
                INSERT INTO seen_njdot_paths (source_id)
                SELECT source_id FROM staged_road_paths
                ON CONFLICT (source_id) DO NOTHING
                """
            )
        )
        if route_paths:
            route_payload = json.dumps(
                [
                    {
                        "source_id": route.source_id,
                        "sri": route.sri,
                        "mp_start": route.mp_start,
                        "mp_end": route.mp_end,
                        "geom": route.ewkt,
                    }
                    for route in route_paths
                ],
                separators=(",", ":"),
            )
            db.execute(
                text(
                    """
                    INSERT INTO staged_road_routes (source_id, sri, mp_start, mp_end, geom)
                    SELECT source_id, sri, mp_start, mp_end, ST_GeomFromEWKT(geom)
                    FROM jsonb_to_recordset(CAST(:records AS jsonb)) AS record(
                        source_id text, sri text, mp_start double precision,
                        mp_end double precision, geom text
                    )
                    """
                ),
                {"records": route_payload},
            )

        route_conflicts = db.execute(
            text(
                """
                SELECT count(*)
                FROM road_routes existing
                JOIN staged_road_routes desired USING (source_id)
                WHERE existing.sri IS DISTINCT FROM desired.sri
                   OR existing.mp_start IS DISTINCT FROM desired.mp_start
                   OR existing.mp_end IS DISTINCT FROM desired.mp_end
                   OR ST_AsEWKB(existing.geom) <> ST_AsEWKB(desired.geom)
                """
            )
        ).scalar_one()
        missing_measures = db.execute(
            text(
                """
                SELECT count(*)
                FROM road_routes existing
                JOIN staged_road_paths path USING (source_id)
                LEFT JOIN staged_road_routes desired USING (source_id)
                WHERE desired.source_id IS NULL
                """
            )
        ).scalar_one()
        if route_conflicts or missing_measures:
            raise IncompatibleRoadRefreshError(
                "NJDOT route measures differ from existing stable IDs; use a fresh database"
            )

        paths_without_municipality = db.execute(
            text(
                """
                SELECT count(*) FROM staged_road_paths path
                WHERE NOT EXISTS (
                    SELECT 1 FROM municipalities municipality
                    WHERE path.geom && municipality.geom
                      AND ST_Intersects(path.geom, municipality.geom)
                      AND ST_Dimension(ST_Intersection(path.geom, municipality.geom)) = 1
                )
                """
            )
        ).scalar_one()
        segment_length_meters = self.segment_length_miles * METERS_PER_MILE
        db.execute(
            text(
                """
                WITH intersections AS (
                    SELECT path.source_id AS path_source_id,
                           path.sri,
                           path.road_name,
                           path.road_type,
                           path.road_class,
                           municipality.muni_id,
                           municipality.muni_code,
                           ST_CollectionExtract(ST_MakeValid(
                               CASE
                                   WHEN ST_CoveredBy(path.geom, municipality.geom) THEN path.geom
                                   ELSE ST_Intersection(path.geom, municipality.geom)
                               END
                           ), 2) AS geom
                    FROM staged_road_paths path
                    JOIN municipalities municipality
                      ON path.geom && municipality.geom
                     AND ST_Intersects(path.geom, municipality.geom)
                ),
                parts AS (
                    SELECT intersections.*,
                           (dumped).path AS part_path,
                           ST_Force2D((dumped).geom) AS geom_4326
                    FROM intersections
                    CROSS JOIN LATERAL ST_Dump(intersections.geom) AS dumped
                ),
                metric_parts AS (
                    SELECT parts.*,
                           ST_Transform(geom_4326, 26918) AS geom_metric
                    FROM parts
                    WHERE GeometryType(geom_4326) = 'LINESTRING'
                      AND NOT ST_IsEmpty(geom_4326)
                      AND ST_Length(geom_4326::geography) > 0
                ),
                piece_parameters AS (
                    SELECT metric_parts.*,
                           GREATEST(1, CEIL(ST_Length(geom_metric) / :segment_length_meters)::integer)
                               AS piece_count
                    FROM metric_parts
                ),
                pieces AS (
                    SELECT piece_parameters.*,
                           piece_index,
                           ST_Transform(
                               ST_LineSubstring(
                                   geom_metric,
                                   piece_index::double precision / piece_count,
                                   (piece_index + 1)::double precision / piece_count
                               ),
                               4326
                           ) AS piece_geom
                    FROM piece_parameters
                    CROSS JOIN LATERAL generate_series(0, piece_count - 1) AS piece_index
                )
                INSERT INTO staged_road_segments
                    (source_id, sri, road_name, road_type, road_class,
                     length_miles, muni_id, geom)
                SELECT concat(
                               path_source_id, chr(58), 'M', muni_code,
                               chr(58), 'P',
                               COALESCE(NULLIF(array_to_string(part_path, '.'), ''), '0'),
                               chr(58), 'S', piece_index
                           ),
                           sri, road_name, road_type, road_class,
                           ST_Length(piece_geom::geography) / :meters_per_mile,
                           muni_id, ST_Force2D(piece_geom)
                FROM pieces
                WHERE NOT ST_IsEmpty(piece_geom)
                  AND ST_Length(piece_geom::geography) > 0
                """
            ),
            {
                "segment_length_meters": segment_length_meters,
                "meters_per_mile": METERS_PER_MILE,
            },
        )

        segment_conflicts = db.execute(
            text(
                """
                SELECT count(*)
                FROM road_segments existing
                JOIN staged_road_segments desired USING (source_id)
                WHERE existing.sri IS DISTINCT FROM desired.sri
                   OR existing.road_name IS DISTINCT FROM desired.road_name
                   OR existing.road_type IS DISTINCT FROM desired.road_type
                   OR existing.road_class IS DISTINCT FROM desired.road_class
                   OR existing.muni_id IS DISTINCT FROM desired.muni_id
                   OR abs(existing.length_miles - desired.length_miles) > 1e-10
                   OR NOT ST_OrderingEquals(existing.geom, desired.geom)
                """
            )
        ).scalar_one()
        stale_segments = db.execute(
            text(
                """
                SELECT count(*)
                FROM road_segments existing
                JOIN staged_road_paths path
                  ON path.source_id = concat(
                      split_part(existing.source_id, chr(58), 1), chr(58),
                      split_part(existing.source_id, chr(58), 2), chr(58),
                      split_part(existing.source_id, chr(58), 3)
                  )
                LEFT JOIN staged_road_segments desired
                  ON desired.source_id = existing.source_id
                WHERE desired.source_id IS NULL
                """
            )
        ).scalar_one()
        if segment_conflicts or stale_segments:
            raise IncompatibleRoadRefreshError(
                "NJDOT source, boundaries, or segment length differ from existing stable IDs; "
                "use a fresh database"
            )

        if route_paths:
            db.execute(
                text(
                    """
                    INSERT INTO road_routes (source_id, sri, mp_start, mp_end, geom)
                    SELECT source_id, sri, mp_start, mp_end, geom FROM staged_road_routes
                    ON CONFLICT (source_id) DO UPDATE SET
                        sri = EXCLUDED.sri,
                        mp_start = EXCLUDED.mp_start,
                        mp_end = EXCLUDED.mp_end,
                        geom = EXCLUDED.geom
                    """
                )
            )
        db.execute(
            text(
                """
                INSERT INTO road_segments
                    (source_id, sri, osm_id, road_name, road_type, road_class,
                     length_miles, muni_id, geom)
                SELECT source_id, sri, NULL, road_name, road_type, road_class,
                       length_miles, muni_id, geom
                FROM staged_road_segments
                ON CONFLICT (source_id) DO UPDATE SET
                    sri = EXCLUDED.sri,
                    osm_id = NULL,
                    road_name = EXCLUDED.road_name,
                    road_type = EXCLUDED.road_type,
                    road_class = EXCLUDED.road_class,
                    length_miles = EXCLUDED.length_miles,
                    muni_id = EXCLUDED.muni_id,
                    geom = EXCLUDED.geom
                """
            )
        )
        routes_upserted = len(route_paths)
        segment_count = db.execute(text("SELECT count(*) FROM staged_road_segments")).scalar_one()
        return BatchResult(routes_upserted, segment_count, paths_without_municipality)

    def ingest(self, db: Session) -> RoadReport:
        self.client.metadata(
            geometry_type="esriGeometryPolyline",
            required_fields=OUT_FIELDS,
            require_m=True,
        )
        source_count = self.client.count()
        if source_count != EXPECTED_FEATURES:
            LOGGER.warning(
                "NJDOT road source count changed from observed baseline %s to %s; "
                "continuing with dynamic pagination validation",
                EXPECTED_FEATURES,
                source_count,
            )
        report = RoadReport(source_count=source_count)
        self.last_report = report
        self.begin_snapshot(db)
        analysis_batch: list[AnalysisPath] = []
        route_batch: list[RoutePath] = []

        def flush_batch() -> None:
            if not analysis_batch and not route_batch:
                return
            result = self.load_batch(db, analysis_batch, route_batch)
            report.routes_upserted += result.routes_upserted
            report.analysis_segments_upserted += result.analysis_segments_upserted
            report.paths_without_municipality += result.paths_without_municipality
            analysis_batch.clear()
            route_batch.clear()

        for feature in self.client.iter_features(
            out_fields=OUT_FIELDS,
            output_format="json",
            return_m=True,
        ):
            report.fetched += 1
            try:
                parsed = self.parse_feature(feature)
            except ValueError as exc:
                report.rejected_features += 1
                report.rejection_details.append(str(exc))
                continue
            if parsed.analysis_paths:
                report.accepted_features += 1
            else:
                report.rejected_features += 1
            report.analysis_paths += len(parsed.analysis_paths)
            report.reference_paths += len(parsed.route_paths)
            report.analysis_path_rejections += len(parsed.analysis_rejections)
            report.reference_path_rejections += len(parsed.reference_rejections)
            report.rejection_details.extend(parsed.analysis_rejections)
            report.rejection_details.extend(parsed.reference_rejections)
            analysis_batch.extend(parsed.analysis_paths)
            route_batch.extend(parsed.route_paths)
            if len(analysis_batch) >= self.batch_size:
                flush_batch()
        flush_batch()

        if report.fetched != source_count:
            raise ArcGISResponseError(
                f"road pagination mismatch: source reported {source_count}, fetched {report.fetched}"
            )
        self.validate_complete_snapshot(db)
        evaluate_coverage(report, allow_partial=False)
        return report


def _write_report(path: Path | None, report: RoadReport) -> None:
    payload = json.dumps(asdict(report), indent=2, sort_keys=True)
    LOGGER.info("NJDOT road ingestion report:\n%s", payload)
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingest official NJDOT all-public-road geometry")
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--segment-length", type=float, default=0.1)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help=(
            "Accept documented record-level omissions after complete source pagination "
            "when a nonempty analysis network was loaded"
        ),
    )
    parser.add_argument("--report", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.offline and not args.cache_dir:
        LOGGER.error("--offline requires --cache-dir")
        return 2
    try:
        client = ArcGISClient(
            LAYER_URL,
            cache_dir=args.cache_dir,
            offline=args.offline,
            refresh_cache=args.refresh_cache,
        )
        ingester = NJDOTRoadIngester(
            client,
            segment_length_miles=args.segment_length,
            batch_size=args.batch_size,
        )
    except ValueError as exc:
        LOGGER.error("Invalid road ingestion configuration: %s", exc)
        return 2

    db = SessionLocal()
    report: RoadReport | None = None
    try:
        with db.begin():
            report = ingester.ingest(db)
        status = evaluate_coverage(report, allow_partial=args.allow_partial)
        _write_report(args.report, report)
        if status:
            LOGGER.error("NJDOT road coverage is incomplete; see the ingestion report")
        elif report.partial_coverage_accepted:
            LOGGER.warning("Accepted documented partial NJDOT road coverage by explicit opt-in")
        return status
    except Exception:
        db.rollback()
        LOGGER.exception("NJDOT road ingestion failed")
        failure_report = report or ingester.last_report
        if failure_report:
            _write_report(args.report, failure_report)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    raise SystemExit(main())
