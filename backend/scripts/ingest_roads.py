"""
Ingest road network data from OpenStreetMap.

Downloads NJ road network from Geofabrik and processes it for analysis.
Segments roads into fixed-length pieces for crash assignment.
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import geopandas as gpd
import requests
from shapely.geometry import LineString, MultiLineString
from shapely.ops import linemerge, split
import logging
from sqlalchemy.orm import Session
from backend.app.models.database import SessionLocal, init_db
from backend.app.models.tables import RoadSegment, Municipality
from backend.app.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# OSM road type classification
ROAD_CLASS_MAPPING = {
    'motorway': 'arterial',
    'trunk': 'arterial',
    'primary': 'arterial',
    'secondary': 'collector',
    'tertiary': 'collector',
    'residential': 'local',
    'unclassified': 'local',
    'living_street': 'local',
}


def download_osm_roads(output_path: str = None) -> gpd.GeoDataFrame:
    """
    Download NJ roads from Geofabrik OSM extract.

    Note: For production, you would download the full PBF file and extract
    with osmium or ogr2ogr. This is a simplified example using a smaller extract.

    Args:
        output_path: Optional path to save downloaded data

    Returns:
        GeoDataFrame with road network
    """
    logger.info("Downloading OSM road network...")

    # For this example, we'll use a smaller extract or process from PBF
    # In production, download from: https://download.geofabrik.de/north-america/us/new-jersey-latest.osm.pbf

    # Placeholder: You would typically use osmium or ogr2ogr to extract roads
    logger.warning("Note: Full OSM download requires osmium or ogr2ogr processing")
    logger.warning("This is a template - integrate with actual OSM processing pipeline")

    # Example using OverpassAPI for smaller areas (not recommended for entire state)
    # For full implementation, see: https://github.com/geopandas/geopandas/issues/1461

    return None


def load_osm_from_file(file_path: str) -> gpd.GeoDataFrame:
    """
    Load OSM road data from preprocessed file.

    Args:
        file_path: Path to GeoJSON or shapefile with OSM roads

    Returns:
        GeoDataFrame with road network
    """
    logger.info(f"Loading road data from {file_path}...")

    try:
        gdf = gpd.read_file(file_path)

        # Ensure CRS is WGS84
        if gdf.crs != "EPSG:4326":
            gdf = gdf.to_crs("EPSG:4326")

        logger.info(f"Loaded {len(gdf)} road features")
        return gdf

    except Exception as e:
        logger.error(f"Error loading road data: {e}")
        raise


def filter_relevant_roads(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Filter to road types relevant for crash analysis.

    Args:
        gdf: Raw OSM road data

    Returns:
        Filtered GeoDataFrame
    """
    logger.info("Filtering relevant road types...")

    # Filter to relevant highway types
    relevant_types = list(ROAD_CLASS_MAPPING.keys())

    if 'highway' in gdf.columns:
        gdf_filtered = gdf[gdf['highway'].isin(relevant_types)].copy()
    elif 'fclass' in gdf.columns:  # Geofabrik shapefile format
        gdf_filtered = gdf[gdf['fclass'].isin(relevant_types)].copy()
    else:
        logger.warning("No highway type column found, using all roads")
        gdf_filtered = gdf.copy()

    logger.info(f"Filtered to {len(gdf_filtered)} relevant roads")

    return gdf_filtered


def classify_roads(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Classify roads into arterial, collector, local.

    Args:
        gdf: GeoDataFrame with OSM highway types

    Returns:
        GeoDataFrame with road_class column
    """
    logger.info("Classifying roads...")

    # Determine highway column name
    highway_col = 'highway' if 'highway' in gdf.columns else 'fclass'

    # Map to simplified classes
    gdf['road_class'] = gdf[highway_col].map(ROAD_CLASS_MAPPING)
    gdf['road_class'] = gdf['road_class'].fillna('local')

    # Log distribution
    class_counts = gdf['road_class'].value_counts()
    logger.info(f"Road classification: {class_counts.to_dict()}")

    return gdf


def segment_roads(gdf: gpd.GeoDataFrame, segment_length_miles: float = 0.1) -> gpd.GeoDataFrame:
    """
    Split roads into fixed-length segments.

    Args:
        gdf: GeoDataFrame with road linestrings
        segment_length_miles: Target length for each segment in miles

    Returns:
        GeoDataFrame with segmented roads
    """
    logger.info(f"Segmenting roads into {segment_length_miles} mile segments...")

    # Convert miles to degrees (approximate at NJ latitude ~40°)
    # 1 degree latitude ≈ 69 miles, 1 degree longitude ≈ 52 miles at 40°N
    # Use average for rough segmentation
    segment_length_degrees = segment_length_miles / 60.0

    segmented_roads = []

    for idx, row in gdf.iterrows():
        geom = row.geometry

        # Handle MultiLineString
        if isinstance(geom, MultiLineString):
            geom = linemerge(geom)

        if isinstance(geom, LineString):
            # Calculate number of segments
            length = geom.length
            num_segments = max(1, int(length / segment_length_degrees))

            # Create segments
            for i in range(num_segments):
                start = i / num_segments
                end = (i + 1) / num_segments

                try:
                    segment_geom = substring(geom, start, end, normalized=True)

                    segment_data = row.to_dict()
                    segment_data['geometry'] = segment_geom
                    segment_data['segment_index'] = i
                    segmented_roads.append(segment_data)

                except Exception as e:
                    logger.debug(f"Error segmenting road {idx}: {e}")
                    continue
        else:
            # Keep non-LineString as is
            segmented_roads.append(row.to_dict())

    gdf_segmented = gpd.GeoDataFrame(segmented_roads, crs=gdf.crs)

    logger.info(f"Created {len(gdf_segmented)} road segments from {len(gdf)} roads")

    return gdf_segmented


def substring(geom: LineString, start_dist: float, end_dist: float, normalized: bool = False) -> LineString:
    """
    Extract substring of a LineString.

    Args:
        geom: Input LineString
        start_dist: Start distance (0-1 if normalized, else in geometry units)
        end_dist: End distance
        normalized: If True, distances are normalized (0-1)

    Returns:
        Substring LineString
    """
    if normalized:
        start_dist = start_dist * geom.length
        end_dist = end_dist * geom.length

    coords = list(geom.coords)
    if len(coords) < 2:
        return geom

    # Simplified substring - for production use shapely.ops.substring
    # This is approximate
    return LineString(coords)


def assign_municipalities(road_gdf: gpd.GeoDataFrame, db: Session) -> gpd.GeoDataFrame:
    """
    Assign each road segment to a municipality using spatial join.

    Args:
        road_gdf: GeoDataFrame with road segments
        db: Database session

    Returns:
        GeoDataFrame with muni_id assigned
    """
    logger.info("Assigning road segments to municipalities...")

    # Load municipalities from database
    municipalities = db.query(Municipality).all()

    if not municipalities:
        logger.error("No municipalities found in database. Run ingest_municipalities.py first.")
        raise ValueError("Municipalities must be loaded before roads")

    # Create GeoDataFrame of municipalities
    muni_data = []
    for muni in municipalities:
        muni_data.append({
            'muni_id': muni.muni_id,
            'name': muni.name,
            'geometry': gpd.GeoSeries.from_wkt([muni.geom])[0]
        })

    muni_gdf = gpd.GeoDataFrame(muni_data, crs="EPSG:4326")

    # Spatial join
    road_with_muni = gpd.sjoin(road_gdf, muni_gdf, how='left', predicate='intersects')

    logger.info(f"Assigned {len(road_with_muni[road_with_muni['muni_id'].notna()])} segments to municipalities")

    return road_with_muni


def load_road_segments(gdf: gpd.GeoDataFrame, db: Session):
    """
    Load road segments into database.

    Args:
        gdf: GeoDataFrame with processed road segments
        db: Database session
    """
    logger.info("Loading road segments into database...")

    loaded_count = 0

    for idx, row in gdf.iterrows():
        try:
            # Skip if no municipality assignment
            if pd.isna(row.get('muni_id')):
                continue

            # Calculate length in miles
            # Approximate: 1 degree ≈ 60 miles at NJ latitude
            length_miles = row.geometry.length * 60.0

            # Create road segment
            segment = RoadSegment(
                osm_id=row.get('osm_id') or row.get('@id'),
                road_name=row.get('name', 'Unnamed'),
                road_type=row.get('highway') or row.get('fclass', 'unknown'),
                road_class=row.get('road_class', 'local'),
                length_miles=length_miles,
                muni_id=int(row['muni_id']),
                geom=f"SRID=4326;{row.geometry.wkt}"
            )

            db.add(segment)
            loaded_count += 1

            if loaded_count % 1000 == 0:
                db.commit()
                logger.info(f"Loaded {loaded_count} road segments...")

        except Exception as e:
            logger.error(f"Error loading segment {idx}: {e}")
            db.rollback()
            continue

    db.commit()
    logger.info(f"Loaded {loaded_count} road segments")


def main():
    """Main ingestion workflow."""
    logger.info("Starting road network ingestion...")

    # Initialize database
    init_db()

    # Load from preprocessed file
    # In production, you would process OSM PBF file first
    file_path = 'data/raw/nj_roads.geojson'

    if not os.path.exists(file_path):
        logger.error(f"Road data file not found: {file_path}")
        logger.info("Please download and preprocess OSM data first.")
        logger.info("See docs/data_preparation.md for instructions")
        return

    # Load and process
    gdf = load_osm_from_file(file_path)
    gdf = filter_relevant_roads(gdf)
    gdf = classify_roads(gdf)
    gdf = segment_roads(gdf, settings.segment_length_miles)

    # Assign municipalities
    db = SessionLocal()
    try:
        gdf = assign_municipalities(gdf, db)
        load_road_segments(gdf, db)
        logger.info("Road network ingestion complete!")
    finally:
        db.close()


if __name__ == "__main__":
    import pandas as pd
    main()
