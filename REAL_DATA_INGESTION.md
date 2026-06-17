# Real NJ Data Ingestion Guide

**Status**: ✅ **READY TO USE**

---

## Overview

This guide explains how to ingest **real New Jersey crash and road data** instead of using sample data. All scripts pull from official government sources.

### Data Sources

1. **Crash Data**: [NJ Open Data Portal](https://data.nj.gov/Transportation/Total-NJ-Crash-Records-By-Year/86dt-kggc) (Socrata API)
2. **Road Network**: [OpenStreetMap via Geofabrik](https://download.geofabrik.de/north-america/us/new-jersey.html)
3. **Municipalities**: [NJ Open Data Portal](https://data.nj.gov/) (Socrata API)

---

## Quick Start

### Prerequisites

```bash
# 1. PostgreSQL with PostGIS running
docker-compose up -d postgres

# 2. Python dependencies installed
cd backend
pip install -r requirements.txt

# 3. (Optional) Get Socrata API token for higher rate limits
# Sign up at: https://data.nj.gov/profile/app_tokens
export SOCRATA_API_TOKEN="your_token_here"

# 4. For road network: Install GDAL
# Ubuntu/Debian:
sudo apt-get install gdal-bin

# MacOS:
brew install gdal
```

### Ingest All Data (One Command)

```bash
cd backend/scripts

# Example 1: Ingest all data for Mercer County (2017-2021)
python ingest_all_real_data.py \
  --county "Mercer" \
  --start-year 2017 \
  --end-year 2021

# Example 2: Ingest just Princeton data
python ingest_all_real_data.py \
  --municipality "Princeton" \
  --county "Mercer" \
  --start-year 2017 \
  --end-year 2021

# Example 3: Ingest limited crash data (testing)
python ingest_all_real_data.py \
  --county "Mercer" \
  --max-crashes 1000 \
  --skip-roads
```

---

## Individual Data Ingestion

### 1. Municipalities

Ingests all 565 NJ municipalities with boundaries.

```bash
cd backend/scripts

# Basic usage
python ingest_municipalities.py

# With API token
python ingest_municipalities.py --api-token YOUR_TOKEN
```

**Data Source**: https://data.nj.gov/resource/k9xb-zgh4.json

**What it does**:
- Fetches municipality names, codes, counties
- Downloads boundary geometries
- Loads into `municipalities` table

**Time**: ~30 seconds

---

### 2. Crash Data

Ingests real NJ crash records from NJTR-1 forms.

```bash
cd backend/scripts

# Basic usage (Mercer County, 2017-2021)
python ingest_nj_crash_data.py \
  --start-year 2017 \
  --end-year 2021 \
  --county "Mercer"

# Specific municipality
python ingest_nj_crash_data.py \
  --municipality "Princeton" \
  --county "Mercer" \
  --start-year 2017 \
  --end-year 2021

# Limit records for testing
python ingest_nj_crash_data.py \
  --county "Mercer" \
  --max-records 5000

# With API token (recommended)
python ingest_nj_crash_data.py \
  --county "Mercer" \
  --api-token YOUR_TOKEN
```

**Data Source**: https://data.nj.gov/resource/86dt-kggc.json

**What it does**:
- Fetches crash records with date, location, severity
- Filters by county/municipality and year range
- Validates coordinates (must be within NJ bounds)
- Maps NJ severity codes to our schema:
  - Fatal → `fatal`
  - Incapacitating Injury → `serious_injury`
  - Moderate Injury / Complaint of Pain → `minor_injury`
  - Property Damage Only → `property_damage`
- Loads into `crashes` table

**Time**:
- Small municipality (Princeton): ~2 minutes
- Medium county (Mercer): ~10 minutes
- Large county (Bergen): ~30 minutes
- Full state: ~2-4 hours

**Rate Limits**:
- **Without token**: 1,000 requests/hour (~50,000 crashes/hour)
- **With token**: Higher limits (depends on token tier)

---

### 3. Road Network

Ingests OpenStreetMap road network for New Jersey.

```bash
cd backend/scripts

# Basic usage (downloads ~50MB, processes ~30 min)
python ingest_osm_roads.py

# Custom segment length
python ingest_osm_roads.py --segment-length 0.05  # 0.05 mile segments

# Download only (no processing)
python ingest_osm_roads.py --download-only

# Specify data directory
python ingest_osm_roads.py --data-dir /path/to/data
```

**Data Source**: https://download.geofabrik.de/north-america/us/new-jersey-latest.osm.pbf

**What it does**:
1. Downloads NJ OSM extract (~50 MB)
2. Extracts roads using `ogr2ogr` (requires GDAL)
3. Filters to relevant road types:
   - Motorway, Trunk, Primary → `arterial`
   - Secondary, Tertiary → `collector`
   - Residential, Unclassified → `local`
4. Segments roads into 0.1 mile (default) units
5. Loads into `road_segments` table

**Time**:
- Download: ~1-2 minutes
- Extract roads: ~5 minutes
- Segment: ~15-20 minutes
- Load to DB: ~10 minutes
- **Total**: ~30-40 minutes

**Disk Space**: ~500 MB (OSM PBF + GeoJSON + database)

**Requirements**:
- `ogr2ogr` (GDAL) must be installed
- ~2 GB RAM for processing

---

## API Token Setup

### Why Use an API Token?

**Without token**:
- 1,000 API requests per hour
- ~50,000 crash records per hour
- Fine for small municipalities

**With token** (free):
- Higher rate limits
- Better for counties or multiple municipalities
- Recommended for production use

### How to Get a Token

1. Go to https://data.nj.gov/
2. Click "Sign Up" (top right)
3. Create free account
4. Go to Profile → App Tokens
5. Click "Create New App Token"
6. Copy token

### How to Use

**Option 1: Environment Variable (Recommended)**
```bash
export SOCRATA_API_TOKEN="your_token_here"
python ingest_all_real_data.py --county Mercer
```

**Option 2: Command-Line Argument**
```bash
python ingest_all_real_data.py \
  --county Mercer \
  --api-token "your_token_here"
```

**Option 3: .env File**
```bash
# Create .env file in backend/
echo "SOCRATA_API_TOKEN=your_token_here" >> backend/.env

# Scripts will auto-load from .env
python ingest_all_real_data.py --county Mercer
```

---

## Ingestion Strategies

### Strategy 1: Single Municipality (Fastest)

**Use case**: Testing, small towns, quick setup

```bash
python ingest_all_real_data.py \
  --municipality "Princeton" \
  --county "Mercer" \
  --start-year 2017 \
  --end-year 2021
```

**Time**: ~5 minutes
**Crashes**: ~500-2,000 depending on municipality

---

### Strategy 2: County (Medium)

**Use case**: Regional analysis, grant applications

```bash
python ingest_all_real_data.py \
  --county "Mercer" \
  --start-year 2017 \
  --end-year 2021
```

**Time**: ~15-30 minutes
**Crashes**: ~10,000-50,000 depending on county

**Recommended counties**:
- Mercer (Princeton, Trenton)
- Middlesex (New Brunswick)
- Hudson (Jersey City)
- Essex (Newark)
- Camden (Camden)

---

### Strategy 3: Full State (Slow)

**Use case**: Statewide analysis, research

```bash
python ingest_all_real_data.py \
  --start-year 2017 \
  --end-year 2021
```

**Time**: ~4-8 hours
**Crashes**: ~1.5-2 million (NJ averages ~300K crashes/year)
**Disk**: ~5-10 GB

**Note**: Requires API token for reasonable performance

---

### Strategy 4: Skip Roads (Fast Testing)

**Use case**: Quick testing without GDAL

```bash
python ingest_all_real_data.py \
  --county "Mercer" \
  --skip-roads
```

**Time**: ~5 minutes
**Note**: Analysis will fail without road segments. Only for testing municipality/crash ingestion.

---

## Troubleshooting

### Error: "No API token - rate limits will apply"

**Solution**: Get a free API token (see above) or accept lower rate limits.

### Error: "ogr2ogr not found"

**Solution**: Install GDAL
```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install gdal-bin python3-gdal

# MacOS
brew install gdal

# Verify
ogr2ogr --version
```

### Error: "Invalid coordinates for crash X"

**Cause**: Some crash records have incorrect lat/lon

**Impact**: Those crashes are skipped (typically < 1%)

**Solution**: None needed - this is expected

### Error: "Connection refused" or "Network error"

**Causes**:
- API is temporarily down
- Network connectivity issues
- Rate limit exceeded

**Solutions**:
1. Wait a few minutes and retry
2. Get API token for higher limits
3. Reduce `--max-records` to fetch smaller batches

### Error: "Database connection failed"

**Solution**: Ensure PostgreSQL is running
```bash
# Check if running
docker ps | grep postgres

# Start if not running
cd backend
docker-compose up -d postgres

# Wait 10 seconds for startup
sleep 10

# Try again
python ingest_all_real_data.py --county Mercer
```

---

## Verification

After ingestion, verify data loaded correctly:

```bash
cd backend

# Connect to database
psql postgresql://hin_user:hin_password@localhost:5432/hin_db

# Check record counts
SELECT COUNT(*) FROM municipalities;  -- Should be 565 for full NJ
SELECT COUNT(*) FROM crashes WHERE crash_date BETWEEN '2017-01-01' AND '2021-12-31';
SELECT COUNT(*) FROM road_segments;

# Check specific municipality
SELECT m.name, m.county, COUNT(c.crash_id) as crash_count
FROM municipalities m
LEFT JOIN crashes c ON ST_Contains(m.geom, c.geom)
WHERE m.name = 'Princeton'
GROUP BY m.name, m.county;

# Exit
\q
```

---

## Data Quality

### Crash Data Quality

**Good** (2017-present):
- Reliable geocoding
- ~98% of crashes have valid lat/lon
- Complete severity data

**Moderate** (2010-2016):
- Geocoding less accurate
- Some missing coordinates
- Still usable

**Poor** (pre-2010):
- Limited geocoding
- Many missing coordinates
- Not recommended

**Recommendation**: Use 2017+ for best results

### Road Network Quality

OpenStreetMap coverage in NJ:
- **Excellent**: Urban areas (Newark, Jersey City, etc.)
- **Good**: Suburban areas
- **Moderate**: Rural areas

Missing:
- Some private roads
- Recent developments (< 6 months)
- Unmapped local streets

**Recommendation**: OSM is sufficient for HIN analysis (focuses on arterials/collectors anyway)

---

## Performance Optimization

### Use Geographic Filters

Instead of:
```bash
# Slow: Downloads entire state
python ingest_nj_crash_data.py --start-year 2017 --end-year 2021
```

Do this:
```bash
# Fast: Downloads only county
python ingest_nj_crash_data.py \
  --county "Mercer" \
  --start-year 2017 \
  --end-year 2021
```

### Use API Token

10x faster ingestion with token.

### Limit Records for Testing

```bash
python ingest_nj_crash_data.py \
  --county "Mercer" \
  --max-records 1000
```

### Run in Background

```bash
# Run ingestion in background (Linux/Mac)
nohup python ingest_all_real_data.py --county Mercer &> ingestion.log &

# Monitor progress
tail -f ingestion.log
```

---

## Expected Results

### Princeton, NJ (2017-2021)

```
Municipalities: 1
Crashes: ~1,500
  - Fatal: ~5
  - Serious Injury: ~75
  - Minor Injury: ~375
  - Property Damage: ~1,045
Road Segments: ~1,500
```

### Mercer County, NJ (2017-2021)

```
Municipalities: 12
Crashes: ~25,000
  - Fatal: ~100
  - Serious Injury: ~1,250
  - Minor Injury: ~6,250
  - Property Damage: ~17,400
Road Segments: ~15,000
```

### Full NJ (2017-2021)

```
Municipalities: 565
Crashes: ~1,600,000
  - Fatal: ~3,000
  - Serious Injury: ~80,000
  - Minor Injury: ~400,000
  - Property Damage: ~1,117,000
Road Segments: ~350,000
```

---

## Next Steps After Ingestion

1. **Verify Data**:
   ```bash
   psql postgresql://hin_user:hin_password@localhost:5432/hin_db
   SELECT COUNT(*) FROM crashes;
   ```

2. **Start Backend**:
   ```bash
   cd backend
   uvicorn app.main:app --reload
   ```

3. **Access API Docs**: http://localhost:8000/docs

4. **Create Analysis**:
   - Via UI: http://localhost:3000
   - Via API: POST to `/api/analysis`

5. **View Results**:
   - Map visualization in frontend
   - Export GeoJSON/Shapefile
   - Generate PDF report (coming soon)

---

## Comparison: Sample vs Real Data

| Aspect | Sample Data | Real Data |
|--------|-------------|-----------|
| **Source** | Generated | NJ Open Data Portal + OSM |
| **Authenticity** | Fake | Real crash records |
| **Coverage** | 5 municipalities | All 565 NJ municipalities |
| **Time Period** | 2017-2021 | 2001-present (recommend 2017+) |
| **Setup Time** | < 1 minute | 5-40 minutes |
| **Disk Space** | < 1 MB | 100 MB - 10 GB |
| **Dependencies** | None | API token (optional), GDAL |
| **Use Case** | Development, testing | Production, grant applications |

---

## Costs

**All data sources are FREE**:
- ✅ NJ Open Data Portal: Free
- ✅ OpenStreetMap: Free
- ✅ Socrata API Token: Free
- ✅ Census Data: Free

**Only costs**:
- Your time (~5-40 minutes)
- Disk space (~100 MB - 10 GB)
- Compute (minimal)

---

## Sources

- [Total NJ Crash Records By Year](https://data.nj.gov/Transportation/Total-NJ-Crash-Records-By-Year/86dt-kggc)
- [Municipalities of New Jersey | Socrata API Foundry](https://dev.socrata.com/foundry/data.nj.gov/k9xb-zgh4)
- [Crash Data, Crash Records, Reference/Links](https://www.nj.gov/transportation/refdata/accident/crash_data.shtm)
- [OpenStreetMap New Jersey](https://download.geofabrik.de/north-america/us/new-jersey.html)
- [Socrata Open Data API (SODA)](https://dev.socrata.com/)

---

**Ready to ingest real data!** 🚀

Choose your strategy above and run the appropriate command.
