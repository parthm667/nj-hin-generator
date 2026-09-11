# Deployment

Production runs as three containers on one Oracle Cloud Always Free VM
(Ubuntu 24.04, VM.Standard.A1.Flex, 4 OCPU / 24 GB) and one Vercel project.

| Piece | Where | How |
|---|---|---|
| API (FastAPI + uvicorn) | `hin-api.mhaske.com` → Oracle VM | `deploy/docker-compose.prod.yml`, TLS by Caddy |
| PostgreSQL 16 + PostGIS | same VM, `pgdata` volume | password generated at first boot into `deploy/.env` |
| Frontend (CRA) | Vercel project `nj-hin`, root `frontend/` | served at `mhaske.com/nj-hin` via a rewrite in the main site's `vercel.json` |

`mhaske.com/nj-hin/api/*` is proxied by Vercel to `hin-api.mhaske.com/api/*`,
so the browser only ever talks to `mhaske.com` and CORS is moot.

## New VM

1. Create the instance with `deploy/cloud-init.yaml` pasted into
   *Advanced options → Management → Cloud-init script*.
2. VCN security list: allow ingress TCP 80 and 443 from `0.0.0.0/0`.
3. Point the `hin-api` A record at the public IP. Caddy fetches a certificate
   on the first request.
4. First boot takes ~10 minutes (aarch64 wheel builds). Watch with
   `tail -f /var/log/nj-hin-firstboot.log`.

## Operating

```
hin ps                 # container status
hin logs backend       # tail API logs
hin update             # git pull + rebuild + restart
hin psql               # psql into the database
hin ingest             # load municipalities, Mercer crashes, OSM roads
hin shell              # bash inside the backend container
```

## Backups

`hin run pg_dump ...` is not wired yet. Until it is:
`docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env exec db pg_dump -U hin -Fc nj_hin > nj_hin.dump`
