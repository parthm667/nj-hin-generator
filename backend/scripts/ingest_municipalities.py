#!/usr/bin/env python3
"""Load official NJGIN municipal boundaries into PostGIS."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import logging
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping

from sqlalchemy import text
from sqlalchemy.orm import Session

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models.database import SessionLocal
from scripts.arcgis_client import ArcGISClient, ArcGISResponseError


LOGGER = logging.getLogger(__name__)
LAYER_URL = (
    "https://services2.arcgis.com/XVOqAjTOJ5P6ngMu/ArcGIS/rest/services/"
    "NJ_Municipal_Boundaries_3424/FeatureServer/0"
)
EXPECTED_MUNICIPALITIES = 564
OUT_FIELDS = ("OBJECTID", "MUN_CODE", "MUN_LABEL", "COUNTY", "MUN_TYPE", "NAME")


@dataclass(frozen=True)
class MunicipalityRecord:
    muni_code: str
    name: str
    county: str
    muni_type: str
    geometry: dict[str, Any]

    @property
    def geometry_json(self) -> str:
        return json.dumps(self.geometry, separators=(",", ":"))


@dataclass
class MunicipalityReport:
    source_count: int = 0
    fetched: int = 0
    accepted: int = 0
    rejected: int = 0
    inserted: int = 0
    updated: int = 0
    rejection_details: list[str] | None = None

    def __post_init__(self) -> None:
        if self.rejection_details is None:
            self.rejection_details = []


def _multipolygon_geojson(geometry: Mapping[str, Any]) -> dict[str, Any]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "Polygon":
        coordinates = [coordinates]
    elif geometry_type != "MultiPolygon":
        raise ValueError(f"expected Polygon or MultiPolygon, got {geometry_type!r}")
    if not isinstance(coordinates, list) or not coordinates:
        raise ValueError("boundary has no polygon coordinates")
    return {"type": "MultiPolygon", "coordinates": coordinates}


def normalize_municipality(feature: Mapping[str, Any]) -> MunicipalityRecord:
    """Validate one NJGIN GeoJSON feature and normalize its database values."""
    properties = feature.get("properties")
    geometry_data = feature.get("geometry")
    if not isinstance(properties, Mapping) or not isinstance(geometry_data, Mapping):
        raise ValueError("feature lacks properties or geometry")

    muni_code = str(properties.get("MUN_CODE") or "").strip()
    name = str(properties.get("MUN_LABEL") or properties.get("NAME") or "").strip()
    county = str(properties.get("COUNTY") or "").strip().title()
    muni_type = str(properties.get("MUN_TYPE") or "").strip()
    if not muni_code or not name or not county or not muni_type:
        raise ValueError("feature lacks MUN_CODE, municipality name, COUNTY, or MUN_TYPE")

    try:
        geometry = _multipolygon_geojson(geometry_data)
    except Exception as exc:
        raise ValueError(f"invalid boundary geometry: {exc}") from exc
    return MunicipalityRecord(muni_code, name, county, muni_type, geometry)


class MunicipalityIngester:
    """Fetch, validate, and idempotently upsert statewide boundaries."""

    def __init__(self, client: ArcGISClient | None = None) -> None:
        self.client = client or ArcGISClient(LAYER_URL)

    def fetch_municipalities(self) -> tuple[list[MunicipalityRecord], MunicipalityReport]:
        self.client.metadata(
            geometry_type="esriGeometryPolygon",
            required_fields=OUT_FIELDS,
        )
        source_count = self.client.count()
        if source_count != EXPECTED_MUNICIPALITIES:
            LOGGER.warning(
                "Municipality source count changed from observed baseline %s to %s; "
                "continuing with dynamic pagination validation",
                EXPECTED_MUNICIPALITIES,
                source_count,
            )

        report = MunicipalityReport(source_count=source_count)
        records: list[MunicipalityRecord] = []
        seen_codes: set[str] = set()
        for index, feature in enumerate(
            self.client.iter_features(out_fields=OUT_FIELDS, output_format="geojson")
        ):
            report.fetched += 1
            try:
                record = normalize_municipality(feature)
                if record.muni_code in seen_codes:
                    raise ValueError(f"duplicate MUN_CODE {record.muni_code}")
                seen_codes.add(record.muni_code)
                records.append(record)
                report.accepted += 1
            except ValueError as exc:
                report.rejected += 1
                report.rejection_details.append(f"feature {index}: {exc}")

        if report.fetched != source_count:
            raise ArcGISResponseError(
                f"municipality pagination mismatch: source reported {source_count}, fetched {report.fetched}"
            )
        if report.rejected:
            raise ArcGISResponseError(
                f"rejected {report.rejected} municipality features: "
                + "; ".join(report.rejection_details[:5])
            )
        return records, report

    @staticmethod
    def load_to_db(
        municipalities: Iterable[MunicipalityRecord], db: Session, report: MunicipalityReport
    ) -> MunicipalityReport:
        records = list(municipalities)
        existing = set(
            db.execute(
                text("SELECT muni_code FROM municipalities WHERE muni_code = ANY(:codes)"),
                {"codes": [record.muni_code for record in records]},
            ).scalars()
        )
        db.execute(
            text(
                """
                CREATE TEMP TABLE IF NOT EXISTS staged_municipalities (
                    muni_code varchar(20) PRIMARY KEY,
                    name varchar(100) NOT NULL,
                    county varchar(50) NOT NULL,
                    geometry_json text NOT NULL
                ) ON COMMIT DROP
                """
            )
        )
        db.execute(text("TRUNCATE staged_municipalities"))
        stage_statement = text(
            """
            INSERT INTO staged_municipalities (muni_code, name, county, geometry_json)
            VALUES (:muni_code, :name, :county, :geometry_json)
            """
        )
        values = [
            {
                "name": record.name,
                "county": record.county,
                "muni_code": record.muni_code,
                "geometry_json": record.geometry_json,
            }
            for record in records
        ]
        if values:
            db.execute(stage_statement, values)

        invalid = db.execute(
            text(
                """
                WITH normalized AS (
                    SELECT muni_code,
                           ST_Multi(ST_CollectionExtract(ST_MakeValid(
                               ST_SetSRID(ST_GeomFromGeoJSON(geometry_json), 4326)
                           ), 3)) AS geom
                    FROM staged_municipalities
                )
                SELECT muni_code FROM normalized
                WHERE ST_IsEmpty(geom) OR NOT ST_IsValid(geom)
                """
            )
        ).scalars().all()
        if invalid:
            raise ValueError(f"invalid municipality geometries after repair: {invalid[:5]}")

        statement = text(
            """
            INSERT INTO municipalities (name, county, muni_code, geom)
            SELECT name, county, muni_code,
                   ST_Multi(ST_CollectionExtract(ST_MakeValid(
                       ST_SetSRID(ST_GeomFromGeoJSON(geometry_json), 4326)
                   ), 3))
            FROM staged_municipalities
            ON CONFLICT (muni_code) DO UPDATE SET
                name = EXCLUDED.name,
                county = EXCLUDED.county,
                geom = EXCLUDED.geom
            """
        )
        if values:
            db.execute(statement)
        report.updated = len(existing)
        report.inserted = len(records) - report.updated
        return report


def _write_report(path: Path | None, report: MunicipalityReport) -> None:
    payload = json.dumps(asdict(report), indent=2, sort_keys=True)
    LOGGER.info("Municipality ingestion report:\n%s", payload)
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingest official NJGIN municipal boundaries")
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--report", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.offline and not args.cache_dir:
        LOGGER.error("--offline requires --cache-dir")
        return 2
    client = ArcGISClient(
        LAYER_URL,
        cache_dir=args.cache_dir,
        offline=args.offline,
        refresh_cache=args.refresh_cache,
    )
    ingester = MunicipalityIngester(client)
    db = SessionLocal()
    try:
        records, report = ingester.fetch_municipalities()
        with db.begin():
            ingester.load_to_db(records, db, report)
        _write_report(args.report, report)
        return 0
    except Exception:
        db.rollback()
        LOGGER.exception("Municipality ingestion failed")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    raise SystemExit(main())
