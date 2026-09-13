#!/usr/bin/env bash
# Foreground acquisition + atomic import. A failed job retries on a later timer tick.
set -euo pipefail
cd /opt/nj-hin
: "${COMPOSE:?COMPOSE must be exported by deploy/autoupdate.sh}"

echo "== $(date -Is) acquiring and validating public NJDOT dashboard crashes through 2025"
$COMPOSE run --rm -T --no-deps api \
  python -u scripts/download_njdot_dashboard.py \
    --start-year 2019 --end-year 2025 \
    --cache-dir /srv/data/raw/njdot-dashboard \
    --report /srv/data/processed/njdot-dashboard-download-report.json

# Pass manifest values as structured subprocess arguments, never shell text.
$COMPOSE run --rm -T --no-deps api python - <<'PY'
import json
import subprocess
import sys
from pathlib import Path

from scripts.download_njdot_dashboard import PORTAL_URL, file_hash

manifest = json.loads(Path('/srv/data/processed/njdot-dashboard-download-report.json').read_text())
if manifest.get('status') != 'succeeded' or (manifest.get('start_year'), manifest.get('end_year')) != (2019, 2025):
    raise SystemExit('Dashboard acquisition manifest is not a validated 2019-2025 snapshot')
csv_path = Path(manifest['csv_path']).resolve()
if not csv_path.is_relative_to(Path('/srv/data/raw/njdot-dashboard').resolve()):
    raise SystemExit('Dashboard CSV is outside the expected source cache')
if file_hash(csv_path) != manifest['csv']['sha256']:
    raise SystemExit('Dashboard CSV checksum changed after validation')
command = [
    sys.executable, '-u', 'scripts/ingest_njdot_dashboard.py',
    '--input-csv', str(csv_path),
    '--report', '/srv/data/processed/njdot-dashboard-import-report.json',
    '--stage', '/srv/data/processed/njdot-dashboard-import-stage.sqlite',
    '--source-url', PORTAL_URL,
    '--expected-count', str(manifest['csv']['rows']),
    '--apply', '--allow-new-historical',
]
retrieved_at = manifest.get('source', {}).get('retrieved_at')
if isinstance(retrieved_at, str) and retrieved_at.strip():
    command.extend(['--retrieved-at', retrieved_at])
subprocess.run(command, check=True)
PY
echo "== $(date -Is) dashboard import through 2025 succeeded"
