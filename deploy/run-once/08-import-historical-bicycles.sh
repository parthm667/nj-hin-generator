#!/usr/bin/env bash
# Foreground job: autoupdate marks this done only after verified success.
# The importer waits for the analysis worker's existing ownership lock.
set -euo pipefail
cd /opt/nj-hin
: "${COMPOSE:?COMPOSE must be exported by deploy/autoupdate.sh}"

echo "== $(date -Is) importing historical bicycle involvement (2017-2022)"
$COMPOSE run --rm -T --no-deps api \
  python -u scripts/ingest_njdot_bicycles.py \
    --start-year 2017 --end-year 2022 \
    --cache-dir /srv/data/raw/bicycles \
    --report /srv/data/processed/bicycle-import-report.json
echo "== $(date -Is) historical bicycle import and verification succeeded"
