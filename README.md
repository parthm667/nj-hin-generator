# NJ High Injury Network Generator

Exploratory crash-network screening for New Jersey municipalities. Results require transportation-safety review and are not grant-certified assessments.

## Overview

This tool helps explore candidate high-crash road segments where usable source records are loaded by:

- Pulling and processing state crash data
- Running spatial analysis to assign crashes to road segments
- Performing statistical significance testing to identify high-risk corridors
- Generating maps and data tables formatted for Action Plan insertion

## Features

- **Automated Data Pipeline**: Ingests crash data, road networks, municipal boundaries, and census demographics
- **Statistical Analysis**: Count-based Poisson screening, separate severity ranking, and connected same-route corridors
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
- NJDOT measured public-road network (OpenStreetMap background tiles)
- NJGIN municipal boundaries
- US Census Bureau boundaries and demographics
- CDC Social Vulnerability Index

## Quick Start

Use [docs/DEPLOY.md](docs/DEPLOY.md) for the Vercel frontend and Railway/Render API. It covers schema initialization, required settings, and remaining real-data limitations. Earlier deployment reports describe historical states, not current readiness guarantees.

See [docs/DATA_PIPELINE.md](docs/DATA_PIPELINE.md) for ingestion and data limitations, and [docs/CORRECTNESS_AND_SAFEGUARDS.md](docs/CORRECTNESS_AND_SAFEGUARDS.md) for corrected statistics, anonymous-use safeguards, and verification. No sign-in is required; recent history is browser-local, while computations and result URLs remain server-side. Generated sample roads are artificial and do not follow the background street map.

### Prerequisites

- Python 3.11 or 3.12
- Node.js 22
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
