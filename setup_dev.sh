#!/bin/bash

# Development setup script for NJ High Injury Network Generator
# This script sets up the entire development environment with sample data

set -e  # Exit on error

echo "============================================================"
echo "NJ High Injury Network Generator - Development Setup"
echo "============================================================"
echo

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Step 1: Generate sample data
echo -e "${YELLOW}[1/6] Generating sample data...${NC}"
cd backend
python3 scripts/generate_sample_data.py
echo -e "${GREEN}✓ Sample data generated${NC}"
echo

# Step 2: Start database
echo -e "${YELLOW}[2/6] Starting PostgreSQL database with PostGIS...${NC}"
cd ..
docker-compose up -d db
echo -e "${GREEN}✓ Database starting...${NC}"
echo

# Step 3: Wait for database to be ready
echo -e "${YELLOW}[3/6] Waiting for database to be healthy...${NC}"
echo "This may take 10-30 seconds..."
max_wait=60
counter=0
while [ $counter -lt $max_wait ]; do
    if docker-compose exec -T db pg_isready -U hin_user -d nj_hin_db > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Database is ready${NC}"
        break
    fi
    sleep 2
    counter=$((counter + 2))
    echo -n "."
done

if [ $counter -ge $max_wait ]; then
    echo -e "\n${YELLOW}⚠ Database health check timed out, but continuing...${NC}"
fi
echo

# Step 4: Create .env file if it doesn't exist
echo -e "${YELLOW}[4/6] Setting up environment configuration...${NC}"
if [ ! -f backend/.env ]; then
    cat > backend/.env << EOF
# Database Configuration
DATABASE_URL=postgresql://hin_user:hin_password@localhost:5432/nj_hin_db
DATABASE_HOST=localhost
DATABASE_PORT=5432
DATABASE_NAME=nj_hin_db
DATABASE_USER=hin_user
DATABASE_PASSWORD=hin_password

# API Configuration
API_HOST=0.0.0.0
API_PORT=8000
API_RELOAD=True
CORS_ORIGINS=http://localhost:3000,http://localhost:5173

# Application
APP_NAME=NJ High Injury Network Generator
APP_VERSION=0.1.0
DEBUG=True

# Analysis Configuration
DEFAULT_ANALYSIS_YEARS=5
CRASH_SNAP_DISTANCE_METERS=50.0
SEGMENT_LENGTH_MILES=0.1
SIGNIFICANCE_THRESHOLD=0.05

# Severity Weights
WEIGHT_FATAL=10
WEIGHT_SERIOUS_INJURY=5
WEIGHT_MINOR_INJURY=3
WEIGHT_PROPERTY_DAMAGE=1
EOF
    echo -e "${GREEN}✓ Created backend/.env${NC}"
else
    echo -e "${GREEN}✓ backend/.env already exists${NC}"
fi
echo

# Step 5: Load sample data into database
echo -e "${YELLOW}[5/6] Loading sample data into database...${NC}"
cd backend
python3 scripts/load_sample_data.py
echo -e "${GREEN}✓ Sample data loaded${NC}"
echo

# Step 6: Verify setup
echo -e "${YELLOW}[6/6] Verifying setup...${NC}"
cd ..

# Check database has data
MUNI_COUNT=$(docker-compose exec -T db psql -U hin_user -d nj_hin_db -t -c "SELECT COUNT(*) FROM municipalities;" 2>/dev/null || echo "0")
CRASH_COUNT=$(docker-compose exec -T db psql -U hin_user -d nj_hin_db -t -c "SELECT COUNT(*) FROM crashes;" 2>/dev/null || echo "0")

echo "Database contents:"
echo "  - Municipalities: $MUNI_COUNT"
echo "  - Crashes: $CRASH_COUNT"

if [ "$MUNI_COUNT" -gt "0" ] && [ "$CRASH_COUNT" -gt "0" ]; then
    echo -e "${GREEN}✓ Database verification passed${NC}"
else
    echo -e "${YELLOW}⚠ Database may not be fully populated${NC}"
fi
echo

echo "============================================================"
echo -e "${GREEN}✓ Development environment setup complete!${NC}"
echo "============================================================"
echo
echo "Next steps:"
echo
echo "1. Start the backend API:"
echo "   cd backend"
echo "   uvicorn app.main:app --reload"
echo
echo "2. In another terminal, test the API:"
echo "   curl http://localhost:8000/health"
echo "   curl http://localhost:8000/api/municipalities"
echo
echo "3. Create a test analysis:"
echo "   curl -X POST http://localhost:8000/api/analysis \\"
echo "     -H \"Content-Type: application/json\" \\"
echo "     -d '{\"muni_id\": 1, \"config\": {\"start_year\": 2017, \"end_year\": 2021}}'"
echo
echo "4. View API documentation:"
echo "   http://localhost:8000/docs"
echo
echo "Database connection:"
echo "  Host: localhost"
echo "  Port: 5432"
echo "  Database: nj_hin_db"
echo "  User: hin_user"
echo "  Password: hin_password"
echo

