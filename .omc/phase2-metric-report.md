# Phase 2 Task 1 — metric batch analysis report

## Outcome

- Replaced per-crash ORM snapping with one bulk `UPDATE` fed by a bounded
  `LEFT JOIN LATERAL` candidate search.
- Candidate filtering and ranking now use PostGIS geography meters via
  `ST_DWithin` and exact `ST_Distance`; equal distances use `segment_id` as a
  deterministic tie-breaker.
- Snap radii are rejected before database access unless finite and
  nonnegative. Forced refreshes write null `segment_id` and `snap_distance`
  when no segment is inside the new threshold. `Crash.geom` is never updated.
- Every HIN run force-refreshes the municipality's assignments and holds a
  same-municipality session advisory lock on a dedicated connection for the
  entire run, including all service commits. Explicit unlock runs in `finally`,
  and unconfirmed acquisition/unlock invalidates the physical connection rather
  than returning a potentially locked session to the connection pool.
- Segment/date statistics, municipality summaries, corridor assignment,
  equity overlay, and analysis summaries are batched. Severity-weighted
  statistical semantics remain unchanged for Phase 3.
- Equity uses the stored 0–100 `svi_percentile > 75`, treats null percentile as
  false, retains nullable `svi_score` in `avg_svi_score`, and chooses the
  lexicographically first intersecting `tract_id` deterministically.
- Crash GeoJSON now exposes the stored nullable `geocode_quality` so API and
  export consumers can distinguish reported from route/milepost-derived
  coordinates without moving points or inventing provenance.

## TDD and verification evidence

- Initial PostGIS regression run: `11 failed, 1 passed`. The failures directly
  reproduced metric-vs-degree nearest selection, stale forced assignments,
  invalid-radius acceptance, N+1 statistics/corridors, null-unsafe score-based
  equity, missing run refresh, and missing advisory locking.
- Final focused run after review remediation:
  `backend/venv/Scripts/python.exe -m pytest backend/tests/test_metric_analysis.py -q`
  with `DATABASE_URL` and `TEST_DATABASE_URL` set to the isolated local test
  database: `15 passed`.
- Fresh compatibility run against `nj_hin_phase2_tests`, initialized from the
  current schema and indexes:
  `pytest test_config.py test_geojson.py test_postgis_integration.py
  test_runtime.py test_metric_analysis.py -q`: `33 passed`.
- `py_compile` passed for both services and the new test module.
- `git diff --check` passed for the two tracked service files.
- Rollback isolation check after tests returned zero test rows across
  municipalities, roads, crashes, analyses, and metric census tracts.
- Review regression RED: two injected PostgreSQL failures reproduced a retained
  session lock after unlock failure and replacement of an acquisition error by
  `InFailedSqlTransaction`. GREEN: both injection tests pass after invalidating
  any lock connection whose acquisition/unlock cannot be confirmed. The final
  database check found zero advisory locks in the analysis namespace.
- Location-provenance serializer RED: the focused test reported `1 failed,
  1 passed` because crash GeoJSON omitted `geocode_quality`. GREEN: the focused
  test is `2 passed`; a fresh GeoJSON/PostGIS/runtime/metric run is `26 passed`.

## Loaded-record coverage safeguards

- Added a query-driven coverage service and
  `GET /api/municipalities/{muni_id}/coverage`. It reports sorted years with
  positive loaded crash-record counts, the per-year counts, and their total;
  it intentionally does not claim source/archive completeness.
- Analysis creation now rejects every absent year in the inclusive requested
  range with the agreed structured `422 missing_crash_data` response before an
  analysis row or background task is created. The HIN service repeats this
  validation inside its municipality lock so direct/background callers cannot
  bypass the API check.
- Analysis details expose `data_status`, `data_message`, `available_years`, and
  `missing_years` without modifying legacy stored status or statistics. A
  completed result is `no_data` when its selected period is empty,
  `missing_years` when the range has gaps, `stale` when the complete range's
  current count differs from `total_crashes`, and otherwise `ready`.
- Shared freshness checks return a structured 409 before serving stale or
  otherwise invalid completed crash, HIN, summary, analysis-PDF, or alternate
  GeoJSON export results. The PDF/CSV 501 endpoints remain unchanged.
- Coverage TDD RED: the first run was `11 failed, 1 passed`. After adding the
  alternate export case, the new bypass regression was separately RED at
  `1 failed, 4 passed`. GREEN: `test_coverage.py` is `13 passed`.
- Fresh full backend verification against rollback-isolated
  `nj_hin_phase2_tests`: `python -m pytest backend/tests -q` returned
  `113 passed, 7 warnings in 10.02s`. `py_compile` passed for all affected
  modules and `git diff --check` passed for tracked affected files. A database
  residue query found zero test municipalities, crashes, and analyses in the
  reserved negative-ID range.

## Changed files

- `backend/app/services/crash_service.py`
- `backend/app/services/hin_service.py`
- `backend/app/services/coverage_service.py` (new)
- `backend/app/routers/municipalities.py`
- `backend/app/routers/analysis.py`
- `backend/app/routers/export.py`
- `backend/app/models/schemas.py`
- `backend/tests/test_metric_analysis.py` (new)
- `backend/tests/test_geojson.py`
- `backend/tests/test_coverage.py` (new)
- `.omc/phase2-metric-report.md` (this report)

## Coordination requirement / concerns

- The new bounded predicate requires the schema owner's functional geography
  GiST index; the existing `gist (geom)` geometry index alone does not support
  `geom::geography` searches. Required shape:
  `CREATE INDEX idx_segments_geography ON road_segments USING gist
  ((geom::geography));`
- Fresh schema inspection confirmed `idx_segments_geography` exists in
  `nj_hin_phase2_tests`.
- Tests exercise the real PostGIS 3.3 instance using negative-ID rows inside
  rollback-only outer transactions; the synthetic fixtures were not changed.
- Freshness is deliberately count-based: it detects backfills and record-count
  changes but cannot detect arbitrary same-count edits. This is the accepted
  bounded safeguard for immutable official source IDs.
- Test output includes existing third-party/model deprecation warnings
  (`datetime.utcnow`, Starlette/AnyIO, ReportLab); model files were outside this
  task's ownership.
- No dependency installs, schema edits, commits, pushes, deployment, or
  production database changes were performed.
