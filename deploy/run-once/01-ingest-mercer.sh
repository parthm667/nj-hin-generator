#!/usr/bin/env bash
# Executed once by deploy/autoupdate.sh (host side, $COMPOSE is exported).
# Waits for the API to be healthy, then loads municipalities, the Mercer
# County NJDOT crash files (2017-2021) and the OSM road network.
set -uo pipefail
cd /opt/nj-hin

echo "== $(date -Is) waiting for backend health"
for i in $(seq 1 60); do
  if $COMPOSE exec -T backend curl -sf http://localhost:8000/health >/dev/null 2>&1; then
    echo "backend healthy after ${i} checks"
    break
  fi
  [ "$i" = 60 ] && { echo "backend never became healthy"; exit 1; }
  sleep 10
done

echo "== $(date -Is) starting Mercer ingest"
$COMPOSE exec -T backend bash deploy/ingest-mercer.sh
rc=$?
echo "== $(date -Is) ingest finished rc=$rc"
exit $rc
