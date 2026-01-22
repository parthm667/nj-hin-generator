"""
Ingest NJ municipal boundaries from NJ GIS Open Data.

Downloads municipal boundary shapefiles and loads them into the database.
Source: https://njogis-newjersey.opendata.arcgis.com/
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import geopandas as gpd
import requests
from sqlalchemy.orm import Session
from backend.app.models.database import SessionLocal, init_db, init_postgis
from backend.app.models.tables import Municipality
from shapely.geometry import shape
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# NJ Municipal Boundaries GeoJSON URL
NJ_MUNI_URL = "https://services2.arcgis.com/XVOqAjTOJ5P6ngMu/arcgis/rest/services/New_Jersey_Municipal_Boundaries/FeatureServer/0/query?where=1%3D1&outFields=*&outSR=4326&f=geojson"


def download_municipal_boundaries(output_path: str = None) -> gpd.GeoDataFrame:
    """
    Download NJ municipal boundaries from ArcGIS REST API.

    Args:
        output_path: Optional path to save the downloaded data

    Returns:
        GeoDataFrame with municipal boundaries
    """
    logger.info("Downloading NJ municipal boundaries...")

    try:
        response = requests.get(NJ_MUNI_URL, timeout=60)
        response.raise_for_status()

        # Load into GeoDataFrame
        gdf = gpd.read_file(response.text)

        logger.info(f"Downloaded {len(gdf)} municipalities")

        # Save to file if requested
        if output_path:
            gdf.to_file(output_path, driver='GeoJSON')
            logger.info(f"Saved to {output_path}")

        return gdf

    except Exception as e:
        logger.error(f"Error downloading municipal boundaries: {e}")
        raise


def process_municipal_data(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Process and clean municipal boundary data.

    Args:
        gdf: Raw GeoDataFrame from source

    Returns:
        Cleaned GeoDataFrame
    """
    logger.info("Processing municipal data...")

    # Ensure CRS is WGS84 (EPSG:4326)
    if gdf.crs != "EPSG:4326":
        gdf = gdf.to_crs("EPSG:4326")

    # Standardize column names (adjust based on actual source schema)
    column_mapping = {
        'MUN': 'name',
        'COUNTY': 'county',
        'MUN_CODE': 'muni_code',
        'MUNICIPAL': 'name',
        'MUNICIPALITY': 'name'
    }

    # Rename columns if they exist
    for old_col, new_col in column_mapping.items():
        if old_col in gdf.columns:
            gdf.rename(columns={old_col: new_col}, inplace=True)

    # Ensure geometry is MultiPolygon
    gdf['geometry'] = gdf['geometry'].apply(
        lambda geom: geom if geom.geom_type == 'MultiPolygon'
        else shape({'type': 'MultiPolygon', 'coordinates': [geom.__geo_interface__['coordinates']]})
    )

    # Clean text fields
    if 'name' in gdf.columns:
        gdf['name'] = gdf['name'].str.strip().str.title()
    if 'county' in gdf.columns:
        gdf['county'] = gdf['county'].str.strip().str.title()

    logger.info(f"Processed {len(gdf)} municipalities")

    return gdf


def load_municipalities(gdf: gpd.GeoDataFrame, db: Session):
    """
    Load municipal boundaries into database.

    Args:
        gdf: GeoDataFrame with municipal data
        db: Database session
    """
    logger.info("Loading municipalities into database...")

    loaded_count = 0
    skipped_count = 0

    for idx, row in gdf.iterrows():
        try:
            # Check if municipality already exists
            muni_code = row.get('muni_code')
            if muni_code:
                existing = db.query(Municipality).filter(
                    Municipality.muni_code == muni_code
                ).first()
                if existing:
                    logger.debug(f"Municipality {row['name']} already exists, skipping")
                    skipped_count += 1
                    continue

            # Create municipality record
            municipality = Municipality(
                name=row.get('name', 'Unknown'),
                county=row.get('county', 'Unknown'),
                muni_code=muni_code,
                geom=f"SRID=4326;{row.geometry.wkt}"
            )

            db.add(municipality)
            loaded_count += 1

            if loaded_count % 50 == 0:
                db.commit()
                logger.info(f"Loaded {loaded_count} municipalities...")

        except Exception as e:
            logger.error(f"Error loading municipality {row.get('name', 'unknown')}: {e}")
            db.rollback()
            continue

    db.commit()
    logger.info(f"Loaded {loaded_count} municipalities, skipped {skipped_count}")


def main():
    """Main ingestion workflow."""
    logger.info("Starting municipal boundaries ingestion...")

    # Initialize database
    try:
        init_postgis()
        init_db()
    except Exception as e:
        logger.warning(f"Database initialization warning: {e}")

    # Download data
    gdf = download_municipal_boundaries(
        output_path='data/raw/nj_municipalities.geojson'
    )

    # Process data
    gdf_processed = process_municipal_data(gdf)

    # Load into database
    db = SessionLocal()
    try:
        load_municipalities(gdf_processed, db)
        logger.info("Municipal boundaries ingestion complete!")
    finally:
        db.close()


if __name__ == "__main__":
    main()
