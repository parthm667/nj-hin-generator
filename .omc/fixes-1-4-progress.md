# HIN correctness and anonymous-use safeguards

User scope: complete findings 1–4, with NO accounts or sign-in. Browser-local interaction/history; analysis computation remains server-side. No deployment or unrelated cleanup.

## Accepted approach
- Count-based Poisson screening, severity ranking separate, explicit short-fragment exclusion, connected same-route corridors. Screening remains exploratory, not certified grant methodology.
- Preserve casualty counts from NJDOT; represent unsupported bicycle and injury detail as unknown. Accurate PDF/CSV, source and limitation disclosures.
- Official SVI ingestion, durable dataset/method versions and stale-result guards. Do not fabricate missing geolocation/detail records.
- No authentication subsystem. Disable shared listing/deletion; browser-local recent history. Anonymous requests bounded by shared rate limits and queue capacity. Separate Postgres-backed worker with exclusive execution/recovery, bounded attempts/timeouts, safe public errors. Backup/restore rehearsal locally; hosted scheduling remains deployment work.

## Work and verification
- [x] Statistical method and topology regression tests + repair.
- [x] Casualty/unknown semantics, SVI, versions, ingestion tests and local backfill.
- [x] Accurate report/export and frontend disclosure/local-history tests.
- [x] Anonymous safeguards and durable worker tests including restart/retry.
- [x] Non-destructive schema upgrade and backup/restore rehearsal.
- [x] Full backend/frontend tests, builds, representative live analysis, independent review.

Evidence: 177 backend tests, 18 frontend tests, CI build and final Docker build passed. Independent security review: 31 focused tests passed. Real analyses 7 (Avalon 2017–2021: 133 crashes/9 HIN) and 8 (Newark 2022: 12,408/535) complete and render. Exact Avalon API/PostGIS geometry comparison passes; five-page PDF visually checked, CSV unknown fields verified. Backup/restore preserves crash identity/geometry checksum; casualty-only enrichment preserves all 1,209,451 records. API dependency audit: no known vulnerabilities. Details: docs/CORRECTNESS_AND_SAFEGUARDS.md. No auth, cloud deployment, commit or push. Hosted edge controls, monitoring and scheduled off-host backups remain deployment work.

Primary guidance checked: OWASP REST Security/Authorization/DoS cheat sheets; FastAPI background-task caveat; PostgreSQL SELECT SKIP LOCKED and pg_dump/pg_restore documentation. No credentials in frontend, no authentication requirement, no external deployment.
