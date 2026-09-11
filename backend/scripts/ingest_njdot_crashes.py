#!/usr/bin/env python3
"""
Ingest REAL NJDOT crash records (NJTR-1 "Accidents" table).

NJDOT publishes individual crash records as per-county, per-year ZIP files:
    https://www.state.nj.us/transportation/refdata/accident/{YEAR}/{County}{YEAR}Accidents.zip

These are comma-delimited, header-less files. Column names come from the
NJTR-1 record layout (2017+ schema), reproduced in FIELD_NAMES below.

This loader:
  - reads a downloaded .zip or .txt Accidents file
  - trims the fixed-width padding NJDOT leaves in every field
  - negates longitude (NJDOT stores it as a positive number)
  - keeps only geotagged rows (lat & long present)
  - maps NJDOT severity (P/I/F) to our schema
  - optionally filters to a municipality (e.g. "West Windsor")
  - loads rows into the crashes table for a given muni_id

Usage:
    python ingest_njdot_crashes.py data/real/Mercer2019Accidents.zip \
        --municipality "WEST WINDSOR" --muni-id 2
"""

import sys
import os
import io
import csv
import zipfile
import argparse
import logging
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.app.models.database import SessionLocal
from backend.app.models.tables import Crash, Municipality

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# NJTR-1 Accidents record layout, 2017-2021 schema (column order matters)
FIELD_NAMES = [
    "id", "county_name", "municipality_name", "crash_date", "crash_day_of_week",
    "crash_time", "police_dept_code", "police_department", "police_station",
    "total_killed", "total_injured", "pedestrians_killed", "pedestrians_injured",
    "severity", "intersection", "alcohol_involved", "hazmat_involved",
    "crash_type_code", "total_vehicles_involved", "crash_location",
    "location_direction", "route", "route_suffix", "sri_std_rte_identifier",
    "milepost", "road_system", "road_character", "road_horizontal_alignment",
    "road_grade", "road_surface_type", "surface_condition", "light_condition",
    "environmental_condition", "road_divided_by", "temporary_traffic_control_zone",
    "distance_to_cross_street", "unit_of_measurement", "directn_from_cross_street",
    "cross_street_name", "is_ramp", "ramp_tofrom_route_name",
    "ramp_tofrom_route_direction", "posted_speed", "posted_speed_cross_street",
    "first_harmful_event", "latitude", "longitude", "cell_phone_in_use_flag",
    "other_property_damage", "reporting_badge_no",
]

# NJDOT single-char crash severity -> our schema.
# The Accidents table only distinguishes Property-damage / Injury / Fatal;
# it does not split injury into serious vs minor (that detail lives in other
# NJTR-1 tables), so all injury crashes map to 'minor_injury'.
SEVERITY_MAP = {"F": "fatal", "I": "minor_injury", "P": "property_damage"}


def parse_njdot_accidents(file_path: str):
    """Yield cleaned crash dicts from an NJDOT Accidents zip/txt file."""
    path = Path(file_path)
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as z:
            inner = z.namelist()[0]
            raw = z.read(inner).decode("latin-1")
    else:
        raw = path.read_text(encoding="latin-1")

    for cols in csv.reader(io.StringIO(raw)):
        if len(cols) < len(FIELD_NAMES):
            continue
        rec = {name: cols[i].strip() for i, name in enumerate(FIELD_NAMES)}
        yield rec


def to_crash_dict(rec: dict):
    """Convert a raw NJDOT record to our crash fields, or None if unusable."""
    lat_s, lon_s = rec.get("latitude", ""), rec.get("longitude", "")
    if not lat_s or not lon_s:
        return None
    try:
        lat = float(lat_s)
        lon = float(lon_s)
    except ValueError:
        return None

    # NJDOT stores NJ longitude as a positive value; the real value is west.
    if lon > 0:
        lon = -lon

    # Sanity-check the point falls in NJ's bounding box.
    if not (38.9 <= lat <= 41.4 and -75.6 <= lon <= -73.9):
        return None

    try:
        crash_date = datetime.strptime(rec["crash_date"], "%m/%d/%Y").date()
    except ValueError:
        return None

    severity = SEVERITY_MAP.get(rec.get("severity", "").upper(), "property_damage")

    def as_int(v):
        try:
            return int(v)
        except (ValueError, TypeError):
            return 0

    ped = as_int(rec.get("pedestrians_killed")) + as_int(rec.get("pedestrians_injured"))

    return {
        "external_id": rec["id"].strip(),
        "crash_date": crash_date,
        "crash_time": rec.get("crash_time") or None,
        "severity": severity,
        "ped_involved": ped > 0,
        "bike_involved": False,  # not a distinct field in the Accidents table
        "road_name": (rec.get("crash_location") or rec.get("route") or "").strip() or None,
        "light_condition": rec.get("light_condition") or None,
        "geom": f"SRID=4326;POINT({lon} {lat})",
        "latitude": lat,
        "longitude": lon,
    }


def ingest(file_path, muni_id, municipality=None, start_year=None, end_year=None):
    db = SessionLocal()
    loaded = skipped_geo = skipped_muni = skipped_year = dup = 0
    # Idempotent re-runs: anything already in the table is skipped instead of
    # tripping the UNIQUE(external_id) constraint and aborting the whole file.
    seen = {ext for (ext,) in db.query(Crash.external_id).all()}
    try:
        for rec in parse_njdot_accidents(file_path):
            if municipality and municipality.upper() not in rec.get("municipality_name", "").upper():
                skipped_muni += 1
                continue
            c = to_crash_dict(rec)
            if c is None:
                skipped_geo += 1
                continue
            if start_year and c["crash_date"].year < start_year:
                skipped_year += 1
                continue
            if end_year and c["crash_date"].year > end_year:
                skipped_year += 1
                continue

            ext = c.pop("external_id")
            if ext in seen:
                dup += 1
                continue
            seen.add(ext)
            c.pop("latitude"); c.pop("longitude")

            db.add(Crash(external_id=ext, muni_id=muni_id, **c))
            loaded += 1
            if loaded % 500 == 0:
                db.commit()
                logger.info(f"  loaded {loaded}...")
        db.commit()
    finally:
        db.close()

    logger.info(
        f"Done: loaded={loaded} skipped(no geo)={skipped_geo} "
        f"skipped(other muni)={skipped_muni} skipped(year)={skipped_year} "
        f"skipped(already loaded)={dup}"
    )
    return loaded


def main():
    ap = argparse.ArgumentParser(description="Ingest real NJDOT crash records")
    ap.add_argument("file", help="Path to a NJDOT Accidents .zip or .txt file")
    ap.add_argument("--muni-id", type=int, required=True,
                    help="municipalities.muni_id to attach these crashes to")
    ap.add_argument("--municipality", default=None,
                    help='Filter by municipality name substring, e.g. "West Windsor"')
    ap.add_argument("--start-year", type=int, default=None)
    ap.add_argument("--end-year", type=int, default=None)
    args = ap.parse_args()

    logger.info(f"Reading {args.file}")
    ingest(args.file, args.muni_id, args.municipality, args.start_year, args.end_year)


if __name__ == "__main__":
    main()
