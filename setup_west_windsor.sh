#!/bin/bash
# West Windsor Township - Complete Setup Script

set -e  # Exit on error

echo "============================================================"
echo "West Windsor Township - Real Data Ingestion"
echo "============================================================"
echo ""

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check if we're in the right directory
if [ ! -f "docker-compose.yml" ]; then
    echo -e "${RED}Error: Must run from project root${NC}"
    exit 1
fi

# Step 1: Database Setup
echo -e "${YELLOW}[STEP 1/4] Setting up PostgreSQL database...${NC}"
echo ""

if command -v docker &> /dev/null; then
    echo "Using Docker for PostgreSQL..."
    docker-compose up -d postgres
    echo "Waiting for PostgreSQL to start..."
    sleep 10
else
    echo -e "${YELLOW}Docker not found. Please start PostgreSQL manually:${NC}"
    echo "  sudo systemctl start postgresql"
    echo "  sudo -u postgres psql -c \"CREATE DATABASE hin_db;\""
    echo "  sudo -u postgres psql -c \"CREATE USER hin_user WITH PASSWORD 'hin_password';\""
    echo "  sudo -u postgres psql -c \"GRANT ALL PRIVILEGES ON DATABASE hin_db TO hin_user;\""
    echo ""
    read -p "Press Enter after PostgreSQL is running..."
fi

# Step 2: Initialize Database Schema
echo ""
echo -e "${YELLOW}[STEP 2/4] Initializing database schema...${NC}"
echo ""

cd backend
python3 -c "from app.models.database import init_db; init_db()"
echo -e "${GREEN}✓ Database schema created${NC}"

# Step 3: Check for API Token
echo ""
echo -e "${YELLOW}[STEP 3/4] Checking API token...${NC}"
echo ""

if [ -z "$SOCRATA_API_TOKEN" ]; then
    echo -e "${YELLOW}Warning: No SOCRATA_API_TOKEN found${NC}"
    echo "Rate limits will apply (1000 requests/hour)"
    echo ""
    echo "To get a free token:"
    echo "  1. Visit: https://data.nj.gov/profile/app_tokens"
    echo "  2. Sign up and create a token"
    echo "  3. Export: export SOCRATA_API_TOKEN='your_token'"
    echo ""
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
else
    echo -e "${GREEN}✓ API token configured${NC}"
fi

# Step 4: Ingest Real Data
echo ""
echo -e "${YELLOW}[STEP 4/4] Ingesting West Windsor data...${NC}"
echo "This will take approximately 25-35 minutes"
echo ""

cd scripts

python3 ingest_all_real_data.py \
  --municipality "West Windsor" \
  --county "Mercer" \
  --start-year 2017 \
  --end-year 2021

# Success!
echo ""
echo "============================================================"
echo -e "${GREEN}✓ WEST WINDSOR SETUP COMPLETE!${NC}"
echo "============================================================"
echo ""
echo "Next steps:"
echo "  1. Start backend:"
echo "     cd backend"
echo "     uvicorn app.main:app --reload"
echo ""
echo "  2. In another terminal, start frontend:"
echo "     cd frontend"
echo "     npm install  # First time only"
echo "     npm start"
echo ""
echo "  3. Access application:"
echo "     Frontend: http://localhost:3000"
echo "     API Docs: http://localhost:8000/docs"
echo ""
echo "  4. Create analysis for West Windsor via UI"
echo ""
