# Deploy on Vercel and Railway/Render

The local application includes source ingestion, count-based screening, corrected casualty reporting, and anonymous-use safeguards. See [DATA_PIPELINE.md](DATA_PIPELINE.md) for source limitations and [CORRECTNESS_AND_SAFEGUARDS.md](CORRECTNESS_AND_SAFEGUARDS.md) for the implementation/verification record. Rendering and internally consistent calculations do not certify the safety methodology.

## Database and schema

Use PostgreSQL with PostGIS. Obtain URLs reachable from your ingestion machine and API service, with the provider's required TLS options. Never put database credentials in public `REACT_APP_*` variables.

From `backend/`, with Python 3.11 or 3.12:

```bash
python -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
export DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/DATABASE?sslmode=require'
python scripts/init_schema.py
```

PowerShell: activate `.\venv\Scripts\Activate.ps1`, then set `$env:DATABASE_URL = 'postgresql://...'`.

The URL is required; `postgres://` is also accepted. API startup does not create tables. Stop the worker before schema upgrades. The initializer serializes all DDL, refuses a live worker, enables PostGIS, creates missing tables, and applies tracked additive upgrades without replacing datasets. It is an explicit revision ledger, not a general-purpose destructive migration engine. If your connection cannot create extensions, enable PostGIS through the provider's administrator interface. Use a separate privileged migration/ingestion role; runtime roles should not own schema objects.

## Load data explicitly

A new schema is empty. For a separate demo database, from `backend/`:

```bash
python scripts/generate_sample_data.py
python scripts/load_sample_data.py
```

These fixtures cover 2017–2021 and are synthetic. Keep them separate from production NJDOT data.

For the official real-data pipeline, install `requirements-ingest.txt` on the ingestion machine and follow [DATA_PIPELINE.md](DATA_PIPELINE.md). The orchestrator loads NJGIN boundaries, the measured NJDOT road network, then county/year crash archives. Start with explicitly verified available years; inspect rejected/unresolved counts in the reports. The legacy geospatial stack in `requirements-scripts.txt` is not needed for this pipeline. Run ingestion offline or as a one-off job, not at API startup.

## Backend service

Railway: service root `backend`, configuration file `/backend/railway.toml`. Its pre-deploy job runs the schema initializer. Render: Docker web service, root `backend`, Dockerfile `./Dockerfile`; run the initializer explicitly before release. Use the explicit initialization command before loading data on either platform.

Leave the service start override empty to use the image command, which honors `PORT` (default `8000`). If an override is required:

```bash
uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --proxy-headers --forwarded-allow-ips="$FORWARDED_ALLOW_IPS" --limit-concurrency 32
```

Set `FORWARDED_ALLOW_IPS` to only the verified ingress IPs/networks. The image defaults to `127.0.0.1`, not `*`. The limiter ignores raw forwarding headers, but Uvicorn can rewrite the peer first; trusting arbitrary forwarding would let callers evade IP quotas. Behind an unconfigured proxy, clients safely share its quota. See [FastAPI proxy guidance](https://fastapi.tiangolo.com/advanced/behind-a-proxy/).

| Backend variable | Value |
| --- | --- |
| `DATABASE_URL` | Backend-reachable Postgres URL with required TLS options |
| `DEBUG` | `false` |
| `APP_ENVIRONMENT` | `production` |
| `ANONYMOUS_RATE_LIMIT_SECRET` | Stable random server-only secret of at least 32 characters; never a `REACT_APP_*` variable |
| `CORS_ORIGINS` | Comma-separated frontend origins, e.g. `https://nj-hin.vercel.app,https://hin.example.org` |
| `CORS_ORIGIN_REGEX` | Optional anchored regex for this project's trusted preview origins |

Origins have no trailing slash or path. Do not use `*` with credentialed CORS. List exact previews or restrict a regex to domains you control. Example for project `nj-hin`, team `myteam`: `^https://nj-hin-[a-z0-9-]+-myteam\.vercel\.app$`. Check it against your actual preview hostname; do not authorize every `*.vercel.app` tenant.

Health check path: `/health`. It queries the database and returns HTTP 503 if unavailable. HTTP 200 verifies connectivity, not populated data or worker health. `/` reports process information. See [Railway config as code](https://docs.railway.com/config-as-code).

Run a separate worker service from the same image with `python -m app.worker`, the same `DATABASE_URL`, and an on-failure restart policy. Do not reuse the API health-check path on the worker. The Postgres queue permits 20 outstanding analyses total and two per anonymous peer by default. Creation is limited to five requests per ten minutes per peer, with shared global caps; exports and reads have separate limits. No accounts or sign-in exist. A supervisor bounds each child job to 900 seconds; SQL and lock waits have shorter limits. Retries stop at three attempts. Stop all worker services before running the pre-deploy schema initializer, then restart after upgrade.

Before public launch, configure HTTPS ingress, edge rate/connection limits, restricted database networking, and a non-owner runtime role. Configure alerts for growing/old pending jobs, failed workers, HTTP errors, and failed backups. These host-specific controls cannot be verified by a local test. Keep source caches/manifests alongside encrypted off-host database backups, set retention, and rehearse restoration. See the local restore evidence and commands in [CORRECTNESS_AND_SAFEGUARDS.md](CORRECTNESS_AND_SAFEGUARDS.md).

## Vercel frontend

| Setting | Value |
| --- | --- |
| Root Directory | `frontend` |
| Framework Preset | Create React App |
| Node.js | `22.x` |
| Install Command | `npm ci` |
| Build Command | `npm run build` |
| Output Directory | `build` |
| `REACT_APP_API_URL` | `https://YOUR-BACKEND-HOST/api` |

Set the API URL for Production and Preview, including `https://` and `/api`. CRA embeds it at build time; redeploy after changing it. `frontend/vercel.json` supplies the SPA fallback for refreshing `/analysis/123`. API requests go directly to the backend, whose CORS must allow the frontend.

Reference: [Vercel configuration](https://vercel.com/docs/project-configuration) and [supported Node versions](https://vercel.com/docs/functions/runtimes/node-js/node-js-versions).

## Smoke test

```bash
curl -f https://YOUR-BACKEND-HOST/health
curl -f https://YOUR-BACKEND-HOST/api/municipalities/
```

Select a municipality with roads and crashes and choose covered years. Inspect `/api/municipalities/ID/coverage`; a missing-year request must return 422 without creating an analysis. Create a valid analysis and confirm the worker reaches `completed` with `data_status: ready`. Check crash points, HIN lines, PDF, and crash/HIN CSV. Unknown measures must not display as zero. Old unversioned results must require a rerun. Shared listing and deletion return 405; Forget removes only browser-local history. Result URLs remain accessible to anyone with the URL, so this is not private storage. Empty HIN can be legitimate when no segment qualifies; use known qualifying fixtures to check rendering.

## Local checks

`docker compose --profile dev up --build` starts PostGIS, runs an initializer, and starts the API and development frontend. Omit `--profile dev` for just the API and database. Load fixtures explicitly using the commands above with a local database URL. The frontend container is for development; Vercel serves production builds.

```bash
cd backend
python -m pip install -r requirements-dev.txt
python -m pytest tests/ -q
cd ../frontend
npm ci
CI=true npm test -- --watchAll=false --runInBand
CI=true npm run build
cd ..
docker build -t nj-hin-api:phase1 backend/
```

PowerShell build: `$env:CI='true'; npm run build`. Integration tests use a disposable PostGIS database via `TEST_DATABASE_URL`; never use production credentials for tests.
