# NJ High Injury Network Generator

Automated High Injury Network identification for New Jersey municipalities, designed to support Safe Streets and Roads for All (SS4A) grant applications.

## Overview

This tool automatically identifies statistically significant high-crash road segments for any New Jersey municipality. It eliminates the technical barrier that prevents small towns from applying for federal safety grants by:

- Pulling and processing state crash data
- Running spatial analysis to assign crashes to road segments
- Performing statistical significance testing to identify high-risk corridors
- Generating maps and data tables formatted for Action Plan insertion

## Features

- **Automated Data Pipeline**: Ingests crash data, road networks, municipal boundaries, and census demographics
- **Statistical Analysis**: Uses Poisson-based significance testing to identify true high-crash corridors
- **Equity Overlay**: Identifies high-injury segments in vulnerable communities
- **Web Interface**: Simple UI for selecting municipalities and viewing results
- **Export Capabilities**: Download maps and data as GeoJSON, CSV, and PDF

## Technology Stack

### Backend
- **FastAPI**: Modern Python web framework
- **PostgreSQL + PostGIS**: Spatial database
- **SQLAlchemy + GeoAlchemy2**: ORM with spatial support
- **GeoPandas**: Geospatial data processing
- **SciPy**: Statistical analysis

### Frontend
- **React**: UI framework
- **Leaflet**: Interactive mapping
- **TanStack Query**: Data fetching and caching

### Data Sources
- NJ DOT Crash Data (NJTR-1 reports)
- OpenStreetMap road network
- US Census Bureau boundaries and demographics
- CDC Social Vulnerability Index

## Quick Start

### Prerequisites

- Python 3.9+
- Node.js 16+
- PostgreSQL 15+ with PostGIS extension

### Installation

See [docs/SETUP.md](docs/SETUP.md) for detailed setup instructions.

## Usage

1. Open web interface
2. Select municipality
3. Choose analysis years
4. View results on interactive map
5. Download reports and data

See [docs/USER_GUIDE.md](docs/USER_GUIDE.md) for detailed usage instructions.

## Methodology

- **Crash Assignment**: Spatial snapping with configurable distance threshold
- **Statistical Testing**: Poisson significance test with road class baseline rates
- **Corridor Grouping**: Network connectivity analysis
- **Equity Analysis**: Census tract vulnerability overlay

See [docs/METHODOLOGY.md](docs/METHODOLOGY.md) for technical details.

## License

MIT License - see LICENSE file

## Acknowledgments

Built for West Windsor Bicycle and Pedestrian Alliance
Inspired by Vision Zero High Injury Network methodologies