#!/usr/bin/env python3
"""Load the explicitly synthetic development dataset into an initialized database."""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
DEFAULT_DATA_DIR = PROJECT_DIR / "data" / "sample"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy.orm import Session

from app.models.database import SessionLocal
from app.models.tables import CensusTract, Crash, Municipality, RoadSegment

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SAMPLE_FILES = (
    "municipalities.json",
    "road_segments.json",
    "crashes.json",
    "census_tracts.json",
)


def _read_json(data_file: Path) -> list[dict]:
    with data_file.open(encoding="utf-8") as file_handle:
        records = json.load(file_handle)
    if not isinstance(records, list):
        raise ValueError(f"Expected a JSON array in {data_file}")
    return records


def _ring_wkt(ring: list[list[float]]) -> str:
    if len(ring) < 4 or ring[0] != ring[-1]:
        raise ValueError("Polygon rings must contain at least four positions and be closed")
    return f"({', '.join(f'{lon} {lat}' for lon, lat in ring)})"


def multipolygon_wkt(geometry: dict) -> str:
    """Convert Polygon or MultiPolygon GeoJSON into MULTIPOLYGON WKT."""
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "Polygon":
        polygons = [coordinates]
    elif geometry_type == "MultiPolygon":
        polygons = coordinates
    else:
        raise ValueError(f"Expected Polygon or MultiPolygon, got {geometry_type!r}")

    if not polygons:
        raise ValueError("Polygon geometry must not be empty")
    polygon_wkts = [f"({', '.join(_ring_wkt(ring) for ring in polygon)})" for polygon in polygons]
    return f"SRID=4326;MULTIPOLYGON({', '.join(polygon_wkts)})"


def load_municipalities(data_file: Path, db: Session) -> tuple[int, dict[int, int]]:
    """Load municipalities and map synthetic source IDs to database IDs."""
    loaded_count = 0
    municipality_ids: dict[int, int] = {}
    for item in _read_json(data_file):
        municipality = db.query(Municipality).filter(
            Municipality.muni_code == item["muni_code"]
        ).first()
        if municipality is None:
            municipality = Municipality(
                name=item["name"],
                county=item["county"],
                muni_code=item["muni_code"],
                geom=multipolygon_wkt(item["boundary"]),
            )
            db.add(municipality)
            db.flush()
            loaded_count += 1
        municipality_ids[int(item["muni_id"])] = municipality.muni_id
    db.commit()
    logger.info("Municipalities inserted: %s", loaded_count)
    return loaded_count, municipality_ids


def load_road_segments(data_file: Path, db: Session, municipality_ids: dict[int, int]) -> int:
    """Load synthetic road segments without treating cross-municipality IDs as duplicates."""
    loaded_count = 0
    for item in _read_json(data_file):
        municipality_id = municipality_ids[int(item["muni_id"])]
        existing = db.query(RoadSegment).filter(
            RoadSegment.osm_id == item["osm_id"],
            RoadSegment.muni_id == municipality_id,
        ).first()
        if existing is not None:
            continue

        coordinates = item["geometry"]["coordinates"]
        coordinate_wkt = ", ".join(f"{lon} {lat}" for lon, lat in coordinates)
        db.add(RoadSegment(
            osm_id=item["osm_id"],
            road_name=item["road_name"],
            road_type=item["road_type"],
            road_class=item["road_class"],
            length_miles=item["length_miles"],
            muni_id=municipality_id,
            geom=f"SRID=4326;LINESTRING({coordinate_wkt})",
        ))
        loaded_count += 1
    db.commit()
    logger.info("Road segments inserted: %s", loaded_count)
    return loaded_count


def load_crashes(data_file: Path, db: Session, municipality_ids: dict[int, int]) -> int:
    """Load crashes with deterministic, idempotent synthetic identifiers."""
    loaded_count = 0
    for item in _read_json(data_file):
        source_municipality_id = int(item["muni_id"])
        external_id = item["external_id"]
        if not external_id.startswith("SAMPLE-"):
            external_id = f"SAMPLE-{source_municipality_id}-{external_id}"
        if db.query(Crash).filter(Crash.external_id == external_id).first() is not None:
            continue

        db.add(Crash(
            external_id=external_id,
            crash_date=datetime.strptime(item["crash_date"], "%Y-%m-%d").date(),
            crash_time=item.get("crash_time"),
            severity=item["severity"],
            ped_involved=item["ped_involved"],
            bike_involved=item["bike_involved"],
            muni_id=municipality_ids[source_municipality_id],
            road_name=item.get("road_name"),
            geocode_quality=item.get("geocode_quality", "medium"),
            weather_condition=item.get("weather_condition"),
            light_condition=item.get("light_condition"),
            geom=f"SRID=4326;POINT({item['longitude']} {item['latitude']})",
        ))
        loaded_count += 1
        if loaded_count % 500 == 0:
            db.commit()
            logger.info("Crashes inserted so far: %s", loaded_count)
    db.commit()
    logger.info("Crashes inserted: %s", loaded_count)
    return loaded_count


def load_census_tracts(data_file: Path, db: Session, municipality_ids: dict[int, int]) -> int:
    """Load synthetic census tracts using the database municipality IDs."""
    loaded_count = 0
    for item in _read_json(data_file):
        if db.query(CensusTract).filter(CensusTract.tract_id == item["tract_id"]).first() is not None:
            continue
        source_municipality_id = item.get("muni_id")
        db.add(CensusTract(
            tract_id=item["tract_id"],
            muni_id=municipality_ids[int(source_municipality_id)] if source_municipality_id else None,
            county_fips=item.get("county_fips"),
            total_population=item.get("total_population", 0),
            median_income=item.get("median_income", 0),
            pct_below_poverty=item.get("pct_below_poverty", 0),
            pct_no_vehicle=item.get("pct_no_vehicle", 0),
            pct_minority=item.get("pct_minority", 0),
            svi_score=item.get("svi_score", 0),
            svi_percentile=item.get("svi_percentile", 0),
            geom=multipolygon_wkt(item["geometry"]),
        ))
        loaded_count += 1
    db.commit()
    logger.info("Census tracts inserted: %s", loaded_count)
    return loaded_count


def parse_args():
    parser = argparse.ArgumentParser(
        description="Load the synthetic sample seed into an already initialized database."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"Synthetic JSON directory (default: {DEFAULT_DATA_DIR})",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data_dir = args.data_dir.expanduser().resolve()
    missing_files = [name for name in SAMPLE_FILES if not (data_dir / name).is_file()]
    if missing_files:
        raise FileNotFoundError(
            f"Synthetic sample data is incomplete in {data_dir}; missing: {', '.join(missing_files)}. "
            "Run scripts/generate_sample_data.py first."
        )

    logger.info("Loading explicitly synthetic sample data from %s", data_dir)
    db = SessionLocal()
    try:
        _, municipality_ids = load_municipalities(data_dir / "municipalities.json", db)
        load_road_segments(data_dir / "road_segments.json", db, municipality_ids)
        load_crashes(data_dir / "crashes.json", db, municipality_ids)
        load_census_tracts(data_dir / "census_tracts.json", db, municipality_ids)
        logger.info(
            "Synthetic seed totals: %s municipalities, %s road segments, %s crashes, %s census tracts",
            db.query(Municipality).count(),
            db.query(RoadSegment).count(),
            db.query(Crash).count(),
            db.query(CensusTract).count(),
        )
    except Exception:
        db.rollback()
        logger.exception("Synthetic sample load failed")
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
