#!/usr/bin/env python3
"""Acquire a validated public NJDOT Crash Data CSV; no database access.

The portal publishes its own entity token and dashboard configuration. Only the
public metric search/download routes used by that dashboard are accessed.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import re
import time
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests

logger = logging.getLogger(__name__)
OFFICIAL_HOST = "https://njdot.aashtowaresafety.net"
PORTAL_URL = OFFICIAL_HOST + "/njdot-crash-data-dashboard"
DOWNLOAD_HOST = "numetric-cache-prod.s3-fips.us-west-2.amazonaws.com"
REQUIRED_COLUMNS = {"id_cr", "Year", "dateofcrash", "casenumber", "County", "Municipality",
                    "Highest Injury Severity Rating", "Geopoint (Calculated)"}
EXTRA_COLUMNS = ("Light Condition", "Bicyclist Involved", "Pedestrian Involved",
                 "Bicyclist Count_sum_sum", "Killed Bicyclist Count_sum_sum",
                 "Injured Bicyclist (Not Killed) Count_sum_sum", "SHSP Emphasis Areas",
                 "Unable to Geocode Crash")


class AcquisitionError(ValueError):
    """An export cannot be safely treated as a complete source snapshot."""


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.part")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True)+"\n", encoding="utf8")
    os.replace(temporary, path)


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024*1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_csv(path, *, start_year, end_year, expected_rows):
    """Validate all rows, not a preview; retain only IDs/counters in memory."""
    years, identifiers = Counter(), set()
    try:
        with Path(path).open(encoding="utf-8-sig", newline="") as source:
            reader = csv.reader(source, strict=True)
            headers = next(reader)
            if len(headers) != len(set(headers)) or not REQUIRED_COLUMNS.issubset(headers):
                raise AcquisitionError("CSV is missing required columns or has duplicate headers")
            id_index, year_index, date_index = (headers.index(name) for name in ("id_cr", "Year", "dateofcrash"))
            for number, row in enumerate(reader, 2):
                if len(row) != len(headers):
                    raise AcquisitionError(f"CSV row {number} has an invalid column count")
                year = row[year_index].strip()
                if not year.isascii() or not year.isdigit() or not start_year <= int(year) <= end_year:
                    raise AcquisitionError(f"CSV row {number} is outside the requested year range")
                try:
                    crash_date = date.fromisoformat(row[date_index])
                except ValueError:
                    raise AcquisitionError(f"CSV row {number} has an invalid crash date") from None
                if crash_date.year != int(year):
                    raise AcquisitionError(f"CSV row {number} date disagrees with its year")
                source_id = row[id_index].strip()
                if not source_id or not source_id.isascii() or not source_id.isdigit():
                    raise AcquisitionError(f"CSV row {number} has an invalid crash ID")
                if source_id in identifiers:
                    raise AcquisitionError(f"CSV row {number} has a duplicate crash ID")
                identifiers.add(source_id)
                years[year] += 1
    except (csv.Error, UnicodeError, StopIteration):
        raise AcquisitionError("CSV could not be fully parsed") from None
    rows = sum(years.values())
    if rows != expected_rows:
        raise AcquisitionError(f"CSV row count {rows} does not match public dashboard count {expected_rows}")
    return dict(rows=rows, years=dict(sorted(years.items())), unique_ids=len(identifiers),
                headers=headers, bytes=Path(path).stat().st_size, sha256=file_hash(path))


def filter_public_export(source_path, destination, *, start_year, end_year):
    excluded, original_rows = 0, 0
    with Path(source_path).open(encoding="utf-8-sig", newline="") as source, destination.open("w", encoding="utf8", newline="") as output:
        reader, writer = csv.reader(source, strict=True), csv.writer(output)
        try:
            headers = next(reader)
            year_index = headers.index("Year")
        except (StopIteration, ValueError):
            raise AcquisitionError("Input public CSV has no Year header") from None
        writer.writerow(headers)
        for number, row in enumerate(reader, 2):
            if len(row) != len(headers):
                raise AcquisitionError(f"Input public CSV row {number} has an invalid column count")
            year = row[year_index].strip()
            if not year.isascii() or not year.isdigit():
                raise AcquisitionError(f"Input public CSV row {number} has an invalid year")
            original_rows += 1
            if start_year <= int(year) <= end_year:
                writer.writerow(row)
            else:
                excluded += 1
    return dict(kind="filtered_public_export", input_path=os.fspath(Path(source_path).resolve()),
                sha256=file_hash(source_path), bytes=Path(source_path).stat().st_size,
                rows=original_rows, excluded_rows=excluded)


class DashboardDownloader:
    def __init__(self, *, cache_dir, session=None, retries=3, poll_interval=10,
                 max_wait=1800, sleep=time.sleep, clock=time.monotonic):
        if retries < 1 or poll_interval <= 0 or max_wait <= 0:
            raise AcquisitionError("Retry and wait bounds must be positive")
        self.cache_dir = Path(cache_dir)
        self.session = session or requests.Session()
        self.retries, self.poll_interval, self.max_wait = retries, poll_interval, max_wait
        self.sleep, self.clock = sleep, clock

    def request(self, method, url, **kwargs):
        # Redirects could disclose the public entity token or signed query. Never
        # follow them, and never interpolate requests exceptions into logs.
        for attempt in range(self.retries):
            try:
                response = self.session.request(method, url, allow_redirects=False,
                                                timeout=(15, 120), **kwargs)
                if 200 <= response.status_code < 300:
                    return response
                status = response.status_code
                response.close()
                if status not in (408, 429, 500, 502, 503, 504):
                    raise AcquisitionError(f"Public dashboard request rejected (HTTP {status})")
            except requests.RequestException:
                pass
            if attempt + 1 < self.retries:
                self.sleep(min(2**attempt, 8))
        raise AcquisitionError("Public dashboard request failed after bounded retries") from None

    def json_request(self, method, url, **kwargs):
        response = self.request(method, url, **kwargs)
        try:
            return response.json()
        except (ValueError, TypeError):
            raise AcquisitionError("Public dashboard returned invalid JSON") from None
        finally:
            response.close()

    def discover(self):
        response = self.request("GET", PORTAL_URL)
        try:
            token_match = re.search(r'"entityToken":"([^"\n]+)"', response.text)
            ids_match = re.search(r'"dashboardIds":(\[[^\]]+\])', response.text)
            token = json.loads('"'+token_match.group(1)+'"')
            dashboard_id = json.loads(ids_match.group(1))[0]
            if not re.fullmatch(r"[a-f0-9-]{36}", dashboard_id):
                raise ValueError("invalid identifier")
        except (AttributeError, ValueError, IndexError, TypeError):
            raise AcquisitionError("Public portal metadata changed; cannot discover dashboard") from None
        finally:
            response.close()
        dashboard = self.json_request("GET", f"{OFFICIAL_HOST}/api/dashboards/{dashboard_id}", params={"entityToken":token})
        try:
            dashboard = dashboard["data"]
            metrics = {visual["id"] for view in dashboard["content"]["views"]
                       for visuals in view["visuals"].values() for visual in visuals
                       if visual.get("displayName") == "Crash Data"}
            if len(metrics) != 1:
                raise ValueError("ambiguous metric")
            metric_id = metrics.pop()
            if not re.fullmatch(r"[a-f0-9-]{36}", metric_id):
                raise ValueError("invalid metric")
            metric = dashboard["metrics"][metric_id]
            return token, dashboard_id, metric_id, metric
        except (KeyError, ValueError, TypeError):
            raise AcquisitionError("Public Crash Data metric configuration changed") from None

    @staticmethod
    def validate_destination(link):
        try:
            parsed = urlsplit(link)
            safe = (parsed.scheme == "https" and parsed.hostname == DOWNLOAD_HOST
                    and parsed.port in (None, 443) and not parsed.username and not parsed.password
                    and not parsed.fragment and re.fullmatch(r"/njdot/downloadReports/[a-f0-9]{64}", parsed.path))
        except (ValueError, TypeError):
            safe = False
        if not safe:
            raise AcquisitionError("Public export returned an unexpected download destination")

    def acquire(self, *, start_year=2019, end_year=2025, refresh=False, input_csv=None,
                extra_columns=False, report_path=None):
        if not 2019 <= start_year <= end_year <= 2025:
            raise AcquisitionError("Choose a dashboard year range within 2019–2025; 2026 is excluded")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        target = self.cache_dir / f"Crash-{start_year}-{end_year}.csv"
        temporary = target.with_name(f".{target.name}.{os.getpid()}.part")
        raw_temporary = target.with_name(f".Crash-public.{os.getpid()}.part")
        report_path = Path(report_path) if report_path else self.cache_dir / "manifest.json"
        attempt_path = report_path.with_name(report_path.stem+"-last-attempt.json")
        report = dict(status="running", started_at=utc_now(), portal=PORTAL_URL,
                      start_year=start_year, end_year=end_year, csv_path=os.fspath(target.resolve()))
        try:
            token, dashboard_id, metric_id, metric = self.discover()
            params = {"entityToken":token}
            endpoint = f"{OFFICIAL_HOST}/api/dashboards/{dashboard_id}/metrics/{metric_id}"
            filters = [{"field":"Date & Time of Crash", "filter":"script",
                        "script":{"params":{"param1":start_year,"param2":end_year},"script":"betweenyears"},
                        "pseudo":"year", "type":"datetime", "datasetId":metric["dataSchema"]["datasetId"],
                        "key":"Date & Time of Crash-year"}]
            # Match the actual public UI export, including ordering. Its config
            # lists Time of Crash but the working UI omits that unavailable field.
            shown = set(metric["dataSchema"]["shownColumns"]) - {"Time of Crash"}
            columns = [column for column in metric["vizSchema"]["columnOrder"] if column in shown]
            if set(columns) != shown:
                raise AcquisitionError("Public CSV column configuration changed")
            if extra_columns:
                allowed = metric["vizSchema"]["columnOrder"]
                if not set(EXTRA_COLUMNS).issubset(allowed):
                    raise AcquisitionError("Requested additional columns are absent from public configuration")
                columns.extend(column for column in EXTRA_COLUMNS if column not in columns)
            # The verified public export is the unfiltered UI snapshot. Filter it
            # locally and reconcile both the full and selected public row counts.
            payload = dict(filters=[], fileName="Crash", columns=columns)
            search = self.json_request("POST", endpoint+"/search", params=params,
                                       json={"filters":filters,"sorting":{"direction":"desc","sortBy":"id_cr"}})
            expected = search.get("data", {}).get("totalRows")
            if type(expected) is not int or expected < 1:
                raise AcquisitionError("Public dashboard did not supply a positive full row count")
            export_search = self.json_request("POST", endpoint+"/search", params=params,
                                              json={"filters":[],"sorting":{"direction":"desc","sortBy":"id_cr"}})
            export_expected = export_search.get("data", {}).get("totalRows")
            if type(export_expected) is not int or export_expected < expected:
                raise AcquisitionError("Public dashboard did not supply a valid export row count")
            report.update(expected_rows=expected, dashboard_id=dashboard_id, metric_id=metric_id,
                          request=payload, selection_filters=filters, export_expected_rows=export_expected,
                          checked_at=utc_now())
            logger.info("Public dashboard expects %s crashes for %s–%s", expected, start_year, end_year)
            if target.exists() and report_path.exists() and not refresh and input_csv is None:
                try:
                    prior = json.loads(report_path.read_text(encoding="utf8"))
                    if prior.get("status") == "succeeded" and prior.get("request") == payload and prior.get("expected_rows") == expected:
                        verified = validate_csv(target, start_year=start_year, end_year=end_year, expected_rows=expected)
                        if extra_columns and not set(EXTRA_COLUMNS).issubset(verified["headers"]):
                            raise AcquisitionError("Requested additional CSV columns are unavailable")
                        if prior.get("csv", {}).get("sha256") == verified["sha256"]:
                            prior.update(checked_at=utc_now(), cache_reused=True)
                            atomic_json(report_path, prior)
                            return prior
                except (AcquisitionError, ValueError, OSError):
                    pass
                logger.info("Cached snapshot changed or is incomplete; reacquiring while preserving its files")
            atomic_json(attempt_path, report)
            if input_csv is not None:
                source = filter_public_export(input_csv, temporary, start_year=start_year, end_year=end_year)
            else:
                status = self.json_request("POST", endpoint+"/download", params=params, json=payload)
                deadline = self.clock()+self.max_wait
                while status.get("status") != "FILE_READY":
                    if status.get("status") != "FILE_IN_PROGRESS":
                        raise AcquisitionError("Public export returned an unsupported status")
                    if self.clock() >= deadline:
                        raise AcquisitionError("Public export timed out; the same request can be retried later")
                    self.sleep(self.poll_interval)
                    status = self.json_request("POST", endpoint+"/download/status", params=params, json=payload)
                link = status.get("link")
                self.validate_destination(link)
                response = self.request("GET", link, stream=True, headers={"Accept-Encoding":"identity"})
                try:
                    with raw_temporary.open("wb") as output:
                        for chunk in response.iter_content(chunk_size=1024*1024):
                            if chunk:
                                output.write(chunk)
                    length = response.headers.get("Content-Length")
                    if length is not None and (not length.isdigit() or raw_temporary.stat().st_size != int(length)):
                        raise AcquisitionError("Downloaded CSV length does not match the server response")
                except requests.RequestException:
                    raise AcquisitionError("Public export transfer was interrupted") from None
                finally:
                    response.close()
                retrieved_at = utc_now()
                source = filter_public_export(raw_temporary, temporary, start_year=start_year, end_year=end_year)
                source.pop("input_path", None)
                source.update(kind="public_metric_export", retrieved_at=retrieved_at)
                raw_temporary.unlink()
            if source["rows"] != export_expected:
                raise AcquisitionError(f"Full CSV row count {source['rows']} does not match public export count {export_expected}")
            validated = validate_csv(temporary, start_year=start_year, end_year=end_year, expected_rows=expected)
            if extra_columns and not set(EXTRA_COLUMNS).issubset(validated["headers"]):
                raise AcquisitionError("Requested additional CSV columns are unavailable")
            os.replace(temporary, target)
            report.update(status="succeeded", finished_at=utc_now(), source=source, csv=validated, cache_reused=False)
            atomic_json(report_path, report)
            atomic_json(attempt_path, report)
            logger.info("Validated %s unique crashes; years=%s; SHA256=%s", validated["unique_ids"], validated["years"], validated["sha256"])
            return report
        except Exception as error:
            temporary.unlink(missing_ok=True)
            raw_temporary.unlink(missing_ok=True)
            safe_error = str(error) if isinstance(error, AcquisitionError) else "Dashboard acquisition failed; inspect local inputs and retry"
            report.update(status="failed", finished_at=utc_now(), error=safe_error)
            atomic_json(attempt_path, report)
            if isinstance(error, AcquisitionError):
                raise
            raise AcquisitionError(safe_error) from None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Download and validate public NJDOT crashes through2025")
    parser.add_argument("--start-year", type=int, default=2019)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--cache-dir", type=Path, default=Path("../data/raw/njdot-dashboard"))
    parser.add_argument("--report", type=Path)
    parser.add_argument("--input-csv", type=Path, help="Filter an already downloaded public export instead of downloading it again")
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--extra-columns", action="store_true", help="Request additional non-personal columns present in public configuration")
    parser.add_argument("--max-wait", type=int, default=1800, help="Maximum export-generation wait in seconds")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        DashboardDownloader(cache_dir=args.cache_dir,max_wait=args.max_wait).acquire(
            start_year=args.start_year,end_year=args.end_year,refresh=args.refresh_cache,
            input_csv=args.input_csv,extra_columns=args.extra_columns,report_path=args.report)
    except AcquisitionError as error:
        logger.error("%s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
