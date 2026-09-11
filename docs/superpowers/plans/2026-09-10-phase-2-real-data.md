# Phase 2: verified map integration and official NJ data

User authority: double-check PostGIS/background map integration; when verified, proceed to Phase 2 of the deployment audit. No publish, push, or production database changes.

## Map gate — passed

The independent read-only audit found valid EPSG:4326 database geometries and exact all-row GeoJSON equality for both local analyses (2,000 crash features and 38 HIN features). Leaflet converts longitude/latitude once and uses EPSG:3857. Independent projection against actual background tile URLs placed 668 visible crash markers within 0.679 pixels, and checked HIN endpoints within 0.444 pixels. PostGIS/Leaflet Web Mercator formulas agreed within 1.9e-9 meters.

The visible mismatch is synthetic provenance: the local test database holds two artificial fixtures (500 randomly generated straight roads and 7,000 crashes), not actual streets. Analysis 1 predates the second fixture load and is stale. Raw crash positions are deliberately preserved rather than moved to their assigned road.

A separate assignment defect was reproduced: degree-based nearest-neighbor selection chose a non-nearest metric road for two West Windsor crashes. Phase 2 fixes metric ranking and refreshes assignments for each analysis.

## Global constraints

- Preserve uncommitted Phase 1 work on `fix/phase-1-deployment`; no commits or deployment.
- Use official source data, validated geometry, stable identities, idempotent bulk loading, explicit rejection/coverage reports, bounded network timeouts and failure exits.
- Do not mix real records into synthetic fixtures. Load a separate local `nj_hin_real` database.
- Runtime API requires no GDAL; ingestion runs offline. Keep geometry EPSG:4326 and calculate distances/lengths in meters.
- Report coordinate versus route/milepost location quality. Do not fabricate bicycle involvement or serious-injury distinctions unavailable in the Accidents source.
- Keep Phase 3 statistical-method changes separate; existing severity-weighted significance remains a documented limitation.
- Regression tests before implementation; author and independent reviewer are separate lanes.

## Task 1: Metric batch analysis

Own `backend/app/services/crash_service.py`, `hin_service.py`, and new service tests only. Replace per-crash requests with a single bounded LATERAL selection and bulk update using geography ST_DWithin and exact ST_Distance, deterministic segment-id tie breaking, and validated nonnegative finite radius. Preserve Crash.geom. Forced refresh must clear assignments outside the new threshold. Each analysis refreshes its municipality assignments; serialize same-municipality analyses through database transaction advisory locking so differing thresholds cannot interfere while statistics are gathered. Keep APIs compatible. Batch segment counts and summary calculations without changing severity semantics. Batch equity overlay uses nullable-safe `svi_percentile > 75` (stored scale 0–100), deterministic intersecting tract choice, and retains the score in avg_svi_score. Avoid N+1 corridor relationship access. Add real PostGIS regression tests for degree-versus-meter selection, threshold decrease/clear, municipality isolation, zero-crash roads, date filters, null/mismatched score versus percentile, raw geometry preservation and bounded query counts. Parent coordinates schema/index changes.

## Task 2: Boundaries and authoritative road network

Replace guessed municipality loader with paginated NJGIN government boundaries; Polygon becomes valid MultiPolygon. Replace broken OSM importer with official NJDOT all-public-roads ingestion. Preserve source SRI and calibrated mileposts/M values for crash linear referencing; split analysis roads into approximately 0.1-mile pieces, assign municipalities spatially, compute geodesic length, stable source keys and idempotent upserts. Retain source route geometry separately so splits do not destroy reference measures. Validate pagination, expected counts, schema and geometry. Add fixture/unit tests and isolated PostGIS smoke tests. Exact schema contract is agreed before writing shared models. Do not overwrite synthetic data or hide missing source records.

## Task 3: NJDOT crash ingestion and orchestration

Extend the working county/year ZIP parser for all 21 counties and explicitly selected years, streaming/batching and idempotent source keys. Resolve municipality using county plus normalized unambiguous name (preserving township/city/borough distinctions); verify coordinate containment. Prefer valid reported coordinates; recover missing coordinates using SRI + calibrated milepost on official route geometry. Reject/record unresolved or ambiguous locations, never invent them. Distinguish coordinate and linear-reference quality. Validate archive/schema/date/severity and report counts by county/year/rejection/location method. Orchestrate boundaries → roads → crashes with cache, resumability, nonzero failure status and a durable manifest. Retire broken alternate crash path through a clear replacement entry point. Test duplicates, ZIP shape, positive longitude, normalized names, missing coordinates, route direction/measure gaps and source failures.

## Task 4: Integration and verification

Create a fresh local PostGIS real-data database and run explicit schema setup. Load statewide boundaries and roads, then verified available county/year crash archives. Initially target 2022 (2023/2024 tested legacy URLs return 404); expand only after availability checks. Verify actual coverage, repeat-run idempotency, valid geometry, route-derived points, counts and representative analyses. Rebuild/restart the local API pointing at the real database; inspect browser crash/HIN alignment and polling. Run backend/frontend tests, CI build, Docker build as affected. Update deployment/data documentation and present precise coverage and limitations. Independent whole-change reviewer evaluates code and evidence before completion.

## Decisions and progress

- Phase 2 begun after the map gate passed.
- Use NJDOT routes rather than OSM for first statewide pipeline: most inspected 2022 Mercer records have no coordinates but do have SRI/milepost, making calibrated official route geometry necessary.
- Use a separate local real-data database, leaving both synthetic fixtures recoverable.
- User AGENTS requests parallel independent work; disjoint ownership and an agreed schema contract prevent conflicting writes.
- Tasks 1–3 complete and independently reviewed. Metric assignment now uses a dedicated-connection session advisory lock spanning intermediate commits; an unconfirmed acquire/unlock invalidates the physical connection so pooled sessions cannot leak the lock.
- Task 4: real statewide 2022 ingestion, browser/metric/API checks, 94 backend tests, two frontend tests, CI production build and Docker build passed. See [MAP_VERIFICATION.md](../../MAP_VERIFICATION.md) and [DATA_PIPELINE.md](../../DATA_PIPELINE.md) for coverage and limitations.
- Task 4 complete: full offline same-source replay exited 0 with all three stages and 21 crash archives successful. Crash inserts0/duplicates219509; road count460639 and ID aggregates unchanged; reference paths105968. Original source omissions remain explicit. Independent code and documentation review approved; no remaining Phase 2 blockers. No deployment or commit performed.
