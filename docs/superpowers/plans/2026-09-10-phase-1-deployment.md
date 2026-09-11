# Phase 1 Deployment Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development to implement the scoped tasks and collect a separate review.

**Goal:** Make the React frontend build for Vercel and the FastAPI container boot with explicit PostGIS initialization, working map geometry, and a reproducible sample-data verification path.

**Architecture:** Use `app.*` imports with `backend/` as the Python project root. Initialize schema with a one-off command, query PostGIS for GeoJSON, and connect the static frontend directly to the backend through configured CORS.

**Tech Stack:** React 18 / CRA 5, Node 22, FastAPI, SQLAlchemy, PostGIS, Docker.

**Spec:** User-provided audit of commit `6ccca7f`, Phase 1 items 1–6 and A1–A8. Later-phase statewide ingestion and analysis-methodology corrections remain outside this change.

## Global Constraints

- Keep changes on `fix/phase-1-deployment`; preserve the user's working directory.
- Do not deploy, provision paid resources, push, or commit changes.
- Use `app.*` consistently; configured imports must not open a database connection.
- Require `DATABASE_URL`, default `DEBUG=false`, support `postgres://` normalization.
- Use explicit production CORS origins and optional project-scoped preview regex.
- Retain API geometry properties and supply valid coordinates through SQL `ST_AsGeoJSON`.
- Keep GDAL and ingestion packages out of the API runtime dependency list.
- Use Node 22 for Vercel and CI; commit-ready npm lockfile and `CI=true` build.
- Sample data must be clearly labeled synthetic; no claim that statewide real data is ready.

## Tasks

### Task 1: Backend runtime and geometry

Files: `backend/app/**`, `backend/tests/**`.

- [x] Normalize imports and make configuration fail clearly for missing URLs.
- [x] Remove startup DDL and make `/health` return 503 on database failure.
- [x] Configure production/preview CORS without wildcard credential origins.
- [x] Serialize crash points and HIN lines from PostGIS projections.
- [x] Exercise import, health, CORS, and real PostGIS geometry with regression tests.

Interfaces: retain `init_db()`, `init_postgis()`, `get_db()`, and API response properties. Scripts own when schema initialization runs.

### Task 2: Frontend build and map

Files: `frontend/**`.

- [x] Fix Query v5 polling and render fetched HIN lines with coordinate conversion.
- [x] Remove build warnings, duplicate Leaflet CSS, and hardcoded documentation links.
- [x] Use trailing slashes for collection endpoints; show map fetch/analysis failures.
- [x] Add Vercel SPA configuration, Node version, and reproducible lockfile.
- [x] Verify polling/render regressions and a production build under `CI=true`.

Interfaces: `REACT_APP_API_URL=https://<backend>/api`; backend GeoJSON coordinates use longitude then latitude.

### Task 3: Scripts, dependencies, and container

Files: `backend/scripts/**`, backend dependency/configuration files, Compose and setup scripts.

- [x] Normalize script imports and add `python scripts/init_schema.py`.
- [x] Repair sample MultiPolygon loading and deterministic data paths.
- [x] Split API, ingestion, and development requirements.
- [x] Remove GDAL build tooling from API image; respect `PORT` and proxy headers.
- [x] Make Compose run schema initialization before the backend and label frontend container as development only.
- [x] Verify scripts, dependency installation, container build and startup where Docker is available.

### Task 4: Deployment guidance, CI, and independent review

Files: `README.md`, `docs/DEPLOY.md`, existing deployment/setup guides, `.github/workflows/ci.yml`.

- [x] Document database → schema → ingestion → backend → Vercel deployment sequence and settings.
- [x] Point earlier deployment guides to current instructions and fix stale start commands.
- [x] Run actual backend tests against CI PostGIS and frontend tests/build on Node 22.
- [x] Collect independent review; resolve findings and record verification limits.

## Verification commands

```bash
cd backend
python -m pip install -r requirements-dev.txt
DATABASE_URL=postgresql://user:password@localhost:5432/nj_hin_db python -c "from app.main import app"
python -m pytest tests/ -q
python scripts/init_schema.py
cd ../frontend
npm ci
CI=true npm test -- --watchAll=false --runInBand
CI=true npm run build
cd ..
docker build -t nj-hin-api:phase1 backend/
```

Database-backed tests use a disposable test database; production credentials are never required for local verification.

## Coordination and decisions

| Tasks | Shared interface | Resolution |
| --- | --- | --- |
| 1 / 2 | Crash and HIN FeatureCollections | Preserve API properties and standard GeoJSON ordering. |
| 1 / 3 | Database helpers and application imports | Keep helper names, move execution to one-off CLI. |
| 1 / 4 | Test database environment | Use a dedicated PostGIS database for integration tests. |
| 2 / 4 | Node runtime and npm lock | Pin both to Node 22. |
| 3 / 4 | Setup commands and dependencies | Documentation follows explicit schema init and split requirements. |

Parallel implementation is authorized by the supplied AGENTS instructions; file ownership is disjoint. A separate reviewer evaluates the combined result after authoring.

## Verification evidence (2026-09-10)

- Backend: 18 pytest tests passed against local PostGIS; two third-party deprecation warnings. `pip check` passed.
- Schema initializer succeeded. Synthetic seed: 5 municipalities, 250 roads, 3,500 crashes, 25 census tracts. A repeat load inserted zero duplicates.
- Princeton sample analysis completed: 500 crash points and 11 HIN lines with geometry coordinates.
- Docker build passed. Container honored `PORT=8080`, health returned 200, map endpoints returned 500/11 features, and PDF returned 200 (`application/pdf`, 7,415 bytes).
- Container import succeeds with an unreachable but configured database URL.
- Frontend: clean `npm ci` using Node 22.23.2/npm 10.9.9, two regression tests passed, and final `CI=true npm run build` compiled successfully. Built bundle: 130.78 kB JS and 9.76 kB CSS (gzip).
- Browser: a fresh West Windsor analysis automatically polled `running` to `completed`; 1,000 points and 27 HIN lines rendered with the expanded compatibility fixture. Reload retained all 1,027 paths; PDF downloaded through the UI.
- Screenshot verification caught Leaflet panes covering the side panel and crash markers covering HIN lines. Explicit stacking contexts and drawing HIN last fixed both. Independent scoped review approved the fixes.
- No remaining Critical/Important independent review findings. No cloud deployment, commit, or push performed.

## Local preview handoff

The development preview remains at `http://localhost:3000`, using only synthetic data. The API test container listens on `127.0.0.1:58001` and disposable PostGIS on `127.0.0.1:55432`. Containers: `nj-hin-phase1-api`, `nj-hin-phase1-postgis`. Stop these with `docker stop nj-hin-phase1-api nj-hin-phase1-postgis` when finished; their data remains available. The temporary host API on port 58000 was stopped.

The unused `frontend/src/components/MapView.js` placeholder was removed; its original remains in Git history. Browser artifacts are ignored under `.playwright-mcp/`.
