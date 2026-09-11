# Setup Guide

Local setup instructions for the NJ High Injury Network Generator. For Vercel and container hosting, follow [DEPLOY.md](DEPLOY.md).

## Prerequisites

### Required Software

- **Python 3.11 or 3.12**: Backend runtime
- **Node.js 22**: Frontend development
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
DEBUG=false
CORS_ORIGINS=http://localhost:3000
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
# For offline ingestion, also install requirements-scripts.txt.
```

### 4. Configure Environment

```bash
cp .env.example .env
# Edit .env with your settings
```

### 5. Initialize Database

```bash
# Run from backend directory with venv activated
python scripts/init_schema.py
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
npm ci
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

Use [DATA_PIPELINE.md](DATA_PIPELINE.md) for the official NJGIN boundaries, measured NJDOT road network, and county/year NJDOT Accidents archives. It explains the important difference between reported coordinates and route/milepost-derived locations, as well as missing-data limitations.

Load boundaries → roads → crashes into a clean real-data database with the current `ingest_all_real_data.py` orchestrator. The official pipeline does not require GDAL. Legacy Socrata and OSM instructions are superseded; the old generic CSV and census scripts are not part of this verified workflow.

For synthetic demonstrations only, use the explicit sample-data commands in [DEPLOY.md](DEPLOY.md#load-data-explicitly). Never mix sample fixtures with real crash data. Census/equity ingestion is optional and remains a separate job; an absent overlay does not indicate low vulnerability.

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
- [Deployment](DEPLOY.md) - Production deployment
- [API Documentation](http://localhost:8000/docs) - Interactive API docs
