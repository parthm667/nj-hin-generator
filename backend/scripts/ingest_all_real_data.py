#!/usr/bin/env python3
"""Run the official real-data loaders in dependency order."""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent.parent

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StageCommand:
    stage: str
    argv: list[str]
    report_path: Path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, payload: Mapping) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _shared_cache_flags(args: argparse.Namespace) -> list[str]:
    flags = []
    if args.offline:
        flags.append("--offline")
    if args.refresh_cache:
        flags.append("--refresh-cache")
    return flags


def build_stage_commands(args: argparse.Namespace) -> list[StageCommand]:
    """Build only selected commands, always preserving source dependencies."""
    cache_dir = Path(args.cache_dir)
    manifest_path = Path(args.report)
    report_dir = manifest_path.parent
    commands = []
    cache_flags = _shared_cache_flags(args)

    if not args.skip_municipalities:
        report = report_dir / "municipalities-report.json"
        commands.append(StageCommand("boundaries", [
            sys.executable, os.fspath(SCRIPT_DIR / "ingest_municipalities.py"),
            "--cache-dir", os.fspath(cache_dir / "municipalities"),
            "--report", os.fspath(report), *cache_flags,
        ], report))
    if not args.skip_roads:
        report = report_dir / "roads-report.json"
        road_command = [
            sys.executable, os.fspath(SCRIPT_DIR / "ingest_njdot_roads.py"),
            "--cache-dir", os.fspath(cache_dir / "roads"),
            "--report", os.fspath(report),
            "--segment-length", str(args.segment_length),
            "--batch-size", str(args.batch_size), *cache_flags,
        ]
        if args.allow_partial_roads:
            road_command.append("--allow-partial")
        commands.append(StageCommand("roads", road_command, report))
    if not args.skip_crashes:
        report = report_dir / "crashes-report.json"
        crash_command = [
            sys.executable, os.fspath(SCRIPT_DIR / "ingest_njdot_crashes.py"),
            "--start-year", str(args.start_year), "--end-year", str(args.end_year),
            "--cache-dir", os.fspath(cache_dir / "crashes"),
            "--report", os.fspath(report), "--batch-size", str(args.batch_size),
            *cache_flags,
        ]
        for county in args.county or []:
            crash_command.extend(["--county", county])
        commands.append(StageCommand("crashes", crash_command, report))
    return commands


def _read_stage_report(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def run_pipeline(
    args: argparse.Namespace,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> int:
    if args.start_year > args.end_year:
        raise ValueError("start-year must be less than or equal to end-year")
    if args.offline and args.refresh_cache:
        raise ValueError("--offline and --refresh-cache cannot be used together")
    manifest_path = Path(args.report)
    manifest = {
        "pipeline": "official_nj_real_data",
        "status": "running",
        "started_at": _utc_now(),
        "updated_at": _utc_now(),
        "parameters": {
            "start_year": args.start_year,
            "end_year": args.end_year,
            "counties": args.county or "all",
            "cache_dir": os.fspath(Path(args.cache_dir)),
            "offline": args.offline,
            "refresh_cache": args.refresh_cache,
            "segment_length": args.segment_length,
            "batch_size": args.batch_size,
            "allow_partial_roads": args.allow_partial_roads,
        },
        "stages": [],
    }
    _atomic_json(manifest_path, manifest)

    for command in build_stage_commands(args):
        logger.info("Starting %s ingestion", command.stage)
        started_at = _utc_now()
        result = runner(command.argv, check=False)
        returncode = int(result.returncode)
        stage = {
            "name": command.stage,
            "status": "succeeded" if returncode == 0 else "failed",
            "started_at": started_at,
            "finished_at": _utc_now(),
            "returncode": returncode,
            "command": command.argv,
            "report_path": os.fspath(command.report_path),
            "report": _read_stage_report(command.report_path),
        }
        manifest["stages"].append(stage)
        manifest["updated_at"] = _utc_now()
        if returncode:
            manifest["status"] = "failed"
            _atomic_json(manifest_path, manifest)
            logger.error("%s ingestion failed with exit code %s", command.stage, returncode)
            return 1
        _atomic_json(manifest_path, manifest)

    manifest["status"] = "succeeded"
    manifest["finished_at"] = _utc_now()
    manifest["updated_at"] = manifest["finished_at"]
    _atomic_json(manifest_path, manifest)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Load official NJ boundaries, roads, and crashes in dependency order"
    )
    parser.add_argument("--start-year", type=int, default=2022)
    parser.add_argument("--end-year", type=int, default=2022)
    parser.add_argument("--county", action="append",
                        help="Crash county; repeat for multiple counties, omit for all 21")
    parser.add_argument("--cache-dir", type=Path, default=PROJECT_DIR / "data" / "raw")
    parser.add_argument("--report", type=Path,
                        default=PROJECT_DIR / "data" / "processed" / "real-data-manifest.json")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--segment-length", type=float, default=0.1)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--skip-municipalities", action="store_true")
    parser.add_argument("--skip-roads", action="store_true")
    parser.add_argument(
        "--allow-partial-roads",
        action="store_true",
        help="Pass explicit documented partial-coverage acceptance to the road loader",
    )
    parser.add_argument("--skip-crashes", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.batch_size <= 0:
        parser.error("--batch-size must be positive")
    if args.segment_length <= 0:
        parser.error("--segment-length must be positive")
    try:
        return run_pipeline(args)
    except ValueError as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
