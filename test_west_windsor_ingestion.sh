#!/bin/bash
# Quick test: Fetch just 100 West Windsor crashes (no roads)

echo "============================================================"
echo "West Windsor - Quick Data Test (100 crashes)"
echo "============================================================"
echo ""

cd /home/user/nj-hin-generator/backend/scripts

echo "Fetching sample West Windsor data..."
echo "- Municipalities: All 565"
echo "- Crashes: Limited to 100 for testing"
echo "- Roads: Skipped (use full script for roads)"
echo ""

python3 ingest_all_real_data.py \
  --municipality "West Windsor" \
  --county "Mercer" \
  --start-year 2017 \
  --end-year 2021 \
  --max-crashes 100 \
  --skip-roads

echo ""
echo "============================================================"
echo "✓ Test Complete!"
echo "============================================================"
echo ""
echo "Verify data loaded:"
echo "  psql postgresql://hin_user:hin_password@localhost:5432/hin_db"
echo "  SELECT COUNT(*) FROM crashes WHERE crash_date >= '2017-01-01';"
echo ""
