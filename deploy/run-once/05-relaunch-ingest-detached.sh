#!/usr/bin/env bash
# Run the statewide ingest in its own one-off container (compose run), so the
# api container can be rebuilt/restarted by deploys without killing the job.
# Idempotent: skips if a container named nj-hin-ingest is already running.
set -uo pipefail
cd /opt/nj-hin
LOG=/var/log/nj-hin
if docker ps --format '{{.Names}}' | grep -qx nj-hin-ingest; then
  echo "nj-hin-ingest already running"; exit 0
fi
docker rm -f nj-hin-ingest >/dev/null 2>&1 || true
echo "== $(date -Is) starting nj-hin-ingest container"
$COMPOSE run -d --no-deps --name nj-hin-ingest api \
  python -u scripts/ingest_all_real_data.py \
    --start-year 2017 --end-year 2022 --skip-municipalities \
    --cache-dir /srv/data/raw --report /srv/data/processed/real-data-manifest.json
sleep 5
docker ps --filter name=nj-hin-ingest --format '{{.Names}} {{.Status}}'
# Stream its output into the public log (host process; survives compose ups).
nohup setsid sh -c "docker logs -f nj-hin-ingest >> $LOG/ingest-statewide.log 2>&1" >/dev/null 2>&1 < /dev/null &
echo "log follower pid $!"
exit 0
