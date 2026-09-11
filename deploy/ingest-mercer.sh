#!/usr/bin/env bash
# Runs inside the backend container (repo root = /srv).
# Loads the municipality table, the five Mercer County NJDOT crash files that
# ship in data/real/, and the OSM road network. Idempotent-ish: rerunning
# re-inserts crashes unless the loader dedupes on external_id (it does: UNIQUE).
#
#   hin ingest                      # everything, Mercer 2017-2021
#   hin ingest --skip-roads         # crashes only
set -euo pipefail
cd /srv

SKIP_ROADS=0
MUNI_FILTER="${MUNI_FILTER:-}"        # e.g. "WEST WINDSOR" to restrict crash rows
for a in "$@"; do case "$a" in --skip-roads) SKIP_ROADS=1;; esac; done

echo "== municipalities (Socrata) =="
python backend/scripts/ingest_municipalities.py ${SOCRATA_API_TOKEN:+--api-token "$SOCRATA_API_TOKEN"}

echo "== resolving Mercer County municipalities =="
# The NJDOT loader attaches rows to one muni_id per call, so iterate the county.
python - <<'PY' > /tmp/mercer_munis.tsv
import sys; sys.path.insert(0, "/srv")
from backend.app.models.database import SessionLocal
from backend.app.models.tables import Municipality
db = SessionLocal()
q = db.query(Municipality).filter(Municipality.county.ilike("mercer"))
for m in q:
    print(f"{m.muni_id}\t{m.name}")
PY
cat /tmp/mercer_munis.tsv

echo "== NJDOT crash files =="
while IFS=$'\t' read -r MID NAME; do
  [ -n "$MUNI_FILTER" ] && ! echo "$NAME" | grep -qi "$MUNI_FILTER" && continue
  for Y in 2017 2018 2019 2020 2021; do
    python backend/scripts/ingest_njdot_crashes.py "data/real/Mercer${Y}Accidents.zip" \
      --muni-id "$MID" --municipality "$NAME" --start-year "$Y" --end-year "$Y" || true
  done
done < /tmp/mercer_munis.tsv

if [ "$SKIP_ROADS" = 0 ]; then
  echo "== OSM roads (Geofabrik NJ extract; 10-30 min) =="
  cd backend && python scripts/ingest_osm_roads.py --data-dir /srv/data/raw --segment-length 0.1
fi
echo "== done =="
