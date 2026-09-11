#!/usr/bin/env bash
# Smoke-test the legacy real-data loader with a bounded record count.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-backend/venv}"
DATABASE_URL="${DATABASE_URL:-postgresql://hin_user:hin_password@localhost:5432/nj_hin_db}"
export DATABASE_URL

if [[ -x "$VENV_DIR/bin/python" ]]; then
  VENV_PYTHON="$VENV_DIR/bin/python"
elif [[ -x "$VENV_DIR/Scripts/python.exe" ]]; then
  VENV_PYTHON="$VENV_DIR/Scripts/python.exe"
else
  "$PYTHON_BIN" -m venv "$VENV_DIR"
  if [[ -x "$VENV_DIR/bin/python" ]]; then
    VENV_PYTHON="$VENV_DIR/bin/python"
  else
    VENV_PYTHON="$VENV_DIR/Scripts/python.exe"
  fi
fi
"$VENV_PYTHON" -m pip install -r backend/requirements-scripts.txt

docker compose up -d db
db_ready=false
for _ in {1..30}; do
  if docker compose exec -T db pg_isready -U hin_user -d nj_hin_db >/dev/null 2>&1; then
    db_ready=true
    break
  fi
  sleep 2
done
if [[ "$db_ready" != true ]]; then
  echo "PostGIS service 'db' did not become ready within 60 seconds." >&2
  exit 1
fi

"$VENV_PYTHON" backend/scripts/init_schema.py
"$VENV_PYTHON" backend/scripts/ingest_all_real_data.py \
  --municipality "West Windsor" \
  --county "Mercer" \
  --start-year 2017 \
  --end-year 2021 \
  --max-crashes 100 \
  --skip-roads

crash_count="$(docker compose exec -T db psql -U hin_user -d nj_hin_db -Atc "SELECT COUNT(*) FROM crashes WHERE external_id NOT LIKE 'SAMPLE-%' AND crash_date BETWEEN DATE '2017-01-01' AND DATE '2021-12-31';")"
if (( crash_count == 0 )); then
  echo "Real-data smoke test loaded zero crashes." >&2
  exit 1
fi

echo "Real-data smoke test loaded $crash_count crash rows."
