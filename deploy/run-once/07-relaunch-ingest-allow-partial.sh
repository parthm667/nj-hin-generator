#!/usr/bin/env bash
# Roads stage completed but 9/105977 NJDOT features are malformed, so the
# pipeline's coverage gate stops it (exit 3). Accept the documented partial
# coverage and run through to crashes, detached from the api container.
set -uo pipefail
cd /opt/nj-hin
LOG=/var/log/nj-hin
docker rm -f nj-hin-ingest >/dev/null 2>&1 || true
echo "== $(date -Is) starting nj-hin-ingest (allow-partial-roads)"
$COMPOSE run -d --no-deps --name nj-hin-ingest api \
  python -u scripts/ingest_all_real_data.py \
    --start-year 2017 --end-year 2022 --skip-municipalities --allow-partial-roads \
    --cache-dir /srv/data/raw --report /srv/data/processed/real-data-manifest.json
sleep 5
docker ps -a --filter name=nj-hin-ingest --format '{{.Names}} {{.Status}}'
nohup setsid sh -c "docker logs -f nj-hin-ingest >> $LOG/ingest-statewide.log 2>&1; docker inspect -f 'container exit code {{.State.ExitCode}}' nj-hin-ingest >> $LOG/ingest-statewide.log 2>&1" >/dev/null 2>&1 < /dev/null &
exit 0
