# 🚀 QUICK DEPLOY - Railway.app

> Historical deployment guide. Use [docs/DEPLOY.md](docs/DEPLOY.md) for the current Vercel + container setup, required CORS settings, and explicit schema initialization. Pricing and readiness claims below have not been revalidated.


**Ultra-fast reference** - See DEPLOY_NOW.md for detailed steps

---

## 1. Sign Up (2 min)
- Go to: https://railway.app
- Click "Login with GitHub"
- Authorize Railway

## 2. Create Project (1 min)
- Click "New Project"
- "Deploy from GitHub repo"
- Select "nj-hin-generator"

## 3. Add PostgreSQL (2 min)
- Click "+ New" → "Database" → "PostgreSQL"
- Click on PostgreSQL → "Data" → "Query"
- Run: `CREATE EXTENSION IF NOT EXISTS postgis;`

## 4. Configure Backend (3 min)
**Settings**:
- Root Directory: `backend`
- Start Command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips='*'`

**Variables**:
- Add: `PYTHONPATH` = `/app`

**Networking**:
- Generate Domain → Copy URL

## 5. Configure Frontend (3 min)
**Settings**:
- Root Directory: `frontend`
- Build: `npm install && npm run build`
- Start: `npx serve -s build -l $PORT`

**Variables**:
- Add: `REACT_APP_API_URL` = `https://[backend-url]/api`

**Networking**:
- Generate Domain

## 6. Load Data (30 min)
```bash
# On your local machine
cd backend
pip3 install -r requirements.txt

# Get DATABASE_URL from Railway PostgreSQL → Connect tab
export DATABASE_URL="postgres://..."

cd scripts
python3 ingest_all_real_data.py \
  --municipality "West Windsor" \
  --county "Mercer" \
  --start-year 2017 \
  --end-year 2021
```

## 7. Test (5 min)
- Open frontend URL
- Select "West Windsor"
- Run analysis
- Download PDF
- ✅ Done!

---

**Cost**: $0 first month (includes $5 credit)
**Time**: 45 minutes total (15 min setup + 30 min data)
**Questions**: Check DEPLOY_NOW.md for detailed help
