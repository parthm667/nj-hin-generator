#!/usr/bin/env python3
"""
Real NJ Crash Data Ingestion Script

Downloads actual crash data from the NJ Open Data Portal (Socrata API)
and loads it into the PostgreSQL database.

Data Source: https://data.nj.gov/Transportation/Total-NJ-Crash-Records-By-Year/86dt-kggc
API Docs: https://dev.socrata.com/
"""

import os
import sys
import requests
import logging
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.orm import Session
from app.models.database import SessionLocal, engine, Base
from app.models.tables import Crash, Municipality

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class NJCrashDataIngester:
    """Ingests real crash data from NJ Open Data Portal."""

    # Socrata API endpoint
    BASE_URL = "https://data.nj.gov/resource/86dt-kggc.json"

    # Severity mapping from NJ data to our schema
    SEVERITY_MAPPING = {
        'Fatal': 'fatal',
        'Incapacitating Injury': 'serious_injury',
        'Moderate Injury': 'minor_injury',
        'Complaint of Pain': 'minor_injury',
        'Property Damage Only': 'property_damage',
    }

    def __init__(self, api_token: Optional[str] = None):
        """
        Initialize the ingester.

        Args:
            api_token: Optional Socrata API token for higher rate limits
        """
        self.api_token = api_token
        self.session = requests.Session()

        if api_token:
            self.session.headers.update({'X-App-Token': api_token})

    def fetch_crashes(
        self,
        start_year: int,
        end_year: int,
        county: Optional[str] = None,
        municipality: Optional[str] = None,
        limit: int = 50000,
        offset: int = 0
    ) -> List[Dict]:
        """
        Fetch crashes from NJ Open Data Portal.

        Args:
            start_year: Start year for crash data
            end_year: End year for crash data
            county: Optional county filter (e.g., "Mercer")
            municipality: Optional municipality filter (e.g., "Princeton")
            limit: Maximum records to fetch per request (max 50,000)
            offset: Offset for pagination

        Returns:
            List of crash records
        """
        params = {
            '$limit': min(limit, 50000),  # Socrata max is 50,000
            '$offset': offset,
            '$order': 'crash_date DESC',
        }

        # Build WHERE clause
        where_clauses = []

        # Date filter
        where_clauses.append(f"crash_date >= '{start_year}-01-01T00:00:00.000'")
        where_clauses.append(f"crash_date <= '{end_year}-12-31T23:59:59.999'")

        # Geographic filters
        if county:
            where_clauses.append(f"county = '{county}'")

        if municipality:
            where_clauses.append(f"municipality = '{municipality}'")

        # Only include crashes with valid coordinates
        where_clauses.append("latitude IS NOT NULL")
        where_clauses.append("longitude IS NOT NULL")

        params['$where'] = ' AND '.join(where_clauses)

        logger.info(f"Fetching crashes with params: {params}")

        try:
            response = self.session.get(self.BASE_URL, params=params)
            response.raise_for_status()

            data = response.json()
            logger.info(f"Fetched {len(data)} crashes (offset: {offset})")

            return data

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching crash data: {e}")
            raise

    def fetch_all_crashes(
        self,
        start_year: int,
        end_year: int,
        county: Optional[str] = None,
        municipality: Optional[str] = None,
        max_records: Optional[int] = None
    ) -> List[Dict]:
        """
        Fetch all crashes with automatic pagination.

        Args:
            start_year: Start year for crash data
            end_year: End year for crash data
            county: Optional county filter
            municipality: Optional municipality filter
            max_records: Optional maximum total records to fetch

        Returns:
            List of all crash records
        """
        all_crashes = []
        offset = 0
        limit = 50000

        while True:
            crashes = self.fetch_crashes(
                start_year=start_year,
                end_year=end_year,
                county=county,
                municipality=municipality,
                limit=limit,
                offset=offset
            )

            if not crashes:
                break

            all_crashes.extend(crashes)

            if max_records and len(all_crashes) >= max_records:
                all_crashes = all_crashes[:max_records]
                break

            if len(crashes) < limit:
                # Last page
                break

            offset += limit

        logger.info(f"Total crashes fetched: {len(all_crashes)}")
        return all_crashes

    def map_severity(self, nj_severity: str) -> str:
        """
        Map NJ severity codes to our schema.

        Args:
            nj_severity: NJ crash severity description

        Returns:
            Standardized severity code
        """
        return self.SEVERITY_MAPPING.get(nj_severity, 'property_damage')

    def load_crashes_to_db(self, crashes: List[Dict], db: Session) -> int:
        """
        Load crashes into the database.

        Args:
            crashes: List of crash records from API
            db: Database session

        Returns:
            Number of crashes loaded
        """
        loaded_count = 0

        for crash_data in crashes:
            try:
                # Extract fields
                crash_id = crash_data.get('crash_id')
                if not crash_id:
                    continue

                # Check if already exists
                existing = db.query(Crash).filter(
                    Crash.external_id == crash_id
                ).first()

                if existing:
                    continue

                # Parse date
                crash_date_str = crash_data.get('crash_date')
                if crash_date_str:
                    crash_date = datetime.fromisoformat(
                        crash_date_str.replace('T', ' ').split('.')[0]
                    ).date()
                else:
                    continue

                # Get coordinates
                lat = crash_data.get('latitude')
                lon = crash_data.get('longitude')

                if not lat or not lon:
                    continue

                try:
                    lat = float(lat)
                    lon = float(lon)
                except (ValueError, TypeError):
                    continue

                # Validate coordinates (roughly NJ bounds)
                if not (38.9 <= lat <= 41.4 and -75.6 <= lon <= -73.9):
                    logger.warning(f"Invalid coordinates for crash {crash_id}: ({lat}, {lon})")
                    continue

                # Get severity
                severity_raw = crash_data.get('severity', 'Property Damage Only')
                severity = self.map_severity(severity_raw)

                # Create WKT geometry
                geom_wkt = f"SRID=4326;POINT({lon} {lat})"

                # Get municipality code if available
                muni_code = crash_data.get('municipality_code')

                # Create crash record
                crash = Crash(
                    external_id=crash_id,
                    crash_date=crash_date,
                    severity=severity,
                    geom=geom_wkt,
                    total_killed=int(crash_data.get('total_killed', 0)),
                    total_injured=int(crash_data.get('total_injured', 0)),
                    pedestrians_killed=int(crash_data.get('pedestrians_killed', 0)),
                    pedestrians_injured=int(crash_data.get('pedestrians_injured', 0)),
                    cyclists_killed=int(crash_data.get('bicyclists_killed', 0)),
                    cyclists_injured=int(crash_data.get('bicyclists_injured', 0)),
                    road_name=crash_data.get('road_name'),
                    road_type=crash_data.get('road_type'),
                )

                db.add(crash)
                loaded_count += 1

                if loaded_count % 1000 == 0:
                    db.commit()
                    logger.info(f"Loaded {loaded_count} crashes...")

            except Exception as e:
                logger.error(f"Error loading crash {crash_data.get('crash_id')}: {e}")
                continue

        db.commit()
        logger.info(f"Successfully loaded {loaded_count} crashes")
        return loaded_count


def main():
    """Main ingestion workflow."""
    import argparse

    parser = argparse.ArgumentParser(description='Ingest real NJ crash data')
    parser.add_argument('--start-year', type=int, default=2017,
                        help='Start year (default: 2017)')
    parser.add_argument('--end-year', type=int, default=2021,
                        help='End year (default: 2021)')
    parser.add_argument('--county', type=str, default=None,
                        help='Filter by county (e.g., "Mercer")')
    parser.add_argument('--municipality', type=str, default=None,
                        help='Filter by municipality (e.g., "Princeton")')
    parser.add_argument('--max-records', type=int, default=None,
                        help='Maximum records to fetch')
    parser.add_argument('--api-token', type=str, default=None,
                        help='Socrata API token (optional but recommended)')

    args = parser.parse_args()

    # Get API token from environment if not provided
    api_token = args.api_token or os.getenv('SOCRATA_API_TOKEN')

    if not api_token:
        logger.warning(
            "No API token provided. Rate limits apply (1000 requests/hour).\n"
            "Get a free token at: https://data.nj.gov/profile/app_tokens"
        )

    logger.info("Starting NJ crash data ingestion...")
    logger.info(f"Period: {args.start_year} - {args.end_year}")
    if args.county:
        logger.info(f"County: {args.county}")
    if args.municipality:
        logger.info(f"Municipality: {args.municipality}")

    # Initialize ingester
    ingester = NJCrashDataIngester(api_token=api_token)

    # Fetch crashes
    logger.info("Fetching crashes from NJ Open Data Portal...")
    crashes = ingester.fetch_all_crashes(
        start_year=args.start_year,
        end_year=args.end_year,
        county=args.county,
        municipality=args.municipality,
        max_records=args.max_records
    )

    if not crashes:
        logger.error("No crashes found matching criteria")
        return

    logger.info(f"Fetched {len(crashes)} crashes")

    # Load to database
    db = SessionLocal()
    try:
        logger.info("Loading crashes to database...")
        loaded = ingester.load_crashes_to_db(crashes, db)
        logger.info(f"✓ Successfully loaded {loaded} crashes")
    finally:
        db.close()


if __name__ == '__main__':
    main()
