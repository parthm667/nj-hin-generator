#!/usr/bin/env python3
"""
NJ Municipality Boundaries Ingestion

Downloads NJ municipal boundaries from the Socrata API
and loads them into the PostgreSQL database.

Data Source: https://data.nj.gov/
"""

import os
import sys
import logging
import requests
from pathlib import Path
from typing import List, Dict

# Import through the repo root so the models are registered once, under the
# same `backend.app` package the API and the other scripts use.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from sqlalchemy.orm import Session
from backend.app.models.database import SessionLocal
from backend.app.models.tables import Municipality

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MunicipalityIngester:
    """Ingests municipality boundaries from NJ Open Data Portal."""

    # Socrata API endpoint for NJ municipalities
    BASE_URL = "https://data.nj.gov/resource/k9xb-zgh4.json"

    def __init__(self, api_token: str = None):
        """Initialize the ingester."""
        self.api_token = api_token
        self.session = requests.Session()

        if api_token:
            self.session.headers.update({'X-App-Token': api_token})

    def fetch_municipalities(self) -> List[Dict]:
        """
        Fetch all NJ municipalities from Socrata API.

        Returns:
            List of municipality records
        """
        params = {
            '$limit': 1000,  # NJ has 565 municipalities
            '$order': 'mun ASC',
        }

        logger.info("Fetching municipalities from NJ Open Data Portal...")

        try:
            response = self.session.get(self.BASE_URL, params=params)
            response.raise_for_status()

            data = response.json()
            logger.info(f"Fetched {len(data)} municipalities")

            return data

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching municipality data: {e}")
            raise

    def load_to_db(self, municipalities: List[Dict], db: Session) -> int:
        """
        Load municipalities to database.

        Args:
            municipalities: List of municipality records
            db: Database session

        Returns:
            Number of municipalities loaded
        """
        logger.info("Loading municipalities to database...")

        loaded_count = 0

        for muni_data in municipalities:
            try:
                # Extract fields
                muni_code = muni_data.get('mun_code')
                muni_name = muni_data.get('mun')
                county_name = muni_data.get('county')

                if not muni_code or not muni_name:
                    continue

                # Check if already exists
                existing = db.query(Municipality).filter(
                    Municipality.muni_code == muni_code
                ).first()

                if existing:
                    continue

                # Extract geometry if available
                geom_wkt = None
                if 'the_geom' in muni_data:
                    geom_data = muni_data['the_geom']
                    if geom_data and 'coordinates' in geom_data:
                        # Convert to WKT format
                        geom_wkt = self._geojson_to_wkt(geom_data)

                # Create municipality record
                municipality = Municipality(
                    muni_code=muni_code,
                    name=muni_name,
                    county=county_name,
                    geom=geom_wkt
                )

                db.add(municipality)
                loaded_count += 1

                if loaded_count % 100 == 0:
                    db.commit()
                    logger.info(f"Loaded {loaded_count} municipalities...")

            except Exception as e:
                logger.error(f"Error loading municipality {muni_data.get('mun')}: {e}")
                continue

        db.commit()
        logger.info(f"Successfully loaded {loaded_count} municipalities")
        return loaded_count

    def _geojson_to_wkt(self, geojson: Dict) -> str:
        """Convert GeoJSON geometry to WKT format."""
        from shapely.geometry import shape

        try:
            geom = shape(geojson)
            return f"SRID=4326;{geom.wkt}"
        except Exception as e:
            logger.warning(f"Error converting GeoJSON to WKT: {e}")
            return None


def main():
    """Main ingestion workflow."""
    import argparse

    parser = argparse.ArgumentParser(description='Ingest NJ municipalities')
    parser.add_argument('--api-token', type=str, default=None,
                        help='Socrata API token (optional)')

    args = parser.parse_args()

    # Get API token from environment if not provided
    api_token = args.api_token or os.getenv('SOCRATA_API_TOKEN')

    if not api_token:
        logger.warning(
            "No API token provided. Get token at: https://data.nj.gov/profile/app_tokens"
        )

    logger.info("Starting municipality data ingestion...")

    # Initialize ingester
    ingester = MunicipalityIngester(api_token=api_token)

    # Fetch municipalities
    municipalities = ingester.fetch_municipalities()

    if not municipalities:
        logger.error("No municipalities found")
        return

    # Load to database
    db = SessionLocal()
    try:
        loaded = ingester.load_to_db(municipalities, db)
        logger.info(f"Successfully loaded {loaded} municipalities")
    finally:
        db.close()


if __name__ == '__main__':
    main()
