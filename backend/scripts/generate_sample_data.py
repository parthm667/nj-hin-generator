"""
Sample data generator for NJ High Injury Network testing.

Generates realistic crash data, municipalities, and road networks for testing
the application without requiring access to real NJ data sources.
"""

import argparse
import json
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List
import math


BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "data" / "sample"


# Real NJ municipalities (sample of major ones)
NJ_MUNICIPALITIES = [
    {"name": "Princeton", "county": "Mercer", "lat": 40.3573, "lon": -74.6672},
    {"name": "West Windsor", "county": "Mercer", "lat": 40.2998, "lon": -74.6357},
    {"name": "Newark", "county": "Essex", "lat": 40.7357, "lon": -74.1724},
    {"name": "Jersey City", "county": "Hudson", "lat": 40.7178, "lon": -74.0431},
    {"name": "Trenton", "county": "Mercer", "lat": 40.2171, "lon": -74.7429},
]

# Road names for realistic data
ROAD_NAMES = [
    "Main Street", "Route 1", "Route 27", "Nassau Street", "Washington Road",
    "Quaker Bridge Road", "Harrison Street", "Alexander Road", "Clarksville Road",
    "Province Line Road", "Southfield Road", "Meadow Road", "Cranbury Road"
]

# Crash severity distribution (realistic percentages)
SEVERITY_DISTRIBUTION = {
    "fatal": 0.01,  # 1%
    "serious_injury": 0.05,  # 5%
    "minor_injury": 0.25,  # 25%
    "property_damage": 0.69  # 69%
}


def generate_municipalities() -> List[Dict]:
    """Generate municipality records with boundaries."""
    municipalities = []

    for idx, muni in enumerate(NJ_MUNICIPALITIES, start=1):
        # Create a simple square boundary around the center point
        # Real boundaries would be more complex
        offset = 0.05  # ~3 miles
        municipalities.append({
            "muni_id": idx,
            "name": muni["name"],
            "county": muni["county"],
            "muni_code": f"NJ{idx:04d}",
            "center_lat": muni["lat"],
            "center_lon": muni["lon"],
            "boundary": {
                "type": "Polygon",
                "coordinates": [[
                    [muni["lon"] - offset, muni["lat"] - offset],
                    [muni["lon"] + offset, muni["lat"] - offset],
                    [muni["lon"] + offset, muni["lat"] + offset],
                    [muni["lon"] - offset, muni["lat"] + offset],
                    [muni["lon"] - offset, muni["lat"] - offset]
                ]]
            }
        })

    return municipalities


def generate_road_segments(municipality: Dict, num_segments: int = 50) -> List[Dict]:
    """Generate road network segments for a municipality."""
    segments = []
    center_lat = municipality["center_lat"]
    center_lon = municipality["center_lon"]

    road_types = {
        "motorway": 0.05,
        "trunk": 0.05,
        "primary": 0.15,
        "secondary": 0.20,
        "tertiary": 0.25,
        "residential": 0.30
    }

    for i in range(num_segments):
        # Random road type based on distribution
        road_type = random.choices(
            list(road_types.keys()),
            weights=list(road_types.values())
        )[0]

        # Classify road
        if road_type in ["motorway", "trunk", "primary"]:
            road_class = "arterial"
        elif road_type in ["secondary", "tertiary"]:
            road_class = "collector"
        else:
            road_class = "local"

        # Generate random segment
        start_offset_lat = random.uniform(-0.04, 0.04)
        start_offset_lon = random.uniform(-0.04, 0.04)

        # Segment length (0.05 to 0.25 miles in degrees, roughly)
        segment_length = random.uniform(0.001, 0.005)
        angle = random.uniform(0, 2 * math.pi)

        start_lat = center_lat + start_offset_lat
        start_lon = center_lon + start_offset_lon
        end_lat = start_lat + segment_length * math.sin(angle)
        end_lon = start_lon + segment_length * math.cos(angle)

        # Calculate length in miles (very rough approximation)
        length_miles = segment_length * 69  # 1 degree ≈ 69 miles

        segments.append({
            # Stable and unique across the complete synthetic dataset.
            "osm_id": 1000000 + (municipality["muni_id"] * 1000) + i,
            "road_name": random.choice(ROAD_NAMES),
            "road_type": road_type,
            "road_class": road_class,
            "length_miles": round(length_miles, 3),
            "muni_id": municipality["muni_id"],
            "geometry": {
                "type": "LineString",
                "coordinates": [[start_lon, start_lat], [end_lon, end_lat]]
            }
        })

    return segments


def weighted_choice(choices: Dict) -> str:
    """Make a weighted random choice."""
    items = list(choices.items())
    weights = [w for _, w in items]
    return random.choices([k for k, _ in items], weights=weights)[0]


def generate_crashes(
    municipality: Dict,
    road_segments: List[Dict],
    start_year: int = 2017,
    end_year: int = 2021,
    crashes_per_year: int = 100
) -> List[Dict]:
    """Generate crash records for a municipality."""
    crashes = []
    crash_id = 1

    for year in range(start_year, end_year + 1):
        for _ in range(crashes_per_year):
            # Random date in year
            start_date = datetime(year, 1, 1)
            end_date = datetime(year, 12, 31)
            delta = end_date - start_date
            random_days = random.randint(0, delta.days)
            crash_date = start_date + timedelta(days=random_days)

            # Random time
            crash_time = f"{random.randint(0, 23):02d}:{random.randint(0, 59):02d}"

            # Select random road segment
            segment = random.choice(road_segments)

            # Generate crash point near segment
            seg_coords = segment["geometry"]["coordinates"]
            # Pick random point along segment
            t = random.random()
            crash_lon = seg_coords[0][0] + t * (seg_coords[1][0] - seg_coords[0][0])
            crash_lat = seg_coords[0][1] + t * (seg_coords[1][1] - seg_coords[0][1])

            # Add small random offset
            crash_lon += random.gauss(0, 0.0001)
            crash_lat += random.gauss(0, 0.0001)

            # Severity based on distribution
            severity = weighted_choice(SEVERITY_DISTRIBUTION)

            # Pedestrian and bicycle involvement (realistic rates)
            ped_involved = random.random() < 0.05  # 5% involve pedestrians
            bike_involved = random.random() < 0.03  # 3% involve bicycles

            # Higher injury rates for ped/bike crashes
            if ped_involved or bike_involved:
                if random.random() < 0.3:  # 30% serious for ped/bike
                    severity = random.choice(["serious_injury", "fatal"])

            crashes.append({
                "crash_id": crash_id,
                "external_id": f"SAMPLE-{municipality['muni_id']}-{year}-{crash_id:06d}",
                "crash_date": crash_date.strftime("%Y-%m-%d"),
                "crash_time": crash_time,
                "severity": severity,
                "ped_involved": ped_involved,
                "bike_involved": bike_involved,
                "muni_id": municipality["muni_id"],
                "road_name": segment["road_name"],
                "latitude": crash_lat,
                "longitude": crash_lon,
                "geocode_quality": random.choice(["high", "high", "medium", "low"]),
                "weather_condition": random.choice(["clear", "rain", "snow", "fog", "clear", "clear"]),
                "light_condition": random.choice(["daylight", "daylight", "dark", "dusk", "dawn"])
            })

            crash_id += 1

    return crashes


def generate_census_tracts(municipality: Dict, num_tracts: int = 5) -> List[Dict]:
    """Generate census tract data for a municipality."""
    tracts = []
    center_lat = municipality["center_lat"]
    center_lon = municipality["center_lon"]

    for i in range(num_tracts):
        tract_id = f"34{municipality['muni_id']:03d}{i:04d}00"

        # Create small rectangular tract
        offset_lat = random.uniform(-0.03, 0.03)
        offset_lon = random.uniform(-0.03, 0.03)
        size = 0.02

        tracts.append({
            "tract_id": tract_id,
            "muni_id": municipality["muni_id"],
            "county_fips": "34021",  # Mercer County example
            "total_population": random.randint(1000, 5000),
            "median_income": random.randint(40000, 120000),
            "pct_below_poverty": round(random.uniform(2, 25), 1),
            "pct_no_vehicle": round(random.uniform(1, 15), 1),
            "pct_minority": round(random.uniform(10, 70), 1),
            "svi_score": round(random.uniform(0, 100), 1),
            "svi_percentile": round(random.uniform(0, 100), 1),
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [center_lon + offset_lon, center_lat + offset_lat],
                    [center_lon + offset_lon + size, center_lat + offset_lat],
                    [center_lon + offset_lon + size, center_lat + offset_lat + size],
                    [center_lon + offset_lon, center_lat + offset_lat + size],
                    [center_lon + offset_lon, center_lat + offset_lat]
                ]]
            }
        })

    return tracts


def generate_full_dataset(output_dir: Path | str = DEFAULT_OUTPUT_DIR, seed: int = 42):
    """Generate a deterministic, explicitly synthetic sample dataset."""
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    random.seed(seed)

    print(f"Generating synthetic sample NJ dataset (seed={seed})...")

    # Generate municipalities
    print("- Generating municipalities...")
    municipalities = generate_municipalities()

    with (output_dir / "municipalities.json").open("w", encoding="utf-8") as f:
        json.dump(municipalities, f, indent=2)

    print(f"  Generated {len(municipalities)} municipalities")

    # Generate data for each municipality
    all_segments = []
    all_crashes = []
    all_tracts = []

    for muni in municipalities:
        print(f"\n- Generating data for {muni['name']}...")

        # Road segments
        segments = generate_road_segments(muni, num_segments=50)
        all_segments.extend(segments)
        print(f"  {len(segments)} road segments")

        # Crashes (fewer for smaller towns, more for cities)
        crashes_per_year = 200 if muni["name"] in ["Newark", "Jersey City"] else 100
        crashes = generate_crashes(muni, segments, crashes_per_year=crashes_per_year)
        all_crashes.extend(crashes)
        print(f"  {len(crashes)} crashes (2017-2021)")

        # Census tracts
        tracts = generate_census_tracts(muni, num_tracts=5)
        all_tracts.extend(tracts)
        print(f"  {len(tracts)} census tracts")

    # Save all data
    with (output_dir / "road_segments.json").open("w", encoding="utf-8") as f:
        json.dump(all_segments, f, indent=2)

    with (output_dir / "crashes.json").open("w", encoding="utf-8") as f:
        json.dump(all_crashes, f, indent=2)

    with (output_dir / "census_tracts.json").open("w", encoding="utf-8") as f:
        json.dump(all_tracts, f, indent=2)

    print(f"\nSynthetic sample dataset generated in {output_dir}/")
    print(f"  - {len(municipalities)} municipalities")
    print(f"  - {len(all_segments)} road segments")
    print(f"  - {len(all_crashes)} crashes")
    print(f"  - {len(all_tracts)} census tracts")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate deterministic synthetic data for local development."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed (default: 42)")
    return parser.parse_args()


if __name__ == "__main__":
    cli_args = parse_args()
    generate_full_dataset(cli_args.output_dir, cli_args.seed)
