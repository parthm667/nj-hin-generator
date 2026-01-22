# Setup Guide

Complete setup instructions for the NJ High Injury Network Generator.

## Prerequisites

### Required Software

- **Python 3.9+**: Backend runtime
- **Node.js 16+**: Frontend development
- **PostgreSQL 15+**: Database with PostGIS extension
- **Git**: Version control

### Optional Tools

- **pgAdmin**: Database management GUI
- **Postman**: API testing
- **QGIS**: GIS analysis and visualization

## Database Setup

### 1. Install PostgreSQL and PostGIS

#### Ubuntu/Debian
```bash
sudo apt-get update
sudo apt-get install postgresql-15 postgresql-15-postgis-3 postgresql-contrib
```

#### macOS (Homebrew)
```bash
brew install postgresql@15 postgis
brew services start postgresql@15
```

#### Windows
Download and install from:
- PostgreSQL: https://www.postgresql.org/download/windows/
- PostGIS: https://postgis.net/install/

### 2. Create Database

```bash
# Connect to PostgreSQL
sudo -u postgres psql

# Create database and user
CREATE DATABASE nj_hin_db;
CREATE USER hin_user WITH PASSWORD 'secure_password_here';
GRANT ALL PRIVILEGES ON DATABASE nj_hin_db TO hin_user;

# Connect to database and enable PostGIS
\c nj_hin_db
CREATE EXTENSION postgis;
CREATE EXTENSION postgis_topology;

# Verify PostGIS installation
SELECT PostGIS_version();

# Exit
\q
```

### 3. Configure Database Connection

Create `backend/.env` file:
```
DATABASE_URL=postgresql://hin_user:secure_password_here@localhost:5432/nj_hin_db
DATABASE_HOST=localhost
DATABASE_PORT=5432
DATABASE_NAME=nj_hin_db
DATABASE_USER=hin_user
DATABASE_PASSWORD=secure_password_here
```

## Backend Setup

### 1. Clone Repository

```bash
git clone https://github.com/parthm667/nj-hin-generator.git
cd nj-hin-generator
```

### 2. Create Python Virtual Environment

```bash
cd backend
python -m venv venv

# Activate virtual environment
# On Linux/macOS:
source venv/bin/activate
# On Windows:
venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Configure Environment

```bash
cp .env.example .env
# Edit .env with your settings
```

### 5. Initialize Database

```bash
# Run from backend directory with venv activated
python -c "from app.models.database import init_db, init_postgis; init_postgis(); init_db()"
```

### 6. Verify Setup

```bash
# Start development server
uvicorn app.main:app --reload

# In another terminal, test API
curl http://localhost:8000/health
```

Should return:
```json
{
  "status": "healthy",
  "version": "0.1.0",
  "database": "connected"
}
```

## Frontend Setup

### 1. Install Dependencies

```bash
cd frontend
npm install
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit if backend is not on localhost:8000
```

Default `.env`:
```
REACT_APP_API_URL=http://localhost:8000/api
```

### 3. Start Development Server

```bash
npm start
```

Application opens at http://localhost:3000

### 4. Build for Production

```bash
npm run build
```

Creates optimized build in `build/` directory.

## Data Ingestion

### 1. Municipal Boundaries

Downloads automatically from NJ GIS Open Data:

```bash
cd backend
source venv/bin/activate
python scripts/ingest_municipalities.py
```

### 2. Road Network

Requires downloading NJ OSM extract from Geofabrik:

```bash
# Download NJ extract
wget https://download.geofabrik.de/north-america/us/new-jersey-latest.osm.pbf

# Extract roads using ogr2ogr
ogr2ogr -f GeoJSON data/raw/nj_roads.geojson \
  new-jersey-latest.osm.pbf lines \
  -where "highway IS NOT NULL"

# Ingest into database
python scripts/ingest_roads.py
```

### 3. Crash Data

Requires access to NJ crash data. Options:

#### Option A: NJ Open Data Portal
```bash
# Set NJ crash data URL in .env
NJ_CRASH_DATA_URL=https://data.nj.gov/resource/YOUR_DATASET_ID.json

# Run ingestion
python scripts/ingest_crashes.py
```

#### Option B: Manual CSV Files
Place crash CSV files in `data/raw/` directory:
- `nj_crashes_2017.csv`
- `nj_crashes_2018.csv`
- etc.

Then run:
```bash
python scripts/ingest_crashes.py
```

### 4. Census Data

Requires Census API key (free from census.gov):

```bash
# Add to .env
CENSUS_API_KEY=your_census_api_key_here

# Run ingestion
python scripts/ingest_census.py
```

Get API key: https://api.census.gov/data/key_signup.html

## Verification

### Check Database Content

```bash
psql -U hin_user -d nj_hin_db

SELECT COUNT(*) FROM municipalities;
SELECT COUNT(*) FROM road_segments;
SELECT COUNT(*) FROM crashes;
SELECT COUNT(*) FROM census_tracts;
```

### Run Test Analysis

1. Open http://localhost:3000
2. Select "West Windsor" municipality
3. Set years: 2017-2021
4. Click "Run Analysis"
5. Wait for completion
6. View results on map

## Troubleshooting

### Database Connection Errors

```bash
# Check PostgreSQL is running
sudo systemctl status postgresql

# Check PostGIS extension
psql -U hin_user -d nj_hin_db -c "SELECT PostGIS_version();"
```

### Import Errors

```bash
# Reinstall dependencies
pip install -r requirements.txt --force-reinstall
```

### Port Already in Use

```bash
# Backend (change port in .env)
API_PORT=8001

# Frontend (change in package.json or use PORT env var)
PORT=3001 npm start
```

### CORS Errors

Update `backend/.env`:
```
CORS_ORIGINS=http://localhost:3000,http://localhost:3001
```

## Next Steps

- [User Guide](USER_GUIDE.md) - How to use the application
- [Data Sources](DATA_SOURCES.md) - Where to get data
- [Deployment](DEPLOYMENT.md) - Production deployment
- [API Documentation](http://localhost:8000/docs) - Interactive API docs
