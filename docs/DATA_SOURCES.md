# New Jersey Crash Data Sources

## Overview

This document describes the available data sources for NJ crash data and how to access them for the High Injury Network Generator.

---

## Official Data Sources

### 1. NJ Open Data Portal (data.nj.gov)

**Primary Source**: [Total NJ Crash Records By Year](https://data.nj.gov/Transportation/Total-NJ-Crash-Records-By-Year/86dt-kggc)

**Platform**: Socrata Open Data (SODA) API

**Coverage**: 2001 - Present (updated regularly)

**Data Quality**:
- Crashes on private properties are not included
- Based on NJTR-1 (New Jersey Police Crash Investigation Report) forms
- Receives ~320,000 crash reports per year
- Data availability lags ~1-2 years

**API Access**:
```bash
# SODA API endpoint format
https://data.nj.gov/resource/{dataset-id}.json

# Example: Total NJ Crash Records By Year
https://data.nj.gov/resource/86dt-kggc.json
```

**API Documentation**: [Socrata Developers](https://dev.socrata.com/)

**Available Fields**:
- Crash date and time
- Municipality code
- County
- Crash severity
- Road information
- Weather/light conditions
- Pedestrian/bicycle involvement
- Latitude/Longitude (improved geocoding after 2017)

**Rate Limits**:
- 1000 requests per rolling hour (unauthenticated)
- Higher limits with API token

**Authentication**:
- Optional (recommended for production)
- Sign up at [data.nj.gov](https://data.nj.gov/)

---

### 2. NJDOT Safety Voyager

**Access**: Restricted to government agencies only

**URL**: [NJDOT Crash Data Search](https://www.nj.gov/transportation/refdata/accident/crashdatasearch.shtm)

**Features**:
- Web-based visualization tool
- Detailed crash analysis
- Traffic count data
- Not publicly accessible

**Note**: Individual crash reports can be purchased through [NJ Portal](https://www.njportal.com/njsp/crashreports/)

---

### 3. njtr1 R Package (Third-Party)

**Repository**: [gavinrozzi/njtr1](https://github.com/gavinrozzi/njtr1)

**Coverage**: 2001-2020

**Description**: R package for downloading and cleaning NJ crash data

**Installation**:
```r
install.packages("devtools")
devtools::install_github("gavinrozzi/njtr1")
```

**Usage**:
```r
library(njtr1)

# Download crash data
crashes <- get_njtr1_crashes(year = 2020)

# Clean and process
clean_crashes <- clean_njtr1(crashes)
```

**Advantage**: Pre-cleaned data, handles data quality issues

---

## Supporting Data Sources

### Municipal Boundaries

**Source**: [NJ Geographic Information Network (NJGIN)](https://njogis-newjersey.opendata.arcgis.com/)

**Dataset**: New Jersey Municipal Boundaries

**Format**: Shapefile, GeoJSON

**Direct Download**:
```bash
# ArcGIS REST API
https://services2.arcgis.com/XVOqAjTOJ5P6ngMu/arcgis/rest/services/
```

**Alternative**: [NJ Open Data](https://data.nj.gov/)

---

### Road Network

**Option 1: OpenStreetMap (Recommended)**

**Source**: [Geofabrik Downloads](https://download.geofabrik.de/north-america/us/new-jersey.html)

**URL**: `https://download.geofabrik.de/north-america/us/new-jersey-latest.osm.pbf`

**Update Frequency**: Daily

**Advantages**:
- Free and open
- Good coverage in NJ
- Easy to process with ogr2ogr or osm2pgsql

**Processing**:
```bash
# Download
wget https://download.geofabrik.de/north-america/us/new-jersey-latest.osm.pbf

# Convert to GeoJSON (roads only)
ogr2ogr -f GeoJSON nj_roads.geojson new-jersey-latest.osm.pbf lines \
  -sql "SELECT * FROM lines WHERE highway IS NOT NULL"
```

**Option 2: NJDOT Roadway Network**

**Source**: [NJ Open Data - NJDOT Roadway Network](https://data.nj.gov/Transportation/NJDOT-Roadway-Network/8i2w-vt9k)

**Format**: Shapefile

**Advantages**:
- Official state data
- Includes Standard Route Identifier (SRI)

**Disadvantages**:
- More complex to process
- Uses linear referencing system

---

### Census/Demographic Data

**Source 1: US Census Bureau**

**API**: [Census API](https://www.census.gov/data/developers/data-sets.html)

**Datasets**:
- American Community Survey (ACS) 5-Year Estimates
- Decennial Census
- TIGER/Line Shapefiles

**Key Variables**:
```
# Poverty
B17001_002E - Below poverty level

# Vehicles
B08201_002E - Households with no vehicle

# Income
B19013_001E - Median household income

# Race/Ethnicity
B02001_001E - Total population
B02001_002E - White alone
```

**Example API Call**:
```bash
https://api.census.gov/data/2021/acs/acs5?
  get=NAME,B01003_001E,B19013_001E&
  for=tract:*&
  in=state:34&
  key=YOUR_API_KEY
```

**API Key**: Free at [Census API Key Signup](https://api.census.gov/data/key_signup.html)

**Source 2: CDC Social Vulnerability Index**

**Download**: [CDC SVI Data](https://www.atsdr.cdc.gov/placeandhealth/svi/data_documentation_download.html)

**Format**: CSV, Shapefile

**NJ Download**:
```bash
# 2020 SVI for New Jersey
https://svi.cdc.gov/data-and-tools-download.html
```

---

## Implementation Status

### Current Implementation (Sample Data)

The application currently uses **generated sample data** for development and testing:

- **Location**: `backend/scripts/generate_sample_data.py`
- **Coverage**: 5 NJ municipalities (Princeton, West Windsor, Newark, Jersey City, Trenton)
- **Period**: 2017-2021
- **Volume**: ~2500 crashes, 250 road segments, 25 census tracts

**Advantages**:
- No API dependencies
- Immediate testing
- Predictable data quality
- No rate limits

### Future Implementation (Real Data)

**Priority 1**: NJ Open Data Portal
- Implement Socrata SODA API client
- Handle pagination
- Implement caching
- Add error handling for rate limits

**Priority 2**: OpenStreetMap Road Network
- Download and process Geofabrik extract
- Filter to relevant road types
- Segment into analysis units

**Priority 3**: Census Data
- Implement Census API client
- Calculate SVI scores
- Join to census tracts

---

## Real Data Integration Guide

### Step 1: NJ Open Data API Client

```python
import requests

class NJCrashDataClient:
    """Client for NJ Open Data Portal."""

    BASE_URL = "https://data.nj.gov/resource"

    def __init__(self, app_token=None):
        self.app_token = app_token
        self.session = requests.Session()

        if app_token:
            self.session.headers.update({
                'X-App-Token': app_token
            })

    def get_crashes(self, year, limit=10000, offset=0):
        """Fetch crash data for a specific year."""
        url = f"{self.BASE_URL}/86dt-kggc.json"

        params = {
            '$where': f"crash_year = {year}",
            '$limit': limit,
            '$offset': offset,
            '$order': 'crash_date'
        }

        response = self.session.get(url, params=params)
        response.raise_for_status()

        return response.json()

    def get_all_crashes(self, year):
        """Fetch all crashes for a year with pagination."""
        all_crashes = []
        offset = 0
        limit = 10000

        while True:
            crashes = self.get_crashes(year, limit=limit, offset=offset)

            if not crashes:
                break

            all_crashes.extend(crashes)
            offset += limit

            if len(crashes) < limit:
                break

        return all_crashes
```

### Step 2: Data Validation

```python
def validate_crash_record(crash):
    """Validate crash data quality."""
    required_fields = ['crash_date', 'municipality', 'county']

    # Check required fields
    for field in required_fields:
        if field not in crash or not crash[field]:
            return False, f"Missing {field}"

    # Validate coordinates
    if 'latitude' in crash and 'longitude' in crash:
        lat = float(crash['latitude'])
        lon = float(crash['longitude'])

        # NJ bounding box: 38.9°N - 41.4°N, -75.6°W - -73.9°W
        if not (38.9 <= lat <= 41.4 and -75.6 <= lon <= -73.9):
            return False, "Coordinates outside NJ"

    return True, "Valid"
```

### Step 3: Incremental Updates

```python
def sync_crashes_incremental(db, start_year, end_year):
    """Incrementally sync crash data."""
    client = NJCrashDataClient(app_token=settings.nj_data_api_token)

    for year in range(start_year, end_year + 1):
        # Check last sync date
        last_sync = get_last_sync_date(db, year)

        # Fetch new/updated crashes
        crashes = client.get_crashes_since(year, last_sync)

        # Process and load
        for crash_data in crashes:
            valid, msg = validate_crash_record(crash_data)

            if valid:
                load_crash(db, crash_data)

        # Update sync timestamp
        update_sync_date(db, year)
```

---

## Data Quality Considerations

### Geocoding Quality

**Issue**: Not all crashes have accurate lat/lon coordinates

**Mitigation**:
1. Filter to crashes after 2017 (better geocoding)
2. Use `geocode_quality` field if available
3. Attempt re-geocoding from address for low-quality records
4. Flag crashes with missing coordinates for manual review

### Municipality Matching

**Issue**: Municipality names may not match exactly

**Mitigation**:
1. Use municipality code/FIPS when available
2. Maintain lookup table for name variations
3. Use geocode to determine municipality (point-in-polygon)

### Missing Data

**Issue**: Some fields may be incomplete

**Mitigation**:
1. Define minimum required fields for analysis
2. Flag records with missing critical data
3. Provide data quality reports

---

## Costs and Rate Limits

### NJ Open Data Portal

- **Cost**: Free
- **Rate Limit**: 1000 req/hour (no token), higher with token
- **Recommended**: Register for API token

### Census API

- **Cost**: Free
- **Rate Limit**: No strict limit
- **Recommended**: Use API key for tracking

### OpenStreetMap

- **Cost**: Free
- **Rate Limit**: None (static downloads)
- **Update Frequency**: Daily

---

## Next Steps

1. **Obtain API Tokens**:
   - Register at [data.nj.gov](https://data.nj.gov/)
   - Get Census API key

2. **Implement API Clients**:
   - NJ Crash Data client
   - Census ACS client
   - OSM download automation

3. **Data Pipeline**:
   - Scheduled daily/weekly sync
   - Data quality validation
   - Error monitoring

4. **Testing**:
   - Compare sample vs. real data
   - Validate analysis results
   - Performance benchmarking

---

## References

- [NJ DOT Crash Records](https://dot.nj.gov/transportation/refdata/accident/)
- [NJ Open Data Portal](https://data.nj.gov/)
- [Socrata API Documentation](https://dev.socrata.com/)
- [US Census API](https://www.census.gov/data/developers.html)
- [OpenStreetMap Wiki](https://wiki.openstreetmap.org/)
- [FHWA Safe Streets for All](https://www.transportation.gov/grants/SS4A)

