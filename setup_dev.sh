#!/usr/bin/env bash
# Reproducible local Docker setup using explicitly synthetic sample data.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-nj_hin}"

echo "Building the lean API image..."
docker compose build backend

echo "Starting PostGIS and initializing the schema..."
docker compose up -d db
docker compose run --rm schema-init

echo "Generating and loading the explicitly synthetic development seed..."
docker compose run --rm --no-deps backend python scripts/generate_sample_data.py
docker compose run --rm --no-deps backend python scripts/load_sample_data.py

echo "Starting the backend..."
docker compose up -d backend

api_ready=false
for _ in {1..30}; do
  if docker compose exec -T backend python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).read()" >/dev/null 2>&1; then
    api_ready=true
    break
  fi
  sleep 2
done
if [[ "$api_ready" != true ]]; then
  docker compose logs backend >&2
  echo "Backend health check did not pass within 60 seconds." >&2
  exit 1
fi

echo "Verifying database row counts..."
municipality_count="$(docker compose exec -T db psql -U hin_user -d nj_hin_db -Atc 'SELECT COUNT(*) FROM municipalities;')"
road_count="$(docker compose exec -T db psql -U hin_user -d nj_hin_db -Atc 'SELECT COUNT(*) FROM road_segments;')"
crash_count="$(docker compose exec -T db psql -U hin_user -d nj_hin_db -Atc 'SELECT COUNT(*) FROM crashes;')"
tract_count="$(docker compose exec -T db psql -U hin_user -d nj_hin_db -Atc 'SELECT COUNT(*) FROM census_tracts;')"

if (( municipality_count < 5 || road_count < 250 || crash_count < 3500 || tract_count < 25 )); then
  echo "Synthetic seed verification failed: municipalities=$municipality_count roads=$road_count crashes=$crash_count tracts=$tract_count" >&2
  exit 1
fi

echo "Synthetic seed ready: municipalities=$municipality_count roads=$road_count crashes=$crash_count tracts=$tract_count"
echo "API: http://localhost:8000 | OpenAPI: http://localhost:8000/docs"
echo "Development frontend (optional): docker compose --profile dev up -d frontend"
