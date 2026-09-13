#!/usr/bin/env python3
"""Enrich existing NJDOT crashes from historical Pedestrians bicycle flags.

The 2017–present Pedestrian layout identifies column34 as Is Bicyclist?:
https://www.nj.gov/transportation/refdata/accident/pdf/2017PedestrianTable.pdf
Only explicit Y records establish involvement. Blank records establish nothing.
No person attributes are retained or included in reports.
"""
from __future__ import annotations

import argparse
import csv
import io
import logging
import os
import sys
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import requests
from sqlalchemy import func, select, text, update

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models.database import SessionLocal
from app.models.tables import Crash, Municipality
from scripts.ingest_njdot_crashes import (
    ArchiveValidationError, NJ_COUNTIES, PROJECT_DIR, _atomic_json, _utc_now,
    build_external_id, normalize_county_name,
)

logger = logging.getLogger(__name__)
COUNTY_CODES = {county: f"{number:02d}" for number, county in enumerate(NJ_COUNTIES, 1)}
COUNTERS = ("source_rows", "source_person_rows", "source_crashes", "matched", "updated",
            "already_known", "unmatched", "conflicting_false")


@dataclass
class BicycleEvidence:
    report: dict
    external_ids: set[str]


def read_archive(path: Path, *, county: str, year: int) -> BicycleEvidence:
    county = normalize_county_name(county)
    if not 2017 <= year <= 2022:
        raise ValueError("supported historical years are 2017–2022")
    expected = {f"{NJ_COUNTIES[county]}{year}Pedestrians.txt"}
    if county == "Cape May":
        expected.add(f"Cape May{year}Pedestrians.txt")
    report = dict.fromkeys(COUNTERS, 0)
    report.update(county=county, year=year, cache_path=os.fspath(path),
                  source_url=BicycleIngester.source_url(county, year), status="validated")
    external_ids = set()
    try:
        with zipfile.ZipFile(path) as archive:
            members = [item for item in archive.infolist() if not item.is_dir()]
            selected = [item for item in members if item.filename in expected]
            # Somerset2019 includes additional companion ZIPs alongside its TXT.
            # Never extract or parse those ZIPs; reject any unexpected text member.
            extra = [item for item in members if item.filename not in expected]
            if len(selected) != 1 or any(
                Path(item.filename).name != item.filename or not item.filename.lower().endswith(".zip")
                for item in extra
            ):
                raise ArchiveValidationError("ZIP member must be the expected county/year Pedestrians.txt")
            if archive.testzip() is not None:
                raise ArchiveValidationError("ZIP CRC validation failed")
            with archive.open(selected[0]) as source:
                rows = csv.reader(io.TextIOWrapper(source, encoding="latin-1", newline=""), strict=True)
                for number, row in enumerate(rows, 1):
                    if len(row) != 35:
                        raise ArchiveValidationError(f"row {number}: expected 35 columns")
                    source_id, flag = row[0].strip(), row[33].strip().upper()
                    if source_id[:4] != str(year):
                        raise ArchiveValidationError(f"row {number}: source year mismatch")
                    if source_id[4:6] != COUNTY_CODES[county]:
                        raise ArchiveValidationError(f"row {number}: source county mismatch")
                    if len(source_id[6:8]) != 2 or not source_id[6:8].isascii() or not source_id[6:8].isdigit():
                        raise ArchiveValidationError(f"row {number}: invalid municipality code")
                    if not source_id[8:].strip():
                        raise ArchiveValidationError(f"row {number}: missing department case number")
                    if flag not in ("", "Y", "N"):
                        raise ArchiveValidationError(f"row {number}: unrecognized bicycle flag")
                    report["source_rows"] += 1
                    if flag == "Y":
                        report["source_person_rows"] += 1
                        external_ids.add(build_external_id(county, year, source_id))
    except (zipfile.BadZipFile, OSError, csv.Error, RuntimeError) as error:
        raise ArchiveValidationError(f"invalid Pedestrians ZIP: {error}") from error
    report["source_crashes"] = len(external_ids)
    return BicycleEvidence(report, external_ids)


class BicycleIngester:
    def __init__(self, *, cache_dir, session=None, retries=3, timeout=(10, 60),
                 offline=False, refresh_cache=False):
        if retries < 1:
            raise ValueError("retries must be positive")
        if offline and refresh_cache:
            raise ValueError("offline and refresh-cache cannot be used together")
        self.cache_dir, self.session = Path(cache_dir), session or requests.Session()
        self.retries, self.timeout = retries, timeout
        self.offline, self.refresh_cache = offline, refresh_cache

    @staticmethod
    def source_url(county, year):
        token = NJ_COUNTIES[normalize_county_name(county)]
        return f"https://www.state.nj.us/transportation/refdata/accident/{year}/{token}{year}Pedestrians.zip"

    def fetch_archive(self, county, year):
        county = normalize_county_name(county)
        destination = self.cache_dir / f"{NJ_COUNTIES[county]}{year}Pedestrians.zip"
        if destination.exists() and not self.refresh_cache:
            read_archive(destination, county=county, year=year)
            return destination
        if self.offline:
            raise FileNotFoundError(f"offline cache miss: {destination}")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.part")
        last_error = None
        for _ in range(self.retries):
            response = None
            try:
                response = self.session.get(self.source_url(county, year), timeout=self.timeout, stream=True)
                response.raise_for_status()
                with temporary.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            output.write(chunk)
                read_archive(temporary, county=county, year=year)
                os.replace(temporary, destination)
                return destination
            except Exception as error:
                last_error = error
                temporary.unlink(missing_ok=True)
            finally:
                if response is not None and hasattr(response, "close"):
                    response.close()
        raise RuntimeError(f"{county} {year}: download failed after {self.retries} attempts: {last_error}") from last_error


def apply_evidence(database, evidence):
    """Caller owns the transaction. Never insert records or commit a partial batch."""
    for item in evidence:
        report = item.report
        for counter in ("matched", "updated", "already_known", "unmatched", "conflicting_false"):
            report[counter] = 0
        ids = sorted(item.external_ids)
        if not ids:
            continue
        year, county = report["year"], report["county"]
        matches = dict(database.execute(
            select(Crash.external_id, Crash.bike_involved)
            .join(Municipality, Crash.muni_id == Municipality.muni_id)
            .where(Crash.external_id.in_(ids), Crash.crash_date >= date(year, 1, 1),
                   Crash.crash_date < date(year + 1, 1, 1),
                   func.upper(Municipality.county) == county.upper())
            .with_for_update(of=Crash)
        ).all())
        changed = [external_id for external_id, known in matches.items() if known is not True]
        # Revision triggers run FOR EACH STATEMENT, even for an empty UPDATE.
        if changed:
            updated = set(database.execute(
                update(Crash.__table__).where(Crash.external_id.in_(changed))
                .values(bike_involved=True).returning(Crash.external_id)
            ).scalars())
            if updated != set(changed):
                raise RuntimeError("bicycle update verification failed")
        if matches:
            confirmed = set(database.execute(
                select(Crash.external_id).where(Crash.external_id.in_(matches), Crash.bike_involved.is_(True))
            ).scalars())
            if confirmed != set(matches):
                raise RuntimeError("bicycle confirmation verification failed")
        report.update(matched=len(matches), updated=len(changed),
                      already_known=len(matches)-len(changed), unmatched=len(ids)-len(matches),
                      conflicting_false=sum(value is False for value in matches.values()))


def _snapshot(database, ids):
    count = database.scalar(text("SELECT count(*) FROM crashes"))
    fingerprint = database.scalar(text("""
        SELECT md5(COALESCE(string_agg(md5((to_jsonb(crash) - 'bike_involved')::text),
                                      '' ORDER BY crash.external_id), ''))
        FROM crashes AS crash WHERE external_id = ANY(:ids)
    """), {"ids": ids})
    return count, fingerprint


def _manifest(evidence, *, status, started_at, parameters, error=None, verification=None):
    reports = [item.report for item in evidence]
    totals = {key: sum(row[key] for row in reports) for key in COUNTERS}
    totals["archives"] = len(reports)
    years = []
    for year in range(parameters["start_year"], parameters["end_year"] + 1):
        rows = [row for row in reports if row["year"] == year]
        years.append(dict(year=year, available_counties=len(rows),
                          expected_counties=len(parameters["counties"]),
                          **{key: sum(row[key] for row in rows) for key in COUNTERS}))
    return dict(pipeline="njdot_bicycles", status=status, started_at=started_at,
                updated_at=_utc_now(), parameters=parameters, totals=totals, years=years,
                archives=reports, error=error, verification=verification)


def run_ingestion(args, database_factory=SessionLocal):
    if not 2017 <= args.start_year <= args.end_year <= 2022:
        raise ValueError("choose a year range within 2017–2022")
    counties = list(dict.fromkeys(normalize_county_name(county) for county in (args.county or NJ_COUNTIES)))
    ingester = BicycleIngester(cache_dir=args.cache_dir, retries=args.retries,
                               offline=args.offline, refresh_cache=args.refresh_cache)
    parameters = dict(start_year=args.start_year, end_year=args.end_year, counties=counties,
                      dry_run=args.dry_run, cache_dir=os.fspath(args.cache_dir))
    evidence, started_at, verification = [], _utc_now(), None
    report_path, status, error = Path(args.report), "validating", None
    def write_report():
        _atomic_json(report_path, _manifest(evidence, status=status, started_at=started_at,
                                           parameters=parameters, error=error, verification=verification))
    write_report()
    try:
        for year in range(args.start_year, args.end_year + 1):
            for county in counties:
                path = ingester.fetch_archive(county, year)
                evidence.append(read_archive(path, county=county, year=year))
            write_report()
            logger.info("%s: validated %s counties, %s confirmed bicycle crash IDs", year, len(counties),
                        sum(item.report["source_crashes"] for item in evidence if item.report["year"] == year))
        if args.dry_run:
            status = "validated"
        else:
            status = "applying"
            write_report()
            ids = sorted(set().union(*(item.external_ids for item in evidence)))
            with database_factory() as database:
                with database.begin():
                    # Same exclusion used by each bounded worker subprocess. A busy
                    # analysis is allowed to finish; this import waits up to60s.
                    database.execute(text("SET LOCAL lock_timeout = '60s'"))
                    database.execute(text("SELECT pg_advisory_xact_lock(1212763714,1)"))
                    database.execute(text("SELECT pg_advisory_xact_lock(1212763717,1)"))
                    before_count, before_fingerprint = _snapshot(database, ids)
                    apply_evidence(database, evidence)
                    after_count, after_fingerprint = _snapshot(database, ids)
                    if (before_count, before_fingerprint) != (after_count, after_fingerprint):
                        raise RuntimeError("crash count or unrelated crash fields changed during import")
                    verification = dict(crash_count_before=before_count, crash_count_after=after_count,
                                        non_bicycle_fields_unchanged=True)
                status = "succeeded"
            for item in evidence:
                item.report["status"] = "succeeded"
    except Exception as exc:
        status, error = "failed", str(exc)
        # The transaction rolls back all updates; never report attempted updates as applied.
        for item in evidence:
            item.report["updated"] = 0
        verification = None
        logger.error("Bicycle import failed: %s", error)
    write_report()
    manifest = _manifest(evidence, status=status, started_at=started_at, parameters=parameters,
                         error=error, verification=verification)
    for year in manifest["years"]:
        logger.info("%s: source=%s matched=%s updated=%s already_known=%s unmatched=%s", year["year"],
                    year["source_crashes"], year["matched"], year["updated"], year["already_known"], year["unmatched"])
    if verification:
        logger.info("Verified crash count %s -> %s; all non-bicycle fields preserved for source IDs",
                    verification["crash_count_before"], verification["crash_count_after"])
    return 0 if status in ("succeeded", "validated") else 1


def build_parser():
    parser = argparse.ArgumentParser(description="Import confirmed historical NJDOT bicycle involvement")
    parser.add_argument("--start-year", type=int, default=2017)
    parser.add_argument("--end-year", type=int, default=2022)
    parser.add_argument("--county", action="append", help="Repeat to select counties; default all21")
    parser.add_argument("--cache-dir", type=Path, default=PROJECT_DIR / "data/raw/njdot-bicycles")
    parser.add_argument("--report", type=Path, default=PROJECT_DIR / "data/processed/njdot-bicycles-report.json")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Validate/count every selected archive without opening the database")
    return parser


def main(argv=None):
    parser = build_parser()
    try:
        return run_ingestion(parser.parse_args(argv))
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
