# Phase 2 crash ingestion report

Date: 2026-09-10

## Implemented

- Replaced the coordinate-only county parser with an official NJDOT Accidents
  loader for all 21 counties and caller-selected year ranges. The safe default
  is 2022 because the verified canonical 2023 and 2024 URLs return 404.
- Added bounded/retried downloads, validated ZIP/member/50-column records,
  streaming CSV reads, batch location resolution, bulk `ON CONFLICT` inserts,
  stable external IDs of at most 50 characters, and atomic JSON reports.
- Crash manifests predeclare the complete requested county/year matrix, persist
  each archive as pending/running/succeeded/failed, expose status counts, and
  report overall success only after every requested archive succeeds. A hard or
  operator interruption therefore leaves a resumable running manifest rather
  than a false success.
- Municipality matching is county-scoped and retains CITY, TOWN, BOROUGH, and
  TOWNSHIP distinctions. A legacy `--muni-id` is a guard after name/county
  resolution; it never assigns every input row blindly.
- Reported coordinates are normalized to west longitude and accepted only when
  covered by the resolved municipality. Missing, malformed, out-of-bounds, or
  municipality-exterior reported coordinates fall back to exact NJDOT SRI and
  milepost location on calibrated `LineStringM` route paths.
- Route lookup uses `ST_LocateAlong` -> `ST_Dump` -> `ST_Force2D`, bounded by
  SRI and actual path measure limits and then by municipality geometry. Near-
  identical shared endpoints collapse to the first deterministic actual route
  point; meaningfully distinct candidates are rejected as ambiguous.
- Location resolution is one JSONB/PostGIS query per batch, not one query per
  crash. Accepted rows record `geocode_quality` as `reported` or
  `route_milepost`. The loader does not invent bicycle involvement or a serious
  injury classification absent from this source.
- Replaced the obsolete Socrata entry point with a compatibility forwarder.
- Rebuilt orchestration as boundaries -> roads -> crashes using
  `sys.executable`, shared cache/offline/refresh controls, immediate failure
  stop, and an atomic manifest embedding each stage report.

## Statewide source-name audit

Read-only comparison used all 21 validated 2022 archives under
`data/raw/official/crashes` and the 564 boundaries already in `nj_hin_real`.
Before explicit source aliases, 3,837 records had names that did not match the
current NJGIN boundary labels. The observed differences were Mount Ephriam
(source typo), Orange, Fairfield, South Orange Village, Milford,
Parsippany-Troy Hills, Passaic Township/Long Hill Township, Point Pleasant
Beach, Lower Alloways Creek, and Sandyston (source typo). Explicit canonical
aliases reduced unmatched municipality-name records to zero. No crash rows were
inserted into `nj_hin_real` during this audit.

## Verification

Fresh focused command:

```text
DATABASE_URL=TEST_DATABASE_URL=postgresql://test_user:test_password@127.0.0.1:55432/nj_hin_phase2_tests
python -m compileall -q <owned scripts and test>
python -m pytest backend/tests/test_njdot_crash_ingestion.py -q
python backend/scripts/ingest_njdot_crashes.py --help
python backend/scripts/ingest_all_real_data.py --help
```

Result: 35 tests passed in 4.19 seconds. The tests include rollback-isolated
PostGIS checks for calibrated non-linear measures, batch reported-coordinate
preference, route fallback, repeat-run idempotency, complete initial manifest
declaration, status counts, and mid-run interruption state. A post-test read
found zero retained test municipality or crash rows in `nj_hin_phase2_tests`.

## CLI

Mercer 2022:

```powershell
backend/venv/Scripts/python.exe backend/scripts/ingest_njdot_crashes.py `
  --start-year 2022 --end-year 2022 --county Mercer `
  --cache-dir data/raw/official/crashes `
  --report data/processed/njdot-crashes-report.json --batch-size 1000
```

Omit `--county` for all 21 counties. The orchestrator accepts the same year,
repeatable county, cache, report, offline, refresh-cache, and batch controls,
plus stage skip flags and road segment length.

## Files

- `backend/scripts/ingest_njdot_crashes.py`
- `backend/scripts/ingest_nj_crash_data.py`
- `backend/scripts/ingest_all_real_data.py`
- `backend/tests/test_njdot_crash_ingestion.py`

No commit, deployment, dependency installation, or synthetic-database mutation
was performed during the original Phase 2 crash-ingestion work.

## Data correctness follow-up

- NJDOT severity `I` is now represented as `injury_unknown`, never as a
  fabricated minor or serious injury. Bicycle involvement is nullable because
  the Accidents archive does not supply it.
- Four nullable, nonnegative casualty counts are parsed without converting
  blanks to zero. GeoJSON exposes these fields and municipality summaries
  disclose injury, bicycle, and casualty completeness explicitly.
- `--enrich-only` updates those source fields by stable external ID and never
  resolves, inserts, or overwrites a crash geometry. The real cached 2017–2022
  run inspected 1,509,938 source rows and updated all 1,209,451 existing crash
  rows; 300,483 never-loaded source IDs and four malformed rows remain reported.
- The official CDC/ATSDR 2020 U.S.-ranked SVI loader fetched and loaded all
  2,175 New Jersey tracts. It retained 10 CDC no-data ranks as NULL, rejected
  zero features, and produced zero invalid geometries. Provenance and ranking
  scope are recorded in the durable report.
- Focused verification passed 59 tests plus bytecode compilation. The full
  backend run passed 160 tests; three failures were in concurrently owned
  dataset-revision/network fixtures and were handed to the parent for review.

Real-data reports:

- `data/processed/casualty-enrichment-report.json`
- `data/processed/svi-2020-report.json`
