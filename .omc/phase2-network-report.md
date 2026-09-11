# Phase 2 official boundaries and road network

## Implemented

- Replaced the guessed Socrata boundary loader with the NJGIN ArcGIS municipal layer. The loader validates layer schema, dynamically verifies the service count against fetched pagination, normalizes Polygon to MultiPolygon, repairs geometry in PostGIS, and bulk-upserts on `muni_code`.
- Added a shared bounded ArcGIS client with deterministic object-ID pagination, connect/read timeouts, bounded retry for connection/429/5xx failures, valid-response-only atomic cache writes, offline replay, schema errors, duplicate-ID detection, and no-progress detection. Invalid JSON, non-UTF-8 cache data, malformed metadata values, and null pagination capabilities are evicted/refetched online and fail explicitly offline.
- Added the NJDOT all-public-roads loader. It retains each Esri path as an independent stable `NJDOT:{OBJECTID}:{path_index}` source path, keeps full `[longitude, latitude, M]` precision in `LINESTRINGM(4326)`, derives path measure bounds from actual vertices, and retains valid XY analysis coverage when a path cannot be used for linear referencing.
- Analysis segments are municipality intersections split in EPSG:26918 (meters) at approximately 0.1 mile, stored in EPSG:4326 with positive geodesic miles, and keyed by source path + municipality + intersection part + piece. Cross-municipality boundary geometry intentionally has a stable segment per municipality.
- Same-snapshot reruns upsert without replacing integer segment IDs. Changed route measures/source geometry/boundaries/segment configuration, stale pieces, and omitted prior source paths are rejected with a fresh-database requirement so crash/HIN foreign keys are never silently repointed.
- Road ingestion remains strict by default. Every successful result requires a nonzero source and nonempty analysis/route outputs when claiming complete coverage. `--allow-partial` only accepts documented record-level omissions after the fetched count matches the nonzero source count and a nonempty analysis network exists. Reports always include `analysis_coverage_complete`, `reference_coverage_complete`, and `partial_coverage_accepted`; the orchestrator exposes the opt-in as `--allow-partial-roads`.
- Added `road_routes`, nullable indexed `road_segments.sri`, nullable unique `road_segments.source_id`, and the functional GiST index on `(geom::geography)`. `init_schema.py` upgrades an existing Phase 1 schema additively and remains rerunnable.
- Retired `ingest_osm_roads.py` as an explicit nonzero shim pointing to the NJDOT loader.

## Verification evidence

- The initial implementation reached **13 passing focused tests** before the statewide scalability review. The final network and orchestration scope reached **61 passing tests** after replacing quadratic prefix joins with exact-key joins/JSONB bulk staging, hardening malformed-cache recovery, and adding explicit partial-coverage acceptance.
- Scoped suite: `TEST_DATABASE_URL=postgresql://test_user:test_password@127.0.0.1:55432/nj_hin_network_test python -m pytest backend/tests/test_official_network.py backend/tests/test_njdot_crash_ingestion.py -q` -> **61 passed**.
- Schema: ran `backend/scripts/init_schema.py` twice against isolated `nj_hin_network_test`; both exited 0. Verified `road_routes.geom` is `LINESTRINGM`, dimension 3, SRID 4326, and all three road-segment source/SRI/geography indexes exist.
- Live boundary source: reported/fetched/accepted **564/564/564**, zero rejections. Rollback smoke load produced 564 valid `MULTIPOLYGON` geometries. Parent integration subsequently reported 564 rows across 21 counties and an idempotent repeat with zero inserts.
- Live road sample: 20 official features produced 20 XY paths, 20 calibrated M paths, zero path rejections, 20 route rows, 124 municipality-clipped analysis segments, and zero unmatched paths in a rollback smoke test.
- Statewide integration committed **105,968 calibrated route paths** and **460,639 analysis segments**, covering all **564 municipalities in 21 counties**, with zero invalid stored geometries. The complete 105,977-feature source contained 9 null-geometry features and 5 additional paths with invalid M values; these omissions remain explicit in the report and require `--allow-partial` for a successful loader exit.
- Performance regression: the complete-snapshot guard handles 2,000 path IDs and 4,000 segments below 0.5 seconds in isolated PostGIS. A direct 1,000-path JSONB staging benchmark (1,000 M routes plus 1,000 clipped segments) completed in 0.391 seconds.
- Static import check: `py_compile` passed for all changed model/schema/ingestion modules. `git diff --check` reported no whitespace errors.

## Commands

```powershell
$env:DATABASE_URL='postgresql://.../nj_hin_real'
backend/venv/Scripts/python.exe backend/scripts/init_schema.py
backend/venv/Scripts/python.exe backend/scripts/ingest_municipalities.py --cache-dir data/raw/official/municipalities --report data/processed/phase2-municipalities.json
backend/venv/Scripts/python.exe backend/scripts/ingest_njdot_roads.py --cache-dir data/raw/official/roads --segment-length 0.1 --batch-size 1000 --allow-partial --report data/processed/phase2-roads.json
```

Both loaders also accept `--offline` for cache-only replay and `--refresh-cache` to deliberately replace cached responses. These flags are mutually exclusive.

## Coverage and limitations

- `564` municipalities and `105,977` roads are observed source baselines, not hard-coded acceptance gates. A changed live count emits a warning; completeness is determined by matching fetched rows to the service's current count.
- NJDOT functional class 1-4 maps to arterial, 5-6 to collector, 7 to local; unknown values remain `unclassified` rather than being dropped. NJDOT SRI is used as `road_name` because this source layer does not expose street names.
- Reference-path rejection is reported separately from XY analysis rejection. Invalid/missing M never gets synthesized. The crash loader must handle multiple distinct `ST_LocateAlong` results as ambiguous.
- Road geometry is a current-network snapshot used with historical 2022 crashes; this is an approximate historical match and must be reported as such.
- Boundary clipping produced 1,460 positive-length micro-slivers shorter than one centimeter. They are retained rather than silently removed; statistical handling is deferred to the Phase 3 method review unless representative analyses show a blocking effect.
- The separate Phase 3 statistical-method limitation remains unchanged.
