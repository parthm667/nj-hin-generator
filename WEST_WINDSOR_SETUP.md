# West Windsor Township - Real Data Setup

**Client**: West Windsor Township, Mercer County, NJ
**Status**: Ready to ingest real data

---

## Quick Setup (3 Commands)

### Step 1: Start Database (if not running)

```bash
# Check if Docker is available
docker --version

# If Docker exists, use docker-compose
cd /home/user/nj-hin-generator
docker-compose up -d postgres

# Wait for database to start
sleep 10

# OR if using system PostgreSQL:
sudo systemctl start postgresql
sudo -u postgres psql -c "CREATE DATABASE hin_db;"
sudo -u postgres psql -c "CREATE USER hin_user WITH PASSWORD 'hin_password';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE hin_db TO hin_user;"
```

### Step 2: Run Database Migrations

```bash
cd /home/user/nj-hin-generator/backend

# Create tables
python -c "from app.models.database import init_db; init_db()"
```

### Step 3: Ingest West Windsor Data

```bash
cd /home/user/nj-hin-generator/backend/scripts

# Optional but recommended: Get free API token
# Visit: https://data.nj.gov/profile/app_tokens
export SOCRATA_API_TOKEN="your_token_here"

# Ingest West Windsor Township data (2017-2021)
python ingest_all_real_data.py \
  --municipality "West Windsor" \
  --county "Mercer" \
  --start-year 2017 \
  --end-year 2021
```

---

## What Gets Downloaded

### West Windsor Township Crash Data (2017-2021)

**Expected Results**:
- **Total Crashes**: ~2,000-2,500
- **Fatal**: ~5-10
- **Serious Injury**: ~100-150
- **Minor Injury**: ~500-625
- **Property Damage**: ~1,400-1,750

**Data Fields**:
- Crash date and time
- Exact location (lat/lon)
- Severity level
- Road name
- Pedestrian/bicycle involvement
- Injuries and fatalities

### West Windsor Road Network

**Expected Results**:
- **Road Segments**: ~2,000-3,000
- **Arterial Roads**: Route 1, Route 571, Clarksville Road, etc.
- **Collector Roads**: Windsor Road, Alexander Road, etc.
- **Local Streets**: Residential streets

**Segment Details**:
- 0.1 mile segments for analysis
- Road classification
- Road names from OSM
- Geometry for mapping

### Municipality Boundary

- West Windsor boundary polygon
- County: Mercer
- Municipal code
- Used for filtering crashes

---

## Time Estimates

- **Municipalities**: 30 seconds (all 565 NJ municipalities)
- **West Windsor Crashes**: 3-5 minutes
- **Road Network**: 20-30 minutes (entire NJ, one-time download)

**Total**: ~25-35 minutes

---

## Ingestion Progress

You'll see output like this:

```
============================================================
Real NJ Data Ingestion Pipeline
============================================================
Period: 2017 - 2021
Municipality: West Windsor
County: Mercer
API Token: Configured
============================================================

[STEP 1/3] Ingesting Municipalities...
------------------------------------------------------------
Fetching municipalities from NJ Open Data Portal...
Fetched 565 municipalities
Loading municipalities to database...
Loaded 100 municipalities...
Loaded 200 municipalities...
...
✓ Successfully loaded 565 municipalities
✓ Municipalities ingested successfully

[STEP 2/3] Ingesting Crash Data...
------------------------------------------------------------
Starting NJ crash data ingestion...
Period: 2017 - 2021
Municipality: West Windsor
County: Mercer
Fetching crashes from NJ Open Data Portal...
Fetched 2,234 crashes
Loading crashes to database...
Loaded 1000 crashes...
Loaded 2000 crashes...
✓ Successfully loaded 2,234 crashes
✓ Crash data ingested successfully

[STEP 3/3] Ingesting Road Network...
------------------------------------------------------------
Note: Road network ingestion requires ogr2ogr (GDAL)
This may take 10-30 minutes for full NJ dataset
Starting OSM road network ingestion...
Downloading NJ OSM data from Geofabrik...
URL: https://download.geofabrik.de/north-america/us/new-jersey-latest.osm.pbf
File size: 52.3 MB
Downloaded: 10.0%
Downloaded: 20.0%
...
✓ Downloaded to ./data/new-jersey-latest.osm.pbf
Extracting roads from OSM data (this may take a few minutes)...
✓ Extracted roads to ./data/nj_roads.geojson
Loading roads from ./data/nj_roads.geojson...
Loaded 45,823 roads
Segmenting roads into 0.1 mile segments...
Created 10000 segments...
Created 20000 segments...
...
✓ Created 287,456 road segments
Loading road segments to database...
Loaded 1000 segments...
Loaded 2000 segments...
...
✓ Successfully loaded 287,456 road segments
✓ Road network ingested successfully

============================================================
INGESTION COMPLETE
============================================================
Success: 3/3 steps completed
✓ All data ingested successfully!

Next steps:
  1. Start backend: uvicorn app.main:app --reload
  2. Start frontend: cd frontend && npm start
  3. Create analysis via UI or API
```

---

## Verify Data Loaded

After ingestion, verify the data:

```bash
# Connect to database
psql postgresql://hin_user:hin_password@localhost:5432/hin_db

# Check West Windsor data
SELECT
  m.name,
  m.county,
  COUNT(c.crash_id) as total_crashes,
  SUM(CASE WHEN c.severity = 'fatal' THEN 1 ELSE 0 END) as fatal,
  SUM(CASE WHEN c.severity = 'serious_injury' THEN 1 ELSE 0 END) as serious_injury,
  SUM(CASE WHEN c.severity = 'minor_injury' THEN 1 ELSE 0 END) as minor_injury
FROM municipalities m
LEFT JOIN crashes c ON ST_Contains(m.geom, c.geom)
WHERE m.name = 'West Windsor'
  AND c.crash_date BETWEEN '2017-01-01' AND '2021-12-31'
GROUP BY m.name, m.county;

# Expected output:
#     name      | county | total_crashes | fatal | serious_injury | minor_injury
# --------------+--------+---------------+-------+----------------+--------------
#  West Windsor | Mercer |          2234 |     7 |            112 |          559
```

---

## Troubleshooting

### If ogr2ogr is not installed:

```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install gdal-bin python3-gdal

# Verify
ogr2ogr --version
```

### If you get rate limit errors:

Get a free API token:
1. Visit: https://data.nj.gov/
2. Sign up (free)
3. Go to Profile → App Tokens
4. Create token
5. Use: `export SOCRATA_API_TOKEN="your_token"`

### If you want to skip road network (faster testing):

```bash
# Just get municipalities and crashes (5 minutes)
python ingest_all_real_data.py \
  --municipality "West Windsor" \
  --county "Mercer" \
  --skip-roads
```

**Note**: Analysis will fail without road segments. Only use `--skip-roads` for testing data ingestion.

---

## Next Steps After Ingestion

### 1. Start Backend

```bash
cd /home/user/nj-hin-generator/backend
uvicorn app.main:app --reload
```

### 2. Start Frontend

```bash
cd /home/user/nj-hin-generator/frontend
npm install  # First time only
npm start
```

### 3. Create West Windsor Analysis

**Via UI**: http://localhost:3000
- Select "West Windsor — Mercer County"
- Years: 2017 - 2021
- Click "Run Analysis"

**Via API**: http://localhost:8000/docs
- POST `/api/analysis`
- Body:
  ```json
  {
    "muni_id": <west_windsor_id>,
    "config": {
      "start_year": 2017,
      "end_year": 2021,
      "snap_distance_meters": 50,
      "segment_length_miles": 0.1,
      "significance_threshold": 0.05
    }
  }
  ```

### 4. View Results

- Map with crash points colored by severity
- High Injury Network corridors highlighted
- Statistics panel with crash counts
- Export GeoJSON/Shapefile

---

## West Windsor Specific Info

**Population**: ~28,000
**Area**: 26.2 sq mi
**Major Roads**:
- US Route 1
- Route 571 (Princeton-Hightstown Road)
- Clarksville Road
- Alexander Road
- Windsor Road

**Crash Hotspots** (typical):
- Route 1 corridor (high traffic)
- Route 571/Clarksville intersection
- Alexander Road near Princeton Junction

**Grant Relevance**:
- Eligible for SS4A Action Plan grants
- NJDOT Local Safety grant opportunities
- TAP (Transportation Alternatives Program)

---

## Data Freshness

- **Crash Data**: Updated regularly by NJDOT (typically 1-2 year lag)
- **Road Network**: Updated daily by OpenStreetMap/Geofabrik
- **Municipalities**: Static (boundaries don't change often)

**Latest Available**: Check https://data.nj.gov/Transportation/Total-NJ-Crash-Records-By-Year/86dt-kggc for most recent year

---

## Cost

**Total Cost**: $0 (all data sources are free)

- NJ Open Data Portal: Free
- OpenStreetMap: Free and open source
- Socrata API Token: Free
- No licensing fees

---

**Ready to pull West Windsor data!**

Run the Step 3 command above to start ingestion.
