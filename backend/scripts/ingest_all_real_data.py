#!/usr/bin/env python3
"""
Master Real Data Ingestion Script

Orchestrates ingestion of all real data sources:
1. NJ Municipalities (Socrata API)
2. NJ Crash Data (Socrata API)
3. OSM Road Network (Geofabrik)
4. Census Data (optional)
"""

import os
import sys
import logging
import argparse
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def run_ingestion_step(script_name: str, args: list = None) -> bool:
    """
    Run an ingestion script and return success status.
    
    Args:
        script_name: Name of the script to run
        args: Optional command-line arguments
        
    Returns:
        True if successful, False otherwise
    """
    import subprocess
    
    script_path = Path(__file__).parent / script_name
    
    if not script_path.exists():
        logger.error(f"Script not found: {script_path}")
        return False
    
    cmd = [sys.executable, str(script_path)]
    if args:
        cmd.extend(args)
    
    logger.info(f"Running: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(cmd, check=True, capture_output=False)
        return result.returncode == 0
    except subprocess.CalledProcessError as e:
        logger.error(f"Script failed with exit code {e.returncode}")
        return False


def main():
    """Main orchestration workflow."""
    parser = argparse.ArgumentParser(
        description='Ingest all real NJ data sources',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Ingest all data for Mercer County, 2017-2021
  python ingest_all_real_data.py --county Mercer --start-year 2017 --end-year 2021
  
  # Ingest just Princeton data
  python ingest_all_real_data.py --municipality Princeton --county Mercer
  
  # Use API token for higher rate limits
  export SOCRATA_API_TOKEN=your_token_here
  python ingest_all_real_data.py --county Mercer
        """
    )
    
    parser.add_argument('--start-year', type=int, default=2017,
                        help='Start year for crash data (default: 2017)')
    parser.add_argument('--end-year', type=int, default=2021,
                        help='End year for crash data (default: 2021)')
    parser.add_argument('--county', type=str, default=None,
                        help='Filter by county (e.g., "Mercer")')
    parser.add_argument('--municipality', type=str, default=None,
                        help='Filter by municipality (e.g., "Princeton")')
    parser.add_argument('--skip-municipalities', action='store_true',
                        help='Skip municipality ingestion')
    parser.add_argument('--skip-crashes', action='store_true',
                        help='Skip crash data ingestion')
    parser.add_argument('--skip-roads', action='store_true',
                        help='Skip road network ingestion')
    parser.add_argument('--max-crashes', type=int, default=None,
                        help='Maximum crash records to fetch')
    parser.add_argument('--api-token', type=str, default=None,
                        help='Socrata API token')
    
    args = parser.parse_args()
    
    # Get API token from environment if not provided
    api_token = args.api_token or os.getenv('SOCRATA_API_TOKEN')
    
    logger.info("=" * 60)
    logger.info("Real NJ Data Ingestion Pipeline")
    logger.info("=" * 60)
    logger.info(f"Period: {args.start_year} - {args.end_year}")
    if args.county:
        logger.info(f"County: {args.county}")
    if args.municipality:
        logger.info(f"Municipality: {args.municipality}")
    if api_token:
        logger.info("API Token: Configured")
    else:
        logger.warning("No API token - rate limits will apply")
        logger.warning("Get free token at: https://data.nj.gov/profile/app_tokens")
    logger.info("=" * 60)
    
    success_count = 0
    total_steps = 3
    
    # Step 1: Municipalities
    if not args.skip_municipalities:
        logger.info("\n[STEP 1/3] Ingesting Municipalities...")
        logger.info("-" * 60)
        
        muni_args = []
        if api_token:
            muni_args.extend(['--api-token', api_token])
        
        if run_ingestion_step('ingest_municipalities.py', muni_args):
            logger.info("✓ Municipalities ingested successfully")
            success_count += 1
        else:
            logger.error("✗ Municipality ingestion failed")
    else:
        logger.info("\n[STEP 1/3] Skipping Municipalities...")
        total_steps -= 1
    
    # Step 2: Crash Data
    if not args.skip_crashes:
        logger.info("\n[STEP 2/3] Ingesting Crash Data...")
        logger.info("-" * 60)
        
        crash_args = [
            '--start-year', str(args.start_year),
            '--end-year', str(args.end_year)
        ]
        
        if args.county:
            crash_args.extend(['--county', args.county])
        
        if args.municipality:
            crash_args.extend(['--municipality', args.municipality])
        
        if args.max_crashes:
            crash_args.extend(['--max-records', str(args.max_crashes)])
        
        if api_token:
            crash_args.extend(['--api-token', api_token])
        
        if run_ingestion_step('ingest_nj_crash_data.py', crash_args):
            logger.info("✓ Crash data ingested successfully")
            success_count += 1
        else:
            logger.error("✗ Crash data ingestion failed")
    else:
        logger.info("\n[STEP 2/3] Skipping Crash Data...")
        total_steps -= 1
    
    # Step 3: Road Network
    if not args.skip_roads:
        logger.info("\n[STEP 3/3] Ingesting Road Network...")
        logger.info("-" * 60)
        logger.warning("Note: Road network ingestion requires ogr2ogr (GDAL)")
        logger.warning("This may take 10-30 minutes for full NJ dataset")
        
        road_args = ['--segment-length', '0.1']
        
        if run_ingestion_step('ingest_osm_roads.py', road_args):
            logger.info("✓ Road network ingested successfully")
            success_count += 1
        else:
            logger.error("✗ Road network ingestion failed")
            logger.warning("If ogr2ogr is not installed:")
            logger.warning("  Ubuntu/Debian: sudo apt-get install gdal-bin")
            logger.warning("  MacOS: brew install gdal")
    else:
        logger.info("\n[STEP 3/3] Skipping Road Network...")
        total_steps -= 1
    
    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("INGESTION COMPLETE")
    logger.info("=" * 60)
    logger.info(f"Success: {success_count}/{total_steps} steps completed")
    
    if success_count == total_steps:
        logger.info("✓ All data ingested successfully!")
        logger.info("\nNext steps:")
        logger.info("  1. Start backend: uvicorn app.main:app --reload")
        logger.info("  2. Start frontend: cd frontend && npm start")
        logger.info("  3. Create analysis via UI or API")
        return 0
    else:
        logger.error("✗ Some ingestion steps failed")
        logger.error("Check logs above for details")
        return 1


if __name__ == '__main__':
    sys.exit(main())
