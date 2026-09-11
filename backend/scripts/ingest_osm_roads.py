#!/usr/bin/env python3
"""
OpenStreetMap Road Network Ingestion

Downloads NJ road network from OpenStreetMap via Geofabrik
and loads it into the PostgreSQL database.

Data Source: https://download.geofabrik.de/north-america/us/new-jersey.html
"""

import os
import sys
import logging
import subprocess
import requests
from pathlib import Path
from typing import Optional
import geopandas as gpd
from shapely.geometry import LineString

# Import through the repo root so the models are registered once, under the
# same `backend.app` package the API and the other scripts use.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from sqlalchemy.orm import Session
from backend.app.models.database import SessionLocal
from backend.app.models.tables import RoadSegment, Municipality

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class OSMRoadIngester:
    """Ingests road network from OpenStreetMap."""

    # Geofabrik NJ extract URL
    OSM_URL = "https://download.geofabrik.de/north-america/us/new-jersey-latest.osm.pbf"

    # Road type classification for HIN analysis
    ROAD_CLASSIFICATIONS = {
        'motorway': 'arterial',
        'trunk': 'arterial',
        'primary': 'arterial',
        'secondary': 'collector',
        'tertiary': 'collector',
        'residential': 'local',
        'unclassified': 'local',
        'service': 'local',
    }

    def __init__(self, data_dir: str = "./data"):
        """Initialize the ingester."""
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)

    def download_osm_data(self) -> Path:
        """
        Download NJ OSM data from Geofabrik.

        Returns:
            Path to downloaded OSM PBF file
        """
        output_file = self.data_dir / "new-jersey-latest.osm.pbf"

        if output_file.exists():
            logger.info(f"OSM data already downloaded: {output_file}")
            return output_file

        logger.info(f"Downloading NJ OSM data from Geofabrik...")
        logger.info(f"URL: {self.OSM_URL}")

        response = requests.get(self.OSM_URL, stream=True)
        response.raise_for_status()

        total_size = int(response.headers.get('content-length', 0))
        logger.info(f"File size: {total_size / 1024 / 1024:.1f} MB")

        with open(output_file, 'wb') as f:
            downloaded = 0
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
                downloaded += len(chunk)
                if total_size:
                    progress = (downloaded / total_size) * 100
                    if downloaded % (1024 * 1024 * 10) == 0:  # Log every 10MB
                        logger.info(f"Downloaded: {progress:.1f}%")

        logger.info(f"✓ Downloaded to {output_file}")
        return output_file

    def extract_roads_geojson(self, osm_file: Path) -> Path:
        """
        Extract road network from OSM PBF to GeoJSON.

        Requires ogr2ogr (GDAL) to be installed.

        Args:
            osm_file: Path to OSM PBF file

        Returns:
            Path to GeoJSON file
        """
        output_file = self.data_dir / "nj_roads.geojson"

        if output_file.exists():
            logger.info(f"Roads GeoJSON already exists: {output_file}")
            return output_file

        logger.info("Extracting roads from OSM data (this may take a few minutes)...")

        # Check if ogr2ogr is available
        try:
            subprocess.run(['ogr2ogr', '--version'], check=True, capture_output=True)
        except FileNotFoundError:
            raise RuntimeError(
                "ogr2ogr not found. Install GDAL:\n"
                "  Ubuntu/Debian: sudo apt-get install gdal-bin\n"
                "  MacOS: brew install gdal\n"
                "  Or use Docker: docker run --rm -v $(pwd):/data osgeo/gdal ogr2ogr ..."
            )

        # Extract roads with ogr2ogr
        # Filter to only major roads for HIN analysis
        sql = (
            "SELECT * FROM lines WHERE "
            "highway IN ('motorway', 'trunk', 'primary', 'secondary', "
            "'tertiary', 'residential', 'unclassified')"
        )

        cmd = [
            'ogr2ogr',
            '-f', 'GeoJSON',
            str(output_file),
            str(osm_file),
            'lines',
            '-sql', sql,
            '-t_srs', 'EPSG:4326'
        ]

        logger.info(f"Running: {' '.join(cmd)}")

        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            logger.info(f"✓ Extracted roads to {output_file}")
            return output_file
        except subprocess.CalledProcessError as e:
            logger.error(f"ogr2ogr failed: {e.stderr}")
            raise

    def segment_roads(
        self,
        geojson_file: Path,
        segment_length_miles: float = 0.1
    ) -> gpd.GeoDataFrame:
        """
        Segment roads into analysis units.

        Args:
            geojson_file: Path to GeoJSON file
            segment_length_miles: Length of each segment in miles

        Returns:
            GeoDataFrame of road segments
        """
        logger.info(f"Loading roads from {geojson_file}...")
        roads = gpd.read_file(geojson_file)

        logger.info(f"Loaded {len(roads)} roads")

        # Convert to projected CRS for length calculation (NJ State Plane)
        roads = roads.to_crs("EPSG:3424")  # NAD83 / New Jersey (ftUS)

        # Convert miles to feet (NJ State Plane is in feet)
        segment_length_ft = segment_length_miles * 5280

        logger.info(f"Segmenting roads into {segment_length_miles} mile segments...")

        segments = []
        segment_id = 0

        for idx, road in roads.iterrows():
            if road.geometry is None or road.geometry.is_empty:
                continue

            if not isinstance(road.geometry, LineString):
                continue

            # Get road properties
            highway_type = road.get('highway', 'unclassified')
            road_class = self.ROAD_CLASSIFICATIONS.get(highway_type, 'local')
            road_name = road.get('name', 'Unnamed Road')

            # Segment the road
            line = road.geometry
            line_length = line.length

            if line_length < segment_length_ft:
                # Road is shorter than segment length, keep as one segment
                segments.append({
                    'segment_id': segment_id,
                    'osm_id': road.get('osm_id', f'generated_{segment_id}'),
                    'name': road_name,
                    'road_class': road_class,
                    'highway_type': highway_type,
                    'length_miles': line_length / 5280,
                    'geometry': line
                })
                segment_id += 1
            else:
                # Split into segments
                num_segments = int(line_length / segment_length_ft) + 1

                for i in range(num_segments):
                    start_dist = i * segment_length_ft
                    end_dist = min((i + 1) * segment_length_ft, line_length)

                    if start_dist >= line_length:
                        break

                    # Create segment
                    try:
                        segment_geom = self._extract_segment(
                            line, start_dist, end_dist
                        )

                        if segment_geom and not segment_geom.is_empty:
                            segments.append({
                                'segment_id': segment_id,
                                'osm_id': road.get('osm_id', f'generated_{segment_id}'),
                                'name': road_name,
                                'road_class': road_class,
                                'highway_type': highway_type,
                                'length_miles': segment_geom.length / 5280,
                                'geometry': segment_geom
                            })
                            segment_id += 1
                    except Exception as e:
                        logger.warning(f"Error segmenting road {idx}: {e}")
                        continue

            if segment_id % 10000 == 0:
                logger.info(f"Created {segment_id} segments...")

        logger.info(f"✓ Created {len(segments)} road segments")

        # Create GeoDataFrame
        segments_gdf = gpd.GeoDataFrame(segments, crs="EPSG:3424")

        # Convert back to WGS84
        segments_gdf = segments_gdf.to_crs("EPSG:4326")

        return segments_gdf

    def _extract_segment(
        self,
        line: LineString,
        start_dist: float,
        end_dist: float
    ) -> Optional[LineString]:
        """
        Extract a segment from a LineString between two distances.

        Args:
            line: Original LineString
            start_dist: Start distance along line
            end_dist: End distance along line

        Returns:
            Segmented LineString or None
        """
        try:
            # Get start and end points
            start_point = line.interpolate(start_dist)
            end_point = line.interpolate(end_dist)

            # Find positions in original line
            coords = list(line.coords)

            # Build segment coordinates
            segment_coords = []

            # Add start point
            segment_coords.append(start_point.coords[0])

            # Add intermediate points
            accumulated_dist = 0
            for i in range(len(coords) - 1):
                p1 = coords[i]
                p2 = coords[i + 1]

                seg_length = ((p2[0] - p1[0])**2 + (p2[1] - p1[1])**2)**0.5
                next_dist = accumulated_dist + seg_length

                if start_dist < next_dist and accumulated_dist < end_dist:
                    if accumulated_dist > start_dist:
                        segment_coords.append(p1)
                    if next_dist < end_dist:
                        segment_coords.append(p2)

                accumulated_dist = next_dist

            # Add end point
            segment_coords.append(end_point.coords[0])

            # Create LineString
            if len(segment_coords) >= 2:
                return LineString(segment_coords)
            else:
                return None

        except Exception as e:
            logger.warning(f"Error extracting segment: {e}")
            return None

    def load_to_db(self, segments_gdf: gpd.GeoDataFrame, db: Session) -> int:
        """
        Load road segments to database.

        Args:
            segments_gdf: GeoDataFrame of road segments
            db: Database session

        Returns:
            Number of segments loaded
        """
        logger.info("Loading road segments to database...")

        loaded_count = 0

        for idx, seg in segments_gdf.iterrows():
            try:
                # Check if already exists
                existing = db.query(RoadSegment).filter(
                    RoadSegment.osm_id == str(seg['osm_id'])
                ).first()

                if existing:
                    continue

                # Create WKT geometry
                geom_wkt = f"SRID=4326;{seg.geometry.wkt}"

                # Create road segment
                road_segment = RoadSegment(
                    osm_id=str(seg['osm_id']),
                    name=seg['name'],
                    road_class=seg['road_class'],
                    highway_type=seg['highway_type'],
                    length_miles=seg['length_miles'],
                    geom=geom_wkt
                )

                db.add(road_segment)
                loaded_count += 1

                if loaded_count % 1000 == 0:
                    db.commit()
                    logger.info(f"Loaded {loaded_count} segments...")

            except Exception as e:
                logger.error(f"Error loading segment {idx}: {e}")
                continue

        db.commit()
        logger.info(f"✓ Successfully loaded {loaded_count} road segments")
        return loaded_count


def main():
    """Main ingestion workflow."""
    import argparse

    parser = argparse.ArgumentParser(description='Ingest OSM road network')
    parser.add_argument('--data-dir', type=str, default='./data',
                        help='Data directory (default: ./data)')
    parser.add_argument('--segment-length', type=float, default=0.1,
                        help='Segment length in miles (default: 0.1)')
    parser.add_argument('--download-only', action='store_true',
                        help='Only download, do not process')

    args = parser.parse_args()

    logger.info("Starting OSM road network ingestion...")

    # Initialize ingester
    ingester = OSMRoadIngester(data_dir=args.data_dir)

    # Download OSM data
    osm_file = ingester.download_osm_data()

    if args.download_only:
        logger.info("Download complete (--download-only specified)")
        return

    # Extract roads to GeoJSON
    geojson_file = ingester.extract_roads_geojson(osm_file)

    # Segment roads
    segments = ingester.segment_roads(geojson_file, args.segment_length)

    # Load to database
    db = SessionLocal()
    try:
        loaded = ingester.load_to_db(segments, db)
        logger.info(f"✓ Successfully loaded {loaded} road segments")
    finally:
        db.close()


if __name__ == '__main__':
    main()
