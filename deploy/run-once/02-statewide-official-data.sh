#!/usr/bin/env bash
# Executed once by deploy/autoupdate.sh (host side, $COMPOSE is exported).
#
# Moves the VM onto the phase-1 backend: makes sure deploy/.env has the values
# the new config needs, resets the database (the previous schema and its
# partial Mercer load are not compatible), applies the schema, then kicks off
# the official statewide pipeline (NJGIN boundaries, NJDOT roads, NJDOT crashes
# 2017-2022 for all 21 counties) detached, so deploys keep flowing while it runs.
set -uo pipefail
cd /opt/nj-hin
LOG=/var/log/nj-hin
ENV=deploy/.env

grep -q '^ANONYMOUS_RATE_LIMIT_SECRET=' $ENV || echo "ANONYMOUS_RATE_LIMIT_SECRET=$(openssl rand -hex 32)" >> $ENV
PGUSER=$(grep ^POSTGRES_USER $ENV | cut -d= -f2)
PGDB=$(grep ^POSTGRES_DB $ENV | cut -d= -f2)

echo "== $(date -Is) resetting database schema"
$COMPOSE up -d db
for i in $(seq 1 30); do $COMPOSE exec -T db pg_isready -U "$PGUSER" -d "$PGDB" >/dev/null 2>&1 && break; sleep 2; done
$COMPOSE stop api worker 2>/dev/null
$COMPOSE exec -T db psql -U "$PGUSER" -d "$PGDB" -v ON_ERROR_STOP=1 -c \
  "DROP SCHEMA public CASCADE; CREATE SCHEMA public; CREATE EXTENSION IF NOT EXISTS postgis;" || exit 1

echo "== $(date -Is) starting api + worker (api applies the schema on start)"
$COMPOSE up -d --remove-orphans api worker caddy || exit 1
for i in $(seq 1 60); do
  $COMPOSE exec -T api curl -sf http://localhost:8000/health >/dev/null 2>&1 && { echo "api healthy"; break; }
  [ "$i" = 60 ] && { echo "api never became healthy"; $COMPOSE logs --tail=50 api; exit 1; }
  sleep 5
done

echo "== $(date -Is) launching statewide ingest in the background (log: $LOG/ingest-statewide.log)"
setsid nohup bash -c "$COMPOSE exec -T api python scripts/ingest_all_real_data.py \
    --start-year 2017 --end-year 2022 \
    --cache-dir /srv/data/raw --report /srv/data/processed/real-data-manifest.json; \
  echo \"== \$(date -Is) statewide ingest finished rc=\$?\"" >> "$LOG/ingest-statewide.log" 2>&1 < /dev/null &
echo "launched pid $!"
exit 0
