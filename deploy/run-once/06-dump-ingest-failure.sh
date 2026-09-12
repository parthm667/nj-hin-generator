#!/usr/bin/env bash
set -uo pipefail
cd /opt/nj-hin
echo "== docker logs nj-hin-ingest (tail 120)"; docker logs --tail 120 nj-hin-ingest 2>&1
echo "== manifest / reports in hindata volume"
$COMPOSE run --rm --no-deps -T api sh -c 'for f in /srv/data/processed/*.json; do echo "--- $f"; head -c 6000 "$f"; echo; done; echo; ls -la /srv/data/raw/roads | tail -3; ls /srv/data/raw/roads | wc -l'
exit 0
