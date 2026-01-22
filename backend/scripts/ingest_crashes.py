"""
Ingest NJ crash data from NJ Open Data portal.

Downloads crash records from the NJ DOT database and loads them into the database.
Source: https://www.state.nj.us/transportation/refdata/accident/
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import requests
from datetime import datetime
from sqlalchemy.orm import Session
from backend.app.models.database import SessionLocal, init_db
from backend.app.models.tables import Crash, Municipality
from backend.app.config import settings
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# NJ Crash Data API endpoints
# Note: Actual URLs need to be confirmed with NJ Open Data portal
NJ_CRASH_API_BASE = "https://data.nj.gov/resource/accidents.json"


def download_crash_data(year: int, output_path: str = None) -> pd.DataFrame:
    """
    Download crash data for a specific year from NJ Open Data.

    Args:
        year: Year to download
        output_path: Optional path to save CSV

    Returns:
        DataFrame with crash records
    """
    logger.info(f"Downloading crash data for {year}...")

    # Build API query
    # Note: Adjust based on actual NJ Open Data API structure
    url = f"{NJ_CRASH_API_BASE}?$where=crash_year={year}&$limit=50000"

    try:
        # For large datasets, may need pagination
        all_records = []
        offset = 0
        limit = 10000

        while True:
            paginated_url = f"{url}&$offset={offset}&$limit={limit}"
            response = requests.get(paginated_url, timeout=60)
            response.raise_for_status()

            data = response.json()
            if not data:
                break

            all_records.extend(data)
            offset += limit

            logger.info(f"Downloaded {len(all_records)} records...")

            if len(data) < limit:
                break

        df = pd.DataFrame(all_records)
        logger.info(f"Downloaded {len(df)} crash records for {year}")

        # Save to file if requested
        if output_path:
            df.to_csv(output_path, index=False)
            logger.info(f"Saved to {output_path}")

        return df

    except Exception as e:
        logger.error(f"Error downloading crash data for {year}: {e}")
        raise


def load_crash_data_from_csv(file_path: str) -> pd.DataFrame:
    """
    Load crash data from CSV file.

    Args:
        file_path: Path to CSV file

    Returns:
        DataFrame with crash records
    """
    logger.info(f"Loading crash data from {file_path}...")

    try:
        df = pd.read_csv(file_path)
        logger.info(f"Loaded {len(df)} crash records")
        return df

    except Exception as e:
        logger.error(f"Error loading crash data: {e}")
        raise


def process_crash_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Process and clean crash data.

    Args:
        df: Raw crash DataFrame

    Returns:
        Cleaned DataFrame
    """
    logger.info("Processing crash data...")

    # Standardize column names (adjust based on actual source schema)
    column_mapping = {
        'CRASH_DATE': 'crash_date',
        'DATE': 'crash_date',
        'CRASH_TIME': 'crash_time',
        'TIME': 'crash_time',
        'SEVERITY': 'severity',
        'CRASH_TYPE': 'severity',
        'LATITUDE': 'latitude',
        'LAT': 'latitude',
        'LONGITUDE': 'longitude',
        'LON': 'longitude',
        'LONG': 'longitude',
        'MUNICIPALITY': 'municipality',
        'MUNI': 'municipality',
        'ROAD_NAME': 'road_name',
        'STREET': 'road_name',
        'ROUTE': 'route_number',
        'PEDESTRIAN': 'ped_involved',
        'BICYCLE': 'bike_involved',
        'CYCLIST': 'bike_involved'
    }

    # Rename columns
    df.rename(columns=column_mapping, inplace=True)

    # Parse dates
    if 'crash_date' in df.columns:
        df['crash_date'] = pd.to_datetime(df['crash_date'], errors='coerce')

    # Standardize severity levels
    severity_mapping = {
        'fatal': 'fatal',
        'fatality': 'fatal',
        'killed': 'fatal',
        'serious injury': 'serious_injury',
        'incapacitating': 'serious_injury',
        'moderate injury': 'minor_injury',
        'minor injury': 'minor_injury',
        'possible injury': 'minor_injury',
        'non-incapacitating': 'minor_injury',
        'property damage': 'property_damage',
        'pdo': 'property_damage',
        'no injury': 'property_damage'
    }

    if 'severity' in df.columns:
        df['severity'] = df['severity'].str.lower().map(severity_mapping)
        df['severity'] = df['severity'].fillna('property_damage')

    # Process boolean flags
    for col in ['ped_involved', 'bike_involved']:
        if col in df.columns:
            df[col] = df[col].fillna(False)
            if df[col].dtype == 'object':
                df[col] = df[col].str.lower().isin(['yes', 'true', '1', 'y'])

    # Filter records with valid coordinates
    if 'latitude' in df.columns and 'longitude' in df.columns:
        # NJ approximate bounds: 38.9°N - 41.4°N, 73.9°W - 75.6°W
        df = df[
            (df['latitude'].between(38.5, 41.5)) &
            (df['longitude'].between(-75.7, -73.8))
        ].copy()

        logger.info(f"Filtered to {len(df)} records with valid NJ coordinates")

    # Add geocode quality assessment
    df['geocode_quality'] = 'medium'

    # Drop records without essential fields
    essential_fields = ['crash_date', 'latitude', 'longitude', 'severity']
    df = df.dropna(subset=essential_fields)

    logger.info(f"Processed {len(df)} crash records")

    return df


def assign_municipalities_to_crashes(df: pd.DataFrame, db: Session) -> pd.DataFrame:
    """
    Assign municipality ID to each crash based on coordinates.

    Args:
        df: DataFrame with crash records
        db: Database session

    Returns:
        DataFrame with muni_id assigned
    """
    logger.info("Assigning municipalities to crashes...")

    # Load municipalities
    municipalities = db.query(Municipality).all()

    if not municipalities:
        logger.error("No municipalities found. Run ingest_municipalities.py first.")
        raise ValueError("Municipalities must be loaded before crashes")

    # Create spatial query for each crash
    # Note: This is a simplified approach. For better performance,
    # use spatial database query directly.

    import geopandas as gpd
    from shapely.geometry import Point

    # Create GeoDataFrame from crashes
    geometry = [Point(xy) for xy in zip(df['longitude'], df['latitude'])]
    crash_gdf = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")

    # Create GeoDataFrame from municipalities
    muni_data = []
    for muni in municipalities:
        muni_data.append({
            'muni_id': muni.muni_id,
            'name': muni.name,
            'geometry': gpd.GeoSeries.from_wkt([muni.geom])[0]
        })

    muni_gdf = gpd.GeoDataFrame(muni_data, crs="EPSG:4326")

    # Spatial join
    crash_with_muni = gpd.sjoin(crash_gdf, muni_gdf, how='left', predicate='within')

    assigned_count = len(crash_with_muni[crash_with_muni['muni_id'].notna()])
    logger.info(f"Assigned {assigned_count} crashes to municipalities")

    return crash_with_muni


def load_crashes(df: pd.DataFrame, db: Session):
    """
    Load crash records into database.

    Args:
        df: DataFrame with processed crash records
        db: Database session
    """
    logger.info("Loading crashes into database...")

    loaded_count = 0
    skipped_count = 0

    for idx, row in df.iterrows():
        try:
            # Skip if no municipality assignment
            if pd.isna(row.get('muni_id')):
                skipped_count += 1
                continue

            # Check for duplicate
            external_id = row.get('crash_id') or row.get('id') or f"{row['crash_date']}_{row['latitude']}_{row['longitude']}"

            existing = db.query(Crash).filter(
                Crash.external_id == str(external_id)
            ).first()

            if existing:
                skipped_count += 1
                continue

            # Create crash record
            crash = Crash(
                external_id=str(external_id),
                crash_date=row['crash_date'],
                crash_time=row.get('crash_time'),
                severity=row['severity'],
                ped_involved=row.get('ped_involved', False),
                bike_involved=row.get('bike_involved', False),
                road_name=row.get('road_name'),
                route_number=row.get('route_number'),
                contributing_factors=row.get('contributing_factors'),
                weather_condition=row.get('weather'),
                light_condition=row.get('light'),
                muni_id=int(row['muni_id']),
                geocode_quality=row.get('geocode_quality', 'medium'),
                geom=f"SRID=4326;POINT({row['longitude']} {row['latitude']})"
            )

            db.add(crash)
            loaded_count += 1

            if loaded_count % 1000 == 0:
                db.commit()
                logger.info(f"Loaded {loaded_count} crashes...")

        except Exception as e:
            logger.error(f"Error loading crash {idx}: {e}")
            db.rollback()
            continue

    db.commit()
    logger.info(f"Loaded {loaded_count} crashes, skipped {skipped_count}")


def main():
    """Main ingestion workflow."""
    logger.info("Starting crash data ingestion...")

    # Initialize database
    init_db()

    # Download or load crash data
    # For multiple years:
    years = range(2017, 2023)  # Last 5 years

    db = SessionLocal()
    try:
        for year in years:
            file_path = f'data/raw/nj_crashes_{year}.csv'

            # Try to load from file, or download if not exists
            if os.path.exists(file_path):
                df = load_crash_data_from_csv(file_path)
            else:
                logger.info(f"File not found: {file_path}")
                logger.info("Attempting to download...")
                try:
                    df = download_crash_data(year, file_path)
                except Exception as e:
                    logger.error(f"Could not download data for {year}: {e}")
                    continue

            # Process data
            df = process_crash_data(df)

            # Assign municipalities
            df = assign_municipalities_to_crashes(df, db)

            # Load into database
            load_crashes(df, db)

        logger.info("Crash data ingestion complete!")

    finally:
        db.close()


if __name__ == "__main__":
    main()
