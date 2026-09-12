#!/usr/bin/env bash
# Caddy's upstream moved from backend:8000 to api:8000; the bind-mounted
# Caddyfile changed but the container was never told. Also (re)launch the
# statewide ingest if the detached launch from job 02 left no log behind.
set -uo pipefail
cd /opt/nj-hin
LOG=/var/log/nj-hin
$COMPOSE restart caddy
sleep 3
$COMPOSE exec -T caddy wget -qO- http://api:8000/health && echo && echo "caddy -> api OK"

if ! pgrep -f "ingest_all_real_data.py" >/dev/null && ! grep -q "Starting" "$LOG/ingest-statewide.log" 2>/dev/null; then
  echo "== $(date -Is) relaunching statewide ingest"
  nohup setsid bash -c 'cd /opt/nj-hin; docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env exec -T api python -u scripts/ingest_all_real_data.py --start-year 2017 --end-year 2022 --cache-dir /srv/data/raw --report /srv/data/processed/real-data-manifest.json; echo "== $(date -Is) statewide ingest finished rc=$?"' >> "$LOG/ingest-statewide.log" 2>&1 < /dev/null &
  disown
  sleep 20
  tail -5 "$LOG/ingest-statewide.log"
else
  echo "ingest already running or started"; tail -5 "$LOG/ingest-statewide.log"
fi
exit 0
