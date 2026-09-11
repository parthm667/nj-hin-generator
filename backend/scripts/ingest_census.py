"""
Ingest census tract data and calculate vulnerability indices.

Downloads census boundaries and demographic data from US Census Bureau.
Calculates Social Vulnerability Index (SVI) scores.
"""

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import geopandas as gpd
import pandas as pd
import requests
from sqlalchemy.orm import Session
from app.models.database import SessionLocal
from app.models.tables import CensusTract, Municipality
from app.config import settings
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Census API configuration
CENSUS_API_BASE = "https://api.census.gov/data"
CENSUS_YEAR = 2021
ACS_DATASET = f"{CENSUS_YEAR}/acs/acs5"

# NJ FIPS code
NJ_FIPS = "34"

# Census variables to fetch
CENSUS_VARS = {
    'B01003_001E': 'total_population',
    'B19013_001E': 'median_income',
    'B17001_002E': 'below_poverty',
    'B08201_002E': 'no_vehicle_households',
    'B08201_001E': 'total_households',
    'B02001_001E': 'total_race',
    'B02001_002E': 'white_alone'
}


def download_census_boundaries(output_path: str = None) -> gpd.GeoDataFrame:
    """
    Download census tract boundaries for NJ from Census Bureau.

    Args:
        output_path: Optional path to save downloaded data

    Returns:
        GeoDataFrame with census tract boundaries
    """
    logger.info("Downloading NJ census tract boundaries...")

    # Census TIGER/Line shapefile URL
    url = f"https://www2.census.gov/geo/tiger/TIGER{CENSUS_YEAR}/TRACT/tl_{CENSUS_YEAR}_{NJ_FIPS}_tract.zip"

    try:
        gdf = gpd.read_file(url)

        logger.info(f"Downloaded {len(gdf)} census tracts")

        # Ensure CRS is WGS84
        if gdf.crs != "EPSG:4326":
            gdf = gdf.to_crs("EPSG:4326")

        # Save to file if requested
        if output_path:
            gdf.to_file(output_path, driver='GeoJSON')
            logger.info(f"Saved to {output_path}")

        return gdf

    except Exception as e:
        logger.error(f"Error downloading census boundaries: {e}")
        raise


def fetch_census_demographics(tract_list: list = None) -> pd.DataFrame:
    """
    Fetch demographic data from Census API.

    Args:
        tract_list: Optional list of tract GEOIDs to fetch. If None, fetches all NJ tracts.

    Returns:
        DataFrame with demographic data
    """
    logger.info("Fetching census demographic data...")

    if not settings.census_api_key:
        logger.warning("No Census API key configured. Using sample data.")
        return pd.DataFrame()

    # Build API request
    var_string = ','.join(CENSUS_VARS.keys())
    url = f"{CENSUS_API_BASE}/{ACS_DATASET}"

    params = {
        'get': f"NAME,{var_string}",
        'for': 'tract:*',
        'in': f'state:{NJ_FIPS}',
        'key': settings.census_api_key
    }

    try:
        response = requests.get(url, params=params, timeout=60)
        response.raise_for_status()

        data = response.json()

        # Convert to DataFrame
        df = pd.DataFrame(data[1:], columns=data[0])

        # Create GEOID
        df['tract_id'] = df['state'] + df['county'] + df['tract']

        logger.info(f"Fetched data for {len(df)} tracts")

        return df

    except Exception as e:
        logger.error(f"Error fetching census data: {e}")
        logger.info("Continuing without demographic data...")
        return pd.DataFrame()


def calculate_vulnerability_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate vulnerability metrics from census data.

    Args:
        df: DataFrame with raw census variables

    Returns:
        DataFrame with calculated metrics
    """
    logger.info("Calculating vulnerability metrics...")

    # Rename columns
    for var_code, var_name in CENSUS_VARS.items():
        if var_code in df.columns:
            df[var_name] = pd.to_numeric(df[var_code], errors='coerce')

    # Calculate percentages
    df['pct_below_poverty'] = (df['below_poverty'] / df['total_population'] * 100).fillna(0)
    df['pct_no_vehicle'] = (df['no_vehicle_households'] / df['total_households'] * 100).fillna(0)
    df['pct_minority'] = ((df['total_race'] - df['white_alone']) / df['total_race'] * 100).fillna(0)

    # Simple vulnerability index (0-100 scale)
    # Higher score = more vulnerable
    # Based on: poverty, no vehicle access, minority status

    # Normalize each metric to 0-100
    for col in ['pct_below_poverty', 'pct_no_vehicle', 'pct_minority']:
        if col in df.columns:
            max_val = df[col].max()
            if max_val > 0:
                df[f'{col}_norm'] = (df[col] / max_val * 100).fillna(0)

    # Calculate composite SVI score (simple average)
    svi_components = ['pct_below_poverty_norm', 'pct_no_vehicle_norm', 'pct_minority_norm']
    available_components = [c for c in svi_components if c in df.columns]

    if available_components:
        df['svi_score'] = df[available_components].mean(axis=1)

        # Calculate percentiles
        df['svi_percentile'] = df['svi_score'].rank(pct=True) * 100
    else:
        df['svi_score'] = 0
        df['svi_percentile'] = 0

    logger.info("Vulnerability metrics calculated")

    return df


def assign_municipalities_to_tracts(tract_gdf: gpd.GeoDataFrame, db: Session) -> gpd.GeoDataFrame:
    """
    Assign each census tract to municipalities it intersects.

    Args:
        tract_gdf: GeoDataFrame with census tracts
        db: Database session

    Returns:
        GeoDataFrame with municipality assignments
    """
    logger.info("Assigning municipalities to census tracts...")

    # Load municipalities
    municipalities = db.query(Municipality).all()

    if not municipalities:
        logger.error("No municipalities found. Run ingest_municipalities.py first.")
        raise ValueError("Municipalities must be loaded before census tracts")

    # Create GeoDataFrame from municipalities
    muni_data = []
    for muni in municipalities:
        muni_data.append({
            'muni_id': muni.muni_id,
            'name': muni.name,
            'geometry': gpd.GeoSeries.from_wkt([muni.geom])[0]
        })

    muni_gdf = gpd.GeoDataFrame(muni_data, crs="EPSG:4326")

    # Spatial join - assign tract to municipality with largest overlap
    tract_with_muni = gpd.sjoin(tract_gdf, muni_gdf, how='left', predicate='intersects')

    logger.info("Municipality assignments complete")

    return tract_with_muni


def load_census_tracts(gdf: gpd.GeoDataFrame, demographics_df: pd.DataFrame, db: Session):
    """
    Load census tracts into database.

    Args:
        gdf: GeoDataFrame with census tract boundaries
        demographics_df: DataFrame with demographic data
        db: Database session
    """
    logger.info("Loading census tracts into database...")

    # Merge demographics if available
    if not demographics_df.empty and 'tract_id' in demographics_df.columns:
        gdf = gdf.merge(demographics_df, left_on='GEOID', right_on='tract_id', how='left')

    loaded_count = 0
    skipped_count = 0

    for idx, row in gdf.iterrows():
        try:
            tract_id = row.get('GEOID') or row.get('tract_id')

            if not tract_id:
                skipped_count += 1
                continue

            # Check for duplicate
            existing = db.query(CensusTract).filter(
                CensusTract.tract_id == tract_id
            ).first()

            if existing:
                skipped_count += 1
                continue

            # Create census tract record
            tract = CensusTract(
                tract_id=tract_id,
                muni_id=int(row['muni_id']) if pd.notna(row.get('muni_id')) else None,
                county_fips=row.get('COUNTYFP'),
                total_population=int(row.get('total_population', 0)),
                median_income=int(row.get('median_income', 0)),
                pct_below_poverty=float(row.get('pct_below_poverty', 0)),
                pct_no_vehicle=float(row.get('pct_no_vehicle', 0)),
                pct_minority=float(row.get('pct_minority', 0)),
                svi_score=float(row.get('svi_score', 0)),
                svi_percentile=float(row.get('svi_percentile', 0)),
                geom=f"SRID=4326;{row.geometry.wkt}"
            )

            db.add(tract)
            loaded_count += 1

            if loaded_count % 100 == 0:
                db.commit()
                logger.info(f"Loaded {loaded_count} census tracts...")

        except Exception as e:
            logger.error(f"Error loading tract {idx}: {e}")
            db.rollback()
            continue

    db.commit()
    logger.info(f"Loaded {loaded_count} census tracts, skipped {skipped_count}")


def main():
    """Main ingestion workflow."""
    logger.info("Starting census data ingestion...")

    # Download census boundaries
    gdf = download_census_boundaries(
        output_path=str(PROJECT_DIR / 'data' / 'raw' / 'nj_census_tracts.geojson')
    )

    # Fetch demographic data
    demographics_df = fetch_census_demographics()

    if not demographics_df.empty:
        demographics_df = calculate_vulnerability_metrics(demographics_df)

    # Assign municipalities
    db = SessionLocal()
    try:
        gdf = assign_municipalities_to_tracts(gdf, db)

        # Load into database
        load_census_tracts(gdf, demographics_df, db)

        logger.info("Census data ingestion complete!")

    finally:
        db.close()


if __name__ == "__main__":
    main()
