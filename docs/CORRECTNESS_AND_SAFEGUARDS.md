# Correctness and anonymous-use safeguards

Local implementation and verification, September 10, 2026. No hosted deployment or sign-in system was added.

## What changed

1. **Statistical screening:** Poisson tests and crashes/mile/year use actual crash counts, with at least three events. Severity-weighted scores are separate ranking values. The baseline excludes the tested segment and falls back from road class to municipality only when exposure exists. Fragments shorter than 0.01 mile do not enter screening or its baseline. Corridors require matching route identity/name and endpoints within one meter, not merely a shared street name.
2. **Reports and source semantics:** NJDOT person counts replace crash-count proxies for fatalities/injuries. Unspecified injury severity and unavailable bicycle measures remain unknown. PDF and crash/HIN CSV exports work, escape untrusted text appropriately, and reject stale results. Reports explicitly describe exploratory screening, loaded-record limitations, configured weights and snap distance.
3. **Data quality and freshness:** Official CDC/ATSDR 2020 nationally ranked SVI is loaded; unavailable ranks remain null. Analyses record revisions for crashes, roads, boundaries and SVI plus a method/configuration fingerprint. Statement triggers detect source-content changes, including same-count edits, while derived crash snapping does not invalidate results. Global invalidation is deliberately conservative. Revisions detect changes; they are not immutable snapshots from which every old analysis can be reconstructed.
4. **Anonymous safeguards:** No accounts, login, ownership tokens or browser-held backend secrets. Recent history and Forget operate in browser storage. Shared list/delete endpoints are disabled. Computation and stored results remain on the backend; anyone with a result URL can read it. Forget does not erase server data, and this is not private document storage.

The single Postgres-backed worker owns execution through a session advisory lock on the same physical connection used for analysis writes. Recovery and bounded retries handle interrupted work. A subprocess watchdog limits whole jobs, including Python computation, to 900 seconds by default. SQL/lock waits have shorter deadlines. Queue capacity and per-peer active-job limits bound backlog. Shared database counters rate-limit creation, exports and reads, with global limits checked first. Request bodies and body-read time are bounded; failures return safe public messages. Counter expiry is periodically cleaned up.

The schema initializer records additive schema revisions, acquires its lock before DDL, and refuses to upgrade while a worker owns execution. Stop workers before initialization. This is a tracked additive upgrade mechanism, not a complete Alembic migration/rollback system.

The design follows [OWASP REST guidance](https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html), [OWASP denial-of-service guidance](https://cheatsheetseries.owasp.org/cheatsheets/Denial_of_Service_Cheat_Sheet.html), the [FastAPI background-work caveat](https://fastapi.tiangolo.com/tutorial/background-tasks/), and PostgreSQL's [queue-locking guidance](https://www.postgresql.org/docs/current/sql-select.html). Rate limiting reduces anonymous abuse; it does not provide authentication, confidentiality or protection against all distributed attacks.

## Preserved data and enrichment

- Existing crashes: **1,209,451**, with the same number of distinct external IDs. No crash rows were inserted by casualty enrichment.
- Source archives: 126 county/year archives for 2017–2022; 1,509,938 source records inspected, 1,209,451 existing records enriched, 300,483 source IDs absent from the loaded usable-location dataset, four schema errors. See `data/processed/casualty-enrichment-report.json`.
- Loaded person totals: 3,562 killed; 382,058 injured; 1,004 pedestrians killed; 15,924 pedestrians injured. These totals describe loaded records, not all statewide crashes.
- Severity: 930,017 property-damage, 3,363 fatal and 276,071 injury-unspecified records. This source cannot supply serious/minor injury or bicycle classifications; bicycle involvement is null, not false.
- SVI: 2,175 valid tracts, 2,165 ranked and ten unknown, intersecting all 564 municipalities. See `data/processed/svi-2020-report.json` and [official CDC source documentation](https://www.atsdr.cdc.gov/place-health/php/svi/svi-data-documentation-download.html).
- Preserved road network: 460,639 segments. Current road geometry still introduces uncertainty when locating historical route/milepost crashes.

## Local backup/restore rehearsal

Before these data changes, a custom-format backup was created at `data/backups/nj_hin_before_correctness.dump` (147,175,899 bytes). Restoration with `pg_restore --exit-on-error` into a separate database, `nj_hin_restore_check`, succeeded. Original and restored tables contained identical crash/road counts and no invalid or non-EPSG:4326 geometries. After enrichment, the crash ID/geometry checksum remained identical to the restored pre-change copy: 1,209,451 rows and summed MD5-prefix checksum `697078415827928642610468`. This checksum is a consistency check, not a cryptographic archive signature.

The rehearsal used this command pattern (substitute explicit, verified connection details and a **new** empty restore database):

```bash
pg_dump --format=custom --file=nj_hin_before_correctness.dump --dbname=SOURCE_DATABASE_URL
createdb --maintenance-db=ADMIN_DATABASE_URL nj_hin_restore_check
pg_restore --exit-on-error --dbname=RESTORE_DATABASE_URL nj_hin_before_correctness.dump
```

Do not restore over the live database. Use secure credential handling rather than committing connection strings. See [PostgreSQL backup documentation](https://www.postgresql.org/docs/current/app-pgdump.html). The local backup is not an encrypted off-host backup schedule, and restoring rows does not by itself prove a complete hosted-service recovery.

## Live representative result

Fresh local Avalon analysis **7**, 2017–2021, reached `completed` automatically in the UI and reports `data_status: ready`: **133 crash points, nine HIN segments, 0.878 HIN miles, zero fatalities and 41 injured people**. It records `count-poisson-v2`, SVI 2020, one reported location and 132 route/milepost estimates. The earlier 27-segment result came from the incorrect weighted-count method and is stale. A smaller HIN is the expected consequence of correcting the test, not lost crash data.

All 133 crash geometries and nine selected HIN geometries match PostGIS exactly. The browser renders 142 corresponding paths; refresh works. Both CSV exports have the expected row counts and preserve unknown bicycle fields as blanks. The five-page PDF was downloaded, text-checked and visually rendered: 41 injuries, pedestrian killed/injured 0/5, unknown bicycle casualties, and corrected method wording. Browser Forget removes local history without deleting the server result. Old analysis 5 visibly reports “Results out of date.” Larger local Newark analysis **8**, 2022, also completed: 12,408 crashes and 535 HIN segments, rendered as 12,943 map paths. Shared listing/deletion returned 405, stale crash/PDF endpoints returned 409, and unsupported bicycle HIN returned 422 rather than a fabricated empty result.

## Regression and independent review

- Backend: **177 tests passed**, with both `TEST_DATABASE_URL` and `DATA_TEST_DATABASE_URL` pointed at disposable local PostGIS; no skipped tests. Ninety dependency/datetime deprecation warnings remain.
- Frontend: **18 tests passed** and `CI=true` production build passed under Node 22.23.2/npm 10.9.9. Regression fixtures include object-shaped input versions, missing data and browser-local history.
- Final API Docker image build passed. `pip-audit -r backend/requirements.txt` reported **no known vulnerabilities** after updating the API framework dependencies; this is a point-in-time dependency check, not a security guarantee.
- Independent safeguard review passed with 31 focused tests covering concurrent admission, shared limits, DDL serialization, lock ownership, watchdog termination and interrupted-job recovery. Correctness review checked count-based screening, short-fragment exclusion, corridor connectivity, nullable source counts, exports and revision invalidation.
- Legacy municipality statistics endpoints now compute loaded-record totals and disclose selected/available/missing years rather than returning placeholder zeros or silently using a rolling period beyond the loaded data.

## Limits and public-launch work

This remains exploratory screening: there is no exposure-adjusted traffic risk model, multiple-testing correction, overdispersion correction or grant certification. Loaded-year availability is not source completeness. Unlocatable source records, detailed injury/bicycle data and time-matched historical road geometry still need better sources. SVI is an ecological tract measure, not individual vulnerability.

Configure and verify HTTPS ingress, trusted proxy networks, edge rate/connection limits, restricted database networking, a non-owner runtime role, worker restart/queue-age alerts, and encrypted off-host backup retention/restoration before public launch. `/health` checks database connectivity, not worker progress, data coverage or scientific validity. Deployment settings and limits are documented in [DEPLOY.md](DEPLOY.md).
