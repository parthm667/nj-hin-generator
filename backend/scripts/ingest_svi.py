#!/usr/bin/env python3
"""Load the official 2020 CDC/ATSDR national-rank SVI snapshot for NJ.

The CDC ``RPL_THEMES`` value is a percentile rank from 0 to 1. This loader
stores it on the application's established 0-to-100 percentile scale. CDC's
``-999`` no-data sentinel is stored as NULL rather than as a numeric score.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import sys
import time
from typing import Any, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models.database import SessionLocal
from scripts.arcgis_client import ArcGISClient, ArcGISResponseError


LOGGER = logging.getLogger(__name__)
CDC_SVI_2020_LAYER_URL = (
    "https://services1.arcgis.com/o90r8yeUBWgKSezU/ArcGIS/rest/services/"
    "SVI2020_US_tract/FeatureServer/0"
)
CDC_SVI_2020_SOURCE_NAME = "CDC/ATSDR SVI 2020 U.S. tract national rank"
CDC_SVI_DOCUMENTATION_URL = (
    "https://www.atsdr.cdc.gov/place-health/php/svi/"
    "svi-data-documentation-download.html"
)
SOURCE_YEAR = 2020
NJ_WHERE = "ST_ABBR = 'NJ'"
EXPECTED_NJ_TRACTS = 2_175
OUT_FIELDS = (
    "FID",
    "FIPS",
    "ST_ABBR",
    "E_TOTPOP",
    "EP_NOVEH",
    "EP_MINRTY",
    "RPL_THEMES",
)


@dataclass(frozen=True)
class SVITract:
    tract_id: str
    county_fips: str
    total_population: int | None
    median_income: int | None
    pct_no_vehicle: float | None
    pct_minority: float | None
    svi_score: float | None
    svi_percentile: float | None
    source_year: int
    source_name: str
    geometry: Mapping[str, Any]


@dataclass
class SVIReport:
    source_url: str = CDC_SVI_2020_LAYER_URL
    documentation_url: str = CDC_SVI_DOCUMENTATION_URL
    source_name: str = CDC_SVI_2020_SOURCE_NAME
    source_year: int = SOURCE_YEAR
    ranking_scope: str = "United States census tracts"
    source_count: int = 0
    fetched: int = 0
    accepted: int = 0
    rejected: int = 0
    missing_svi: int = 0
    upserted: int = 0
    rejections: Counter = field(default_factory=Counter)
    status: str = "pending"
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["rejections"] = dict(sorted(self.rejections.items()))
        return payload


def _optional_number(
    value: Any,
    field_name: str,
    *,
    minimum: float,
    maximum: float | None = None,
) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(field_name)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(field_name) from exc
    if number == -999:
        return None
    if not math.isfinite(number) or number < minimum:
        raise ValueError(field_name)
    if maximum is not None and number > maximum:
        raise ValueError(field_name)
    return number


def _optional_integer(value: Any, field_name: str) -> int | None:
    number = _optional_number(value, field_name, minimum=0)
    if number is None:
        return None
    integer = int(number)
    if integer != number:
        raise ValueError(field_name)
    return integer


def normalize_svi_feature(feature: Mapping[str, Any]) -> SVITract:
    """Validate one CDC GeoJSON feature without inventing missing values."""
    properties = feature.get("properties")
    if not isinstance(properties, Mapping):
        raise ValueError("properties")
    if properties.get("ST_ABBR") != "NJ":
        raise ValueError("state")

    tract_id = str(properties.get("FIPS") or "").strip()
    if len(tract_id) != 11 or not tract_id.isdigit() or not tract_id.startswith("34"):
        raise ValueError("tract_id")

    geometry = feature.get("geometry")
    if not isinstance(geometry, Mapping) or geometry.get("type") not in {
        "Polygon",
        "MultiPolygon",
    }:
        raise ValueError("geometry")
    if not geometry.get("coordinates"):
        raise ValueError("geometry")

    rank = _optional_number(
        properties.get("RPL_THEMES"),
        "svi_percentile",
        minimum=0,
        maximum=1,
    )
    percentile = None if rank is None else round(rank * 100, 6)
    return SVITract(
        tract_id=tract_id,
        county_fips=tract_id[2:5],
        total_population=_optional_integer(
            properties.get("E_TOTPOP"), "total_population"
        ),
        # Median household income is not an SVI 2020 source field.
        median_income=None,
        pct_no_vehicle=_optional_number(
            properties.get("EP_NOVEH"),
            "pct_no_vehicle",
            minimum=0,
            maximum=100,
        ),
        pct_minority=_optional_number(
            properties.get("EP_MINRTY"),
            "pct_minority",
            minimum=0,
            maximum=100,
        ),
        # The source has a rank, not a distinct unranked composite score. Store
        # the official rank in both legacy application fields for compatibility.
        svi_score=percentile,
        svi_percentile=percentile,
        source_year=SOURCE_YEAR,
        source_name=CDC_SVI_2020_SOURCE_NAME,
        geometry=geometry,
    )


LOAD_BATCH_SQL = text(
    """
    WITH input AS (
        SELECT *
        FROM jsonb_to_recordset(CAST(:records AS jsonb)) AS item(
            tract_id text,
            county_fips text,
            total_population integer,
            median_income integer,
            pct_no_vehicle double precision,
            pct_minority double precision,
            svi_score double precision,
            svi_percentile double precision,
            source_year integer,
            source_name text,
            geometry jsonb
        )
    ), normalized AS (
        SELECT
            input.*,
            ST_Multi(
                ST_CollectionExtract(
                    ST_MakeValid(
                        ST_SetSRID(ST_GeomFromGeoJSON(input.geometry::text), 4326)
                    ),
                    3
                )
            )::geometry(MultiPolygon, 4326) AS geom
        FROM input
    )
    INSERT INTO census_tracts (
        tract_id,
        county_fips,
        total_population,
        median_income,
        pct_no_vehicle,
        pct_minority,
        svi_score,
        svi_percentile,
        source_year,
        source_name,
        geom
    )
    SELECT
        tract_id,
        county_fips,
        total_population,
        median_income,
        pct_no_vehicle,
        pct_minority,
        svi_score,
        svi_percentile,
        source_year,
        source_name,
        geom
    FROM normalized
    WHERE NOT ST_IsEmpty(geom)
    ON CONFLICT (tract_id) DO UPDATE SET
        county_fips = EXCLUDED.county_fips,
        total_population = EXCLUDED.total_population,
        median_income = EXCLUDED.median_income,
        pct_no_vehicle = EXCLUDED.pct_no_vehicle,
        pct_minority = EXCLUDED.pct_minority,
        svi_score = EXCLUDED.svi_score,
        svi_percentile = EXCLUDED.svi_percentile,
        source_year = EXCLUDED.source_year,
        source_name = EXCLUDED.source_name,
        geom = EXCLUDED.geom
    """
)


def load_svi_batch(
    database: Session,
    records: Sequence[SVITract],
    report: SVIReport,
) -> None:
    if not records:
        return
    payload = [asdict(record) for record in records]
    result = database.execute(LOAD_BATCH_SQL, {"records": json.dumps(payload)})
    affected = result.rowcount
    if affected != len(records):
        raise ValueError(
            f"geometry load mismatch: expected {len(records)}, upserted {affected}"
        )
    report.upserted += affected


class CDC2020SVIIngester:
    def __init__(
        self,
        client: ArcGISClient | None = None,
        *,
        batch_size: int = 500,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self.client = client or ArcGISClient(CDC_SVI_2020_LAYER_URL)
        self.batch_size = batch_size

    def ingest(self, database: Session, report: SVIReport) -> None:
        self.client.metadata(
            geometry_type="esriGeometryPolygon",
            required_fields=OUT_FIELDS,
        )
        report.source_count = self.client.count(NJ_WHERE)
        if report.source_count != EXPECTED_NJ_TRACTS:
            raise ArcGISResponseError(
                "CDC SVI 2020 NJ tract count changed: "
                f"expected {EXPECTED_NJ_TRACTS}, got {report.source_count}"
            )

        batch: list[SVITract] = []
        for feature in self.client.iter_features(
            out_fields=OUT_FIELDS,
            where=NJ_WHERE,
            output_format="geojson",
            out_sr=4326,
        ):
            report.fetched += 1
            try:
                record = normalize_svi_feature(feature)
            except ValueError as exc:
                report.rejected += 1
                report.rejections[str(exc)] += 1
                continue
            report.accepted += 1
            if record.svi_percentile is None:
                report.missing_svi += 1
            batch.append(record)
            if len(batch) >= self.batch_size:
                load_svi_batch(database, batch, report)
                database.commit()
                batch.clear()
        if batch:
            load_svi_batch(database, batch, report)
            database.commit()

        if report.fetched != report.source_count:
            raise ArcGISResponseError(
                f"feature count mismatch: expected {report.source_count}, "
                f"fetched {report.fetched}"
            )
        if report.rejected:
            raise ValueError(f"rejected {report.rejected} CDC SVI features")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    for attempt in range(5):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05 * (2**attempt))


def run_ingestion(args: argparse.Namespace, *, database_factory=SessionLocal) -> int:
    report = SVIReport(status="running", started_at=_utc_now())
    report_path = Path(args.report)
    _atomic_json(report_path, report.as_dict())
    database = database_factory()
    try:
        client = ArcGISClient(
            CDC_SVI_2020_LAYER_URL,
            cache_dir=args.cache_dir,
            offline=args.offline,
            refresh_cache=args.refresh_cache,
        )
        CDC2020SVIIngester(client, batch_size=args.batch_size).ingest(database, report)
        report.status = "succeeded"
        return 0
    except Exception as exc:
        database.rollback()
        report.status = "failed"
        report.error = str(exc)
        LOGGER.exception("CDC SVI ingestion failed")
        return 1
    finally:
        database.close()
        report.finished_at = _utc_now()
        _atomic_json(report_path, report.as_dict())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Load official 2020 CDC/ATSDR U.S.-ranked SVI tracts for NJ"
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=PROJECT_DIR / "data" / "raw" / "official" / "svi-2020",
    )
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument(
        "--report",
        type=Path,
        default=PROJECT_DIR / "data" / "processed" / "svi-2020-report.json",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = build_parser().parse_args(argv)
    if args.offline and args.refresh_cache:
        raise SystemExit("--offline and --refresh-cache cannot be combined")
    return run_ingestion(args)


if __name__ == "__main__":
    raise SystemExit(main())
