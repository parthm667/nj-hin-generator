#!/usr/bin/env python3
"""Load official NJDOT county/year Accidents archives.

Valid reported coordinates are preferred after a municipality containment
check. Otherwise, an exact calibrated SRI/milepost lookup is performed
against ``road_routes`` LineStringM geometry.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import logging
import math
import os
import re
import sys
import time
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator, Mapping, Sequence

import requests
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models.database import SessionLocal
from app.models.tables import Crash

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

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
SEVERITY_MAP = {"F": "fatal", "I": "injury_unknown", "P": "property_damage"}
NJ_COUNTIES = {
    "Atlantic": "Atlantic", "Bergen": "Bergen", "Burlington": "Burlington",
    "Camden": "Camden", "Cape May": "CapeMay", "Cumberland": "Cumberland",
    "Essex": "Essex", "Gloucester": "Gloucester", "Hudson": "Hudson",
    "Hunterdon": "Hunterdon", "Mercer": "Mercer", "Middlesex": "Middlesex",
    "Monmouth": "Monmouth", "Morris": "Morris", "Ocean": "Ocean",
    "Passaic": "Passaic", "Salem": "Salem", "Somerset": "Somerset",
    "Sussex": "Sussex", "Union": "Union", "Warren": "Warren",
}
ARCHIVE_URL = (
    "https://www.state.nj.us/transportation/refdata/accident/"
    "{year}/{token}{year}Accidents.zip"
)
NJ_BOUNDS = (-75.6, 38.9, -73.9, 41.4)
POINT_TOLERANCE_DEGREES = 1e-7


class ArchiveValidationError(ValueError):
    """The downloaded file is not the expected NJDOT archive shape."""


@dataclass(frozen=True)
class PreparedCrash:
    source_id: str
    external_id: str
    county: str
    municipality_name: str
    crash_date: date
    crash_time: str | None
    severity: str
    ped_involved: bool
    bike_involved: bool | None
    total_killed: int | None
    total_injured: int | None
    pedestrians_killed: int | None
    pedestrians_injured: int | None
    road_name: str | None
    light_condition: str | None
    reported_point: tuple[float, float] | None
    coordinate_issue: str | None
    sri: str | None
    milepost: float | None


@dataclass(frozen=True)
class LocationResult:
    point: tuple[float, float] | None
    method: str | None = None
    reason: str | None = None


@dataclass
class ArchiveReport:
    county: str
    year: int
    source_url: str
    cache_path: str | None = None
    status: str = "pending"
    records: int = 0
    loaded: int = 0
    updated: int = 0
    duplicates: int = 0
    rejected: int = 0
    rejections: Counter = field(default_factory=Counter)
    coordinate_issues: Counter = field(default_factory=Counter)
    location_methods: Counter = field(default_factory=Counter)
    error: str | None = None

    def reject(self, reason: str) -> None:
        self.rejected += 1
        self.rejections[reason] += 1

    def as_dict(self) -> dict:
        return {
            "county": self.county, "year": self.year, "source_url": self.source_url,
            "cache_path": self.cache_path, "status": self.status, "records": self.records,
            "loaded": self.loaded, "updated": self.updated,
            "duplicates": self.duplicates, "rejected": self.rejected,
            "rejections": dict(sorted(self.rejections.items())),
            "coordinate_issues": dict(sorted(self.coordinate_issues.items())),
            "location_methods": dict(sorted(self.location_methods.items())), "error": self.error,
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, payload: Mapping) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for attempt in range(5):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05 * (2**attempt))


def normalize_county_name(value: str) -> str:
    compact = re.sub(r"[^A-Z]", "", (value or "").upper())
    for county in NJ_COUNTIES:
        if re.sub(r"[^A-Z]", "", county.upper()) == compact:
            return county
    raise ValueError(f"unknown county: {value}")


def normalize_municipality_name(value: str) -> str:
    """Normalize spelling without erasing the municipality government type."""
    words = re.sub(r"[^A-Z0-9]+", " ", (value or "").upper()).split()
    suffixes = {
        "TWP": "TOWNSHIP", "TOWNSHIP": "TOWNSHIP", "BORO": "BOROUGH",
        "BOROUGH": "BOROUGH", "CITY": "CITY", "TOWN": "TOWN", "VILLAGE": "VILLAGE",
    }
    if words and words[-1] in suffixes:
        words[-1] = suffixes[words[-1]]
    normalized = " ".join(words)
    # Verified 2022 Accidents labels that differ from current NJGIN labels.
    # These explicit corrections keep the legal type in the canonical value;
    # ordinary names are never matched after stripping CITY/BOROUGH/TOWNSHIP.
    source_aliases = {
        "MOUNT EPHRIAM BOROUGH": "MOUNT EPHRAIM BOROUGH",
        "ORANGE CITY": "CITY OF ORANGE TOWNSHIP",
        "FAIRFIELD BOROUGH": "FAIRFIELD TOWNSHIP",
        "SOUTH ORANGE VILLAGE TOWNSHIP": "SOUTH ORANGE VILLAGE",
        "MILFORD TOWNSHIP": "MILFORD BOROUGH",
        "PARSIPPANY TROY HILLS": "PARSIPPANY TROY HILLS TOWNSHIP",
        "PASSAIC TOWNSHIP": "LONG HILL TOWNSHIP",
        "PT PLEASANT BEACH BOROUGH": "POINT PLEASANT BEACH BOROUGH",
        "LOWER ALLOWAYS CRK TOWNSHIP": "LOWER ALLOWAYS CREEK TOWNSHIP",
        "SANDVSTON TOWNSHIP": "SANDYSTON TOWNSHIP",
    }
    return source_aliases.get(normalized, normalized)


def build_external_id(county: str, year: int, source_id: str) -> str:
    token = normalize_county_name(county).upper().replace(" ", "_")
    raw = f"NJDOT:{year}:{token}:{source_id.strip()}"
    if len(raw) <= 50:
        return raw
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    return f"NJDOT:{year}:{token[:8]}:{digest}"


def _archive_member(archive: zipfile.ZipFile) -> str:
    candidates = [
        item.filename for item in archive.infolist()
        if not item.is_dir() and Path(item.filename).name.lower().endswith("accidents.txt")
    ]
    if len(candidates) != 1 or Path(candidates[0]).name != candidates[0]:
        raise ArchiveValidationError("ZIP must contain exactly one top-level Accidents.txt member")
    return candidates[0]


def validate_archive(
    path: str | Path, expected_member: str | Sequence[str] | None = None
) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            member = _archive_member(archive)
            expected_members = (
                [expected_member]
                if isinstance(expected_member, str)
                else list(expected_member or [])
            )
            if expected_members and member.lower() not in {
                candidate.lower() for candidate in expected_members
            }:
                raise ArchiveValidationError(
                    f"ZIP member {member!r} does not match expected {expected_members!r}"
                )
            bad_member = archive.testzip()
            if bad_member:
                raise ArchiveValidationError(f"ZIP CRC failed for {bad_member}")
            if archive.getinfo(member).file_size <= 0:
                raise ArchiveValidationError("Accidents.txt member is empty")
            return member
    except (OSError, zipfile.BadZipFile) as exc:
        raise ArchiveValidationError(f"invalid ZIP archive: {exc}") from exc


def parse_njdot_accidents(
    file_path: str | Path, *, on_reject: Callable[[str], None] | None = None
) -> Iterator[dict[str, str]]:
    """Stream cleaned records from a validated ZIP or legacy text file."""
    path = Path(file_path)
    reject = on_reject or (lambda _reason: None)
    if path.suffix.lower() == ".zip":
        member = validate_archive(path)
        with zipfile.ZipFile(path) as archive, archive.open(member) as raw:
            stream = io.TextIOWrapper(raw, encoding="latin-1", newline="")
            yield from _iter_records(stream, reject)
    else:
        with path.open(encoding="latin-1", newline="") as stream:
            yield from _iter_records(stream, reject)


def _iter_records(stream: Iterable[str], reject: Callable[[str], None]) -> Iterator[dict[str, str]]:
    pending: list[str] | None = None
    continuation: list[str] = []
    for columns in csv.reader(stream):
        if pending is not None:
            if not columns:
                continue
            if len(columns) == 1 and len(continuation) < 16:
                continuation.append(columns[0].strip())
                if sum(map(len, continuation)) <= 4096:
                    continue
            elif len(columns) == 2 and columns[1].strip().isdigit():
                damage_parts = [pending[48], *continuation, columns[0]]
                pending[48] = " ".join(part.strip() for part in damage_parts if part.strip())
                pending.append(columns[1])
                yield {
                    name: pending[index].strip()
                    for index, name in enumerate(FIELD_NAMES)
                }
                pending = None
                continuation = []
                continue

            reject("schema_columns")
            pending = None
            continuation = []
            if len(columns) not in (len(FIELD_NAMES), len(FIELD_NAMES) - 1):
                continue

        if len(columns) == len(FIELD_NAMES):
            yield {name: columns[index].strip() for index, name in enumerate(FIELD_NAMES)}
        elif len(columns) == len(FIELD_NAMES) - 1:
            pending = columns
        else:
            reject("schema_columns")

    if pending is not None:
        reject("schema_columns")


def _optional_nonnegative_int(value: str, field_name: str) -> int | None:
    text_value = (value or "").strip()
    if not text_value:
        return None
    try:
        result = int(text_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(field_name) from exc
    if result < 0:
        raise ValueError(field_name)
    return result


def _reported_point(record: Mapping[str, str]) -> tuple[tuple[float, float] | None, str | None]:
    latitude_text = (record.get("latitude") or "").strip()
    longitude_text = (record.get("longitude") or "").strip()
    if not latitude_text and not longitude_text:
        return None, "reported_missing"
    if not latitude_text or not longitude_text:
        return None, "reported_incomplete"
    try:
        latitude, longitude = float(latitude_text), float(longitude_text)
    except ValueError:
        return None, "reported_non_numeric"
    if longitude > 0:
        longitude = -longitude
    west, south, east, north = NJ_BOUNDS
    if not all(map(math.isfinite, (longitude, latitude))) or not (
        west <= longitude <= east and south <= latitude <= north
    ):
        return None, "reported_out_of_bounds"
    return (longitude, latitude), None


def prepare_record(
    record: Mapping[str, str], *, expected_county: str, expected_year: int
) -> PreparedCrash:
    county = normalize_county_name(record.get("county_name", ""))
    if county != normalize_county_name(expected_county):
        raise ValueError("county")
    source_id = (record.get("id") or "").strip()
    if not source_id:
        raise ValueError("source_id")
    try:
        crash_date = datetime.strptime((record.get("crash_date") or "").strip(), "%m/%d/%Y").date()
    except ValueError as exc:
        raise ValueError("date") from exc
    if crash_date.year != expected_year:
        raise ValueError("year")
    severity_code = (record.get("severity") or "").strip().upper()
    if severity_code not in SEVERITY_MAP:
        raise ValueError("severity")
    municipality_name = normalize_municipality_name(record.get("municipality_name", ""))
    if not municipality_name:
        raise ValueError("municipality_name")
    point, coordinate_issue = _reported_point(record)
    sri = (record.get("sri_std_rte_identifier") or "").strip().upper() or None
    milepost_text = (record.get("milepost") or "").strip()
    milepost = None
    if milepost_text:
        try:
            candidate = float(milepost_text)
            if math.isfinite(candidate):
                milepost = candidate
        except ValueError:
            pass
    total_killed = _optional_nonnegative_int(
        record.get("total_killed", ""), "total_killed"
    )
    total_injured = _optional_nonnegative_int(
        record.get("total_injured", ""), "total_injured"
    )
    pedestrians_killed = _optional_nonnegative_int(
        record.get("pedestrians_killed", ""), "pedestrians_killed"
    )
    pedestrians_injured = _optional_nonnegative_int(
        record.get("pedestrians_injured", ""), "pedestrians_injured"
    )
    ped_involved = any(
        count is not None and count > 0
        for count in (pedestrians_killed, pedestrians_injured)
    )
    return PreparedCrash(
        source_id=source_id,
        external_id=build_external_id(county, expected_year, source_id),
        county=county,
        municipality_name=municipality_name,
        crash_date=crash_date,
        crash_time=(record.get("crash_time") or "").strip() or None,
        severity=SEVERITY_MAP[severity_code],
        ped_involved=ped_involved,
        bike_involved=None,
        total_killed=total_killed,
        total_injured=total_injured,
        pedestrians_killed=pedestrians_killed,
        pedestrians_injured=pedestrians_injured,
        road_name=((record.get("crash_location") or record.get("route") or "").strip() or None),
        light_condition=(record.get("light_condition") or "").strip() or None,
        reported_point=point, coordinate_issue=coordinate_issue, sri=sri, milepost=milepost,
    )


ROUTE_POINT_SQL = text("""
    SELECT r.source_id, ST_X(ST_Force2D(located.geom)) AS longitude,
           ST_Y(ST_Force2D(located.geom)) AS latitude
    FROM road_routes AS r
    JOIN municipalities AS m ON m.muni_id = :muni_id
    CROSS JOIN LATERAL ST_Dump(ST_LocateAlong(r.geom, :milepost)) AS located
    WHERE r.sri = :sri
      AND :milepost BETWEEN LEAST(r.mp_start, r.mp_end) AND GREATEST(r.mp_start, r.mp_end)
      AND ST_Covers(m.geom, ST_Force2D(located.geom))
    ORDER BY r.source_id
""")


def _collapse_route_points(rows: Sequence[Mapping]) -> LocationResult:
    clusters: list[list[tuple[float, float]]] = []
    for row in rows:
        point = (float(row["longitude"]), float(row["latitude"]))
        for cluster in clusters:
            anchor = cluster[0]
            if max(abs(point[0] - anchor[0]), abs(point[1] - anchor[1])) <= POINT_TOLERANCE_DEGREES:
                cluster.append(point)
                break
        else:
            clusters.append([point])
    if not clusters:
        return LocationResult(None, reason="route_measure_unresolved")
    if len(clusters) > 1:
        return LocationResult(None, reason="route_ambiguous")
    # Rows are source-id ordered. Keep the first actual calibrated point rather
    # than averaging tiny source-rounding differences into an invented point.
    return LocationResult(clusters[0][0], method="route_milepost")


def locate_route_point(database: Session, *, muni_id: int, sri: str, milepost: float) -> LocationResult:
    rows = database.execute(
        ROUTE_POINT_SQL, {"muni_id": muni_id, "sri": sri, "milepost": milepost}
    ).mappings().all()
    return _collapse_route_points(rows)


BATCH_LOCATION_SQL = text("""
    WITH input AS (
        SELECT * FROM jsonb_to_recordset(CAST(:records AS jsonb)) AS item(
            row_key integer, muni_id integer, reported_longitude double precision,
            reported_latitude double precision, sri text, milepost double precision
        )
    ), checked AS (
        SELECT item.*,
            CASE WHEN item.reported_longitude IS NULL OR item.reported_latitude IS NULL THEN false
                 ELSE ST_Covers(municipality.geom, ST_SetSRID(
                     ST_Point(item.reported_longitude, item.reported_latitude), 4326)) END AS reported_valid,
            municipality.geom AS municipality_geom
        FROM input AS item
        JOIN municipalities AS municipality ON municipality.muni_id = item.muni_id
    )
    SELECT checked.row_key, checked.reported_valid, route.source_id,
           ST_X(route.geom) AS route_longitude, ST_Y(route.geom) AS route_latitude
    FROM checked
    LEFT JOIN LATERAL (
        SELECT road.source_id, ST_Force2D(located.geom) AS geom
        FROM road_routes AS road
        CROSS JOIN LATERAL ST_Dump(ST_LocateAlong(road.geom, checked.milepost)) AS located
        WHERE NOT checked.reported_valid AND checked.sri IS NOT NULL
          AND checked.milepost IS NOT NULL AND road.sri = checked.sri
          AND checked.milepost BETWEEN LEAST(road.mp_start, road.mp_end)
                                       AND GREATEST(road.mp_start, road.mp_end)
          AND ST_Covers(checked.municipality_geom, ST_Force2D(located.geom))
        ORDER BY road.source_id
    ) AS route ON true
    ORDER BY checked.row_key, route.source_id
""")


def resolve_location_batch(
    database: Session, records: Sequence[tuple[PreparedCrash, int]]
) -> list[LocationResult]:
    payload = []
    for row_key, (record, muni_id) in enumerate(records):
        longitude, latitude = record.reported_point or (None, None)
        payload.append({"row_key": row_key, "muni_id": muni_id,
                        "reported_longitude": longitude, "reported_latitude": latitude,
                        "sri": record.sri, "milepost": record.milepost})
    rows = database.execute(BATCH_LOCATION_SQL, {"records": json.dumps(payload)}).mappings().all()
    grouped: dict[int, list[Mapping]] = defaultdict(list)
    for row in rows:
        grouped[int(row["row_key"])].append(row)
    locations = []
    for row_key, (record, _muni_id) in enumerate(records):
        item_rows = grouped.get(row_key, [])
        if item_rows and item_rows[0]["reported_valid"]:
            locations.append(LocationResult(record.reported_point, method="reported"))
            continue
        route_rows = [
            {"longitude": row["route_longitude"], "latitude": row["route_latitude"]}
            for row in item_rows
            if row["route_longitude"] is not None and row["route_latitude"] is not None
        ]
        if route_rows:
            locations.append(_collapse_route_points(route_rows))
        elif not record.sri or record.milepost is None:
            locations.append(LocationResult(None, reason="route_reference_missing"))
        else:
            locations.append(LocationResult(None, reason="route_measure_unresolved"))
    return locations


class NJDOTCrashIngester:
    def __init__(self, *, cache_dir: str | Path = PROJECT_DIR / "data" / "raw" / "njdot-crashes",
                 session: requests.Session | None = None, timeout: tuple[float, float] = (5, 60),
                 retries: int = 3, batch_size: int = 1000, offline: bool = False,
                 refresh_cache: bool = False):
        if batch_size <= 0 or retries <= 0:
            raise ValueError("batch_size and retries must be positive")
        self.cache_dir, self.session, self.timeout = Path(cache_dir), session or requests.Session(), timeout
        self.retries, self.batch_size = retries, batch_size
        self.offline, self.refresh_cache = offline, refresh_cache

    @staticmethod
    def source_url(county: str, year: int) -> str:
        canonical = normalize_county_name(county)
        return ARCHIVE_URL.format(year=year, token=NJ_COUNTIES[canonical])

    @staticmethod
    def archive_name(county: str, year: int) -> str:
        canonical = normalize_county_name(county)
        return f"{NJ_COUNTIES[canonical]}{year}Accidents.zip"

    def fetch_archive(self, county: str, year: int) -> Path:
        canonical_county = normalize_county_name(county)
        name = self.archive_name(county, year)
        destination = self.cache_dir / name
        expected_members = [f"{Path(name).stem}.txt"]
        if canonical_county == "Cape May":
            expected_members.append(f"Cape May{year}Accidents.txt")
        if destination.exists() and not self.refresh_cache:
            validate_archive(destination, expected_members)
            return destination
        if self.offline:
            raise FileNotFoundError(f"offline cache miss: {destination}")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.part")
        last_error: Exception | None = None
        for _attempt in range(1, self.retries + 1):
            try:
                response = self.session.get(self.source_url(county, year), timeout=self.timeout, stream=True)
                response.raise_for_status()
                with temporary.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            output.write(chunk)
                validate_archive(temporary, expected_members)
                os.replace(temporary, destination)
                return destination
            except Exception as exc:
                last_error = exc
                temporary.unlink(missing_ok=True)
        raise RuntimeError(
            f"failed to download {self.source_url(county, year)} after {self.retries} attempts: {last_error}"
        ) from last_error

    @staticmethod
    def municipality_lookup(database: Session, county: str) -> dict[str, list[int]]:
        rows = database.execute(text(
            "SELECT muni_id, name FROM municipalities "
            "WHERE regexp_replace(upper(county), '[^A-Z]', '', 'g') = :county"
        ), {"county": re.sub(r"[^A-Z]", "", county.upper())}).mappings()
        lookup: dict[str, list[int]] = defaultdict(list)
        for row in rows:
            lookup[normalize_municipality_name(row["name"])].append(int(row["muni_id"]))
        return dict(lookup)

    def _load_batch(self, database: Session, records: Sequence[tuple[PreparedCrash, int]],
                    report: ArchiveReport) -> None:
        locations = resolve_location_batch(database, records)
        rows = []
        for (record, muni_id), location in zip(records, locations):
            if record.coordinate_issue:
                report.coordinate_issues[record.coordinate_issue] += 1
            elif location.method != "reported":
                report.coordinate_issues["reported_outside_municipality"] += 1
            if location.reason:
                report.reject(location.reason)
                continue
            assert location.point is not None and location.method is not None
            longitude, latitude = location.point
            report.location_methods[location.method] += 1
            rows.append({
                "external_id": record.external_id, "crash_date": record.crash_date,
                "crash_time": record.crash_time, "severity": record.severity,
                "ped_involved": record.ped_involved,
                "bike_involved": record.bike_involved,
                "total_killed": record.total_killed,
                "total_injured": record.total_injured,
                "pedestrians_killed": record.pedestrians_killed,
                "pedestrians_injured": record.pedestrians_injured,
                "muni_id": muni_id, "road_name": record.road_name,
                "route_number": record.sri, "light_condition": record.light_condition,
                "geom": f"SRID=4326;POINT({longitude} {latitude})",
                "geocode_quality": location.method,
            })
        if rows:
            unique_rows = {row["external_id"]: row for row in rows}
            external_ids = list(unique_rows)
            existing_ids = set(
                database.execute(
                    text(
                        "SELECT external_id FROM crashes "
                        "WHERE external_id = ANY(:external_ids)"
                    ),
                    {"external_ids": external_ids},
                ).scalars()
            )
            insert_statement = postgresql_insert(Crash.__table__).values(
                list(unique_rows.values())
            )
            statement = insert_statement.on_conflict_do_update(
                index_elements=[Crash.external_id],
                set_={
                    "severity": insert_statement.excluded.severity,
                    "ped_involved": insert_statement.excluded.ped_involved,
                    "bike_involved": insert_statement.excluded.bike_involved,
                    "total_killed": insert_statement.excluded.total_killed,
                    "total_injured": insert_statement.excluded.total_injured,
                    "pedestrians_killed": insert_statement.excluded.pedestrians_killed,
                    "pedestrians_injured": insert_statement.excluded.pedestrians_injured,
                },
            )
            database.execute(statement)
            report.loaded += len(unique_rows) - len(existing_ids)
            report.duplicates += len(rows) - len(unique_rows) + len(existing_ids)
        database.commit()

    def ingest_archive(self, database: Session, archive_path: str | Path, *, county: str, year: int,
                       report: ArchiveReport, municipality: str | None = None,
                       muni_id: int | None = None) -> None:
        lookup = self.municipality_lookup(database, county)
        requested_name = normalize_municipality_name(municipality) if municipality else None
        batch: list[tuple[PreparedCrash, int]] = []

        def schema_rejection(reason: str) -> None:
            report.records += 1
            report.reject(reason)

        for raw in parse_njdot_accidents(archive_path, on_reject=schema_rejection):
            report.records += 1
            try:
                record = prepare_record(raw, expected_county=county, expected_year=year)
            except ValueError as exc:
                report.reject(str(exc))
                continue
            if requested_name and record.municipality_name != requested_name:
                report.reject("municipality_filter")
                continue
            candidates = lookup.get(record.municipality_name, [])
            if not candidates:
                report.reject("municipality_unmatched")
                continue
            if len(candidates) > 1:
                report.reject("municipality_ambiguous")
                continue
            resolved_muni_id = candidates[0]
            if muni_id is not None and resolved_muni_id != muni_id:
                report.reject("muni_id_mismatch")
                continue
            batch.append((record, resolved_muni_id))
            if len(batch) >= self.batch_size:
                self._load_batch(database, batch, report)
                batch.clear()
        if batch:
            self._load_batch(database, batch, report)

    def _enrich_batch(
        self,
        database: Session,
        records: Sequence[PreparedCrash],
        report: ArchiveReport,
    ) -> None:
        unique_records = {record.external_id: record for record in records}
        payload = [
            {
                "external_id": record.external_id,
                "severity": record.severity,
                "ped_involved": record.ped_involved,
                "bike_involved": record.bike_involved,
                "total_killed": record.total_killed,
                "total_injured": record.total_injured,
                "pedestrians_killed": record.pedestrians_killed,
                "pedestrians_injured": record.pedestrians_injured,
            }
            for record in unique_records.values()
        ]
        rows = database.execute(
            text(
                """
                WITH input AS (
                    SELECT *
                    FROM jsonb_to_recordset(CAST(:records AS jsonb)) AS item(
                        external_id text,
                        severity text,
                        ped_involved boolean,
                        bike_involved boolean,
                        total_killed integer,
                        total_injured integer,
                        pedestrians_killed integer,
                        pedestrians_injured integer
                    )
                )
                UPDATE crashes AS crash
                SET
                    severity = input.severity,
                    ped_involved = crash.ped_involved OR input.ped_involved,
                    bike_involved = input.bike_involved,
                    total_killed = COALESCE(input.total_killed, crash.total_killed),
                    total_injured = COALESCE(input.total_injured, crash.total_injured),
                    pedestrians_killed = COALESCE(
                        input.pedestrians_killed,
                        crash.pedestrians_killed
                    ),
                    pedestrians_injured = COALESCE(
                        input.pedestrians_injured,
                        crash.pedestrians_injured
                    )
                FROM input
                WHERE crash.external_id = input.external_id
                RETURNING crash.external_id
                """
            ),
            {"records": json.dumps(payload)},
        ).scalars().all()
        matched = set(rows)
        report.updated += len(matched)
        report.duplicates += len(records) - len(unique_records)
        for external_id in unique_records.keys() - matched:
            report.reject("existing_crash_missing")
        database.commit()

    def enrich_archive(
        self,
        database: Session,
        archive_path: str | Path,
        *,
        county: str,
        year: int,
        report: ArchiveReport,
        municipality: str | None = None,
    ) -> None:
        """Refresh source semantics without resolving or rewriting locations."""
        requested_name = (
            normalize_municipality_name(municipality) if municipality else None
        )
        batch: list[PreparedCrash] = []

        def schema_rejection(reason: str) -> None:
            report.records += 1
            report.reject(reason)

        for raw in parse_njdot_accidents(archive_path, on_reject=schema_rejection):
            report.records += 1
            try:
                record = prepare_record(raw, expected_county=county, expected_year=year)
            except ValueError as exc:
                report.reject(str(exc))
                continue
            if requested_name and record.municipality_name != requested_name:
                report.reject("municipality_filter")
                continue
            batch.append(record)
            if len(batch) >= self.batch_size:
                self._enrich_batch(database, batch, report)
                batch.clear()
        if batch:
            self._enrich_batch(database, batch, report)


def _manifest(archives: Sequence[ArchiveReport], *, started_at: str, parameters: Mapping) -> dict:
    status_counts = Counter(item.status for item in archives)
    totals = {"archives": len(archives), "records": sum(x.records for x in archives),
              "loaded": sum(x.loaded for x in archives),
              "updated": sum(x.updated for x in archives),
              "duplicates": sum(x.duplicates for x in archives),
              "rejected": sum(x.rejected for x in archives),
              "status_counts": dict(sorted(status_counts.items()))}
    for name in ("rejections", "coordinate_issues", "location_methods"):
        counter = Counter()
        for item in archives:
            counter.update(getattr(item, name))
        totals[name] = dict(sorted(counter.items()))
    if status_counts["failed"]:
        status = "failed"
    elif archives and status_counts["succeeded"] == len(archives):
        status = "succeeded"
    else:
        status = "running"
    return {"pipeline": "njdot_crashes", "status": status,
            "started_at": started_at, "updated_at": _utc_now(), "parameters": dict(parameters),
            "totals": totals, "archives": [item.as_dict() for item in archives]}


def run_ingestion(args: argparse.Namespace, database_factory=SessionLocal) -> int:
    if args.start_year > args.end_year:
        raise ValueError("start-year must be less than or equal to end-year")
    counties = list(dict.fromkeys(
        normalize_county_name(item) for item in (args.county or list(NJ_COUNTIES))
    ))
    ingester = NJDOTCrashIngester(cache_dir=args.cache_dir, batch_size=args.batch_size,
                                  offline=args.offline, refresh_cache=args.refresh_cache)
    report_path, started_at = Path(args.report), _utc_now()
    parameters = {"start_year": args.start_year, "end_year": args.end_year,
                  "counties": counties, "cache_dir": os.fspath(Path(args.cache_dir)),
                  "offline": args.offline, "refresh_cache": args.refresh_cache,
                  "batch_size": args.batch_size,
                  "enrich_only": getattr(args, "enrich_only", False)}
    archive_specs: list[tuple[str, int, Path | None]] = []
    if args.archives:
        pattern = re.compile(r"^(?P<county>[A-Za-z]+)(?P<year>\d{4})Accidents$")
        for archive in args.archives:
            match = pattern.match(Path(archive).stem)
            if not match:
                raise ValueError(f"cannot infer county/year from archive filename: {archive}")
            archive_specs.append((normalize_county_name(match.group("county")),
                                  int(match.group("year")), Path(archive)))
    else:
        archive_specs.extend((county, year, None)
                             for year in range(args.start_year, args.end_year + 1)
                             for county in counties)

    reports = [
        ArchiveReport(county=county, year=year, source_url=ingester.source_url(county, year))
        for county, year, _supplied_path in archive_specs
    ]
    _atomic_json(report_path, _manifest(reports, started_at=started_at, parameters=parameters))

    for (county, year, supplied_path), item in zip(archive_specs, reports):
        item.status = "running"
        _atomic_json(report_path, _manifest(reports, started_at=started_at, parameters=parameters))
        database = None
        try:
            archive_path = supplied_path or ingester.fetch_archive(county, year)
            validate_archive(archive_path)
            item.cache_path = os.fspath(Path(archive_path))
            database = database_factory()
            if getattr(args, "enrich_only", False):
                ingester.enrich_archive(
                    database,
                    archive_path,
                    county=county,
                    year=year,
                    report=item,
                    municipality=args.municipality,
                )
            else:
                ingester.ingest_archive(database, archive_path, county=county, year=year, report=item,
                                        municipality=args.municipality, muni_id=args.muni_id)
            item.status = "succeeded"
        except Exception as exc:
            if database is not None:
                database.rollback()
            item.status, item.error = "failed", str(exc)
            logger.error("%s %s failed: %s", county, year, exc)
        finally:
            if database is not None:
                database.close()
            _atomic_json(report_path, _manifest(reports, started_at=started_at, parameters=parameters))
    return 0 if reports and all(item.status == "succeeded" for item in reports) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Load official NJDOT Accidents archives")
    parser.add_argument("archives", nargs="*",
                        help="Legacy local CountyYYYYAccidents.zip paths; otherwise download")
    parser.add_argument("--start-year", type=int, default=2022)
    parser.add_argument("--end-year", type=int, default=2022)
    parser.add_argument("--county", action="append",
                        help="County to load; repeat for multiple counties, omit for all 21")
    parser.add_argument("--cache-dir", type=Path,
                        default=PROJECT_DIR / "data" / "raw" / "njdot-crashes")
    parser.add_argument("--report", type=Path,
                        default=PROJECT_DIR / "data" / "processed" / "njdot-crashes-report.json")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument(
        "--enrich-only",
        action="store_true",
        help="Update semantics for existing external IDs without resolving locations",
    )
    parser.add_argument("--municipality", help="Legacy exact municipality-name filter")
    parser.add_argument("--muni-id", type=int,
                        help="Legacy ID guard; records must still match name/county/geometry")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser, args = build_parser(), None
    args = parser.parse_args(argv)
    if args.offline and args.refresh_cache:
        parser.error("--offline and --refresh-cache cannot be used together")
    if args.enrich_only and args.muni_id is not None:
        parser.error("--muni-id cannot be used with --enrich-only")
    try:
        return run_ingestion(args)
    except ValueError as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
