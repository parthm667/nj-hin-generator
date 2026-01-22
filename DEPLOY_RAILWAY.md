# Deploy to Railway.app ($5/month)

**Cost**: $5/month (you get $5 free credit to start = 1 month free!)
**Time**: 10 minutes
**Perfect for**: Production deployment for West Windsor

---

## Why Railway?

- ✅ **Cheapest permanent option** ($5/month total)
- ✅ **Easiest deployment** (3 clicks, seriously)
- ✅ **Always on** (no sleep like free tiers)
- ✅ **Auto-scaling** (handles traffic spikes)
- ✅ **PostgreSQL included** in price
- ✅ **Auto HTTPS** and custom domains
- ✅ **GitHub auto-deploy** (push to deploy)

---

## Step 1: Create Railway Account

1. Go to https://railway.app
2. Click "Login"
3. Choose "Login with GitHub"
4. Authorize Railway

**You get $5 free credit** (enough for 1 month)

**Time**: 1 minute

---

## Step 2: Create New Project

1. Click "New Project"
2. Select "Deploy from GitHub repo"
3. Choose your `nj-hin-generator` repository
4. Railway will analyze your repo and auto-detect:
   - Backend (Python/FastAPI)
   - Frontend (React)

**Time**: 1 minute

---

## Step 3: Add PostgreSQL Database

1. In your project dashboard, click "New"
2. Select "Database"
3. Choose "PostgreSQL"
4. Railway automatically creates and connects it

5. Click on the PostgreSQL service
6. Go to "Variables" tab
7. Note the connection details (auto-configured)

**Time**: 2 minutes

---

## Step 4: Enable PostGIS Extension

1. Click on PostgreSQL service
2. Click "Data" tab
3. Click "Query"
4. Run:
   ```sql
   CREATE EXTENSION IF NOT EXISTS postgis;
   ```
5. Click "Execute"

**Time**: 1 minute

---

## Step 5: Configure Backend Service

Railway auto-detects your backend, but let's configure it:

1. Click on your backend service
2. Go to "Settings" tab
3. Set:
   - **Root Directory**: `backend`
   - **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - **Build Command**: (leave default, Railway auto-detects)

4. Go to "Variables" tab
5. Add these (Railway auto-fills DATABASE_URL):
   ```
   PYTHONPATH=/app
   ```

6. Click "Deploy" if needed

**Time**: 2 minutes

---

## Step 6: Configure Frontend Service

1. Click on your frontend service (or add new service if not detected)
2. Go to "Settings" tab
3. Set:
   - **Root Directory**: `frontend`
   - **Build Command**: `npm install && npm run build`
   - **Start Command**: `npx serve -s build -l $PORT`

4. Go to "Variables" tab
5. Add:
   ```
   REACT_APP_API_URL=${{backend.RAILWAY_PUBLIC_DOMAIN}}/api
   ```
   (Railway auto-substitutes the backend URL)

6. Click "Deploy"

**Time**: 2 minutes

---

## Step 7: Generate Public URLs

1. Click on **Backend** service
2. Go to "Settings" tab
3. Scroll to "Networking"
4. Click "Generate Domain"
5. Copy the URL (like `nj-hin-backend-production.up.railway.app`)

6. Click on **Frontend** service
7. Go to "Settings" tab
8. Scroll to "Networking"
9. Click "Generate Domain"
10. Copy the URL (like `nj-hin-frontend-production.up.railway.app`)

**Time**: 1 minute

---

## Step 8: Ingest West Windsor Data

### Option A: Railway CLI (Recommended)

1. Install Railway CLI:
   ```bash
   # Mac/Linux
   curl -fsSL https://railway.app/install.sh | sh

   # Or with npm
   npm install -g @railway/cli
   ```

2. Login:
   ```bash
   railway login
   ```

3. Link to your project:
   ```bash
   cd /home/user/nj-hin-generator
   railway link
   ```

4. Run ingestion:
   ```bash
   railway run python backend/scripts/ingest_all_real_data.py \
     --municipality "West Windsor" \
     --county "Mercer" \
     --start-year 2017 \
     --end-year 2021
   ```

### Option B: From Local Machine

1. Get database URL from Railway dashboard:
   - Click on PostgreSQL service
   - Go to "Connect" tab
   - Copy "PostgreSQL Connection URL"

2. Set environment variable:
   ```bash
   export DATABASE_URL="<paste-url-here>"
   ```

3. Run ingestion locally:
   ```bash
   cd backend
   pip install -r requirements.txt
   cd scripts
   python ingest_all_real_data.py \
     --municipality "West Windsor" \
     --county "Mercer" \
     --start-year 2017 \
     --end-year 2021
   ```

**Time**: 30 minutes (data download)

---

## Step 9: Test Your Deployment

1. Open your frontend URL
2. Select "West Windsor — Mercer County"
3. Years: 2017 - 2021
4. Click "Run Analysis"
5. Wait for completion
6. Download PDF report
7. ✅ Success!

**Time**: 10 minutes (analysis)

---

## Features You Get

### Always On
- ✅ No sleep/wake delays
- ✅ Instant response
- ✅ Professional experience

### Auto-Deploy from GitHub
- ✅ Push to GitHub → Auto deploys
- ✅ No manual deployment
- ✅ Preview deployments for PRs

### Built-in Metrics
- ✅ CPU usage
- ✅ Memory usage
- ✅ Request logs
- ✅ Database metrics

### Custom Domain (Optional)
- ✅ Use your own domain
- ✅ Auto HTTPS
- ✅ Free SSL certificate

---

## Cost Breakdown

**Included in $5/month**:
- PostgreSQL database (8 GB storage)
- Backend web service
- Frontend web service
- 500 hours of runtime (enough for always-on)
- Unlimited bandwidth
- Auto SSL/HTTPS
- GitHub auto-deploy

**Additional costs** (optional):
- $0 if usage stays under 500 hours/month
- ~$1-2/month if you go over (unlikely for West Windsor)

**You get $5 free credit** to start, so first month is FREE!

---

## Usage Monitoring

Railway shows real-time costs:

1. Go to project dashboard
2. Click "Usage" tab
3. See:
   - Current month cost
   - Remaining credit
   - Estimated month-end cost

**Typical usage for West Windsor**:
- ~$4-5/month (one municipality)
- Well within the $5/month budget

---

## Scaling (If Needed)

If West Windsor wants to add more municipalities:

**5 municipalities**: $5/month (still fits)
**10 municipalities**: $7-8/month
**All Mercer County**: $10-12/month
**Statewide**: $20-30/month

Still MUCH cheaper than consultant ($15K+)!

---

## Comparison to Render.com Free

| Feature | Railway ($5/mo) | Render Free |
|---------|-----------------|-------------|
| Database | Permanent | 90 days only |
| Sleep/Wake | Never sleeps | Sleeps after 15 min |
| Performance | Fast always | Slow on wake |
| Storage | 8 GB | 1 GB |
| Best for | Production | Demo/Testing |

**Recommendation**:
- Use **Render free** for initial demo to West Windsor
- Upgrade to **Railway $5/mo** when they approve
- Best value for permanent deployment

---

## Auto-Deployment Workflow

After setup, your workflow is:

1. Make code changes locally
2. Commit to GitHub:
   ```bash
   git add .
   git commit -m "Add new feature"
   git push
   ```
3. Railway automatically:
   - Detects the push
   - Builds new version
   - Runs tests
   - Deploys to production
4. New version live in 2-3 minutes

**Zero manual deployment!**

---

## Adding Custom Domain

If West Windsor wants `hin.westwindsor.org`:

1. In Railway, click on Frontend service
2. Go to "Settings" → "Networking"
3. Click "Custom Domain"
4. Enter: `hin.westwindsor.org`
5. Railway gives you DNS records
6. Add DNS records to West Windsor's domain
7. Wait 5-10 minutes for DNS propagation
8. ✅ Custom domain works with auto HTTPS!

**Cost**: $0 (included in $5/month)

---

## Environment Variables (Reference)

Railway auto-configures most variables, but here's the full list:

**Backend**:
```
DATABASE_URL=<auto-configured>
POSTGRES_HOST=<auto-configured>
POSTGRES_PORT=<auto-configured>
POSTGRES_DB=<auto-configured>
POSTGRES_USER=<auto-configured>
POSTGRES_PASSWORD=<auto-configured>
PYTHONPATH=/app
PORT=<auto-configured>
```

**Frontend**:
```
REACT_APP_API_URL=${{backend.RAILWAY_PUBLIC_DOMAIN}}/api
PORT=<auto-configured>
```

---

## Troubleshooting

### Build fails

Check:
- Root directory is set correctly (`backend` or `frontend`)
- requirements.txt is in backend directory
- package.json is in frontend directory

### Can't connect to database

Check:
- PostGIS extension is enabled
- DATABASE_URL is in backend variables
- Database service is running (check Railway dashboard)

### Frontend can't reach backend

Check:
- REACT_APP_API_URL is set correctly
- Backend has public domain generated
- Backend is running (check logs)

### Data ingestion fails

Check:
- Internet access from Railway (should work)
- Database has space (check usage tab)
- Try with `--max-crashes 100` first

---

## Support

Railway has great support:

- **Discord**: https://discord.gg/railway
- **Docs**: https://docs.railway.app
- **Status**: https://status.railway.app

Response time: Usually within hours

---

## Migration Path

**Now → 3 months**: Use Render.com FREE for demo

**After approval**: Migrate to Railway $5/month:
1. Export data from Render
2. Deploy to Railway (10 minutes)
3. Import data
4. Switch DNS
5. Done!

Or just start with Railway from day 1 if West Windsor approves budget.

---

## Cost Comparison

**Railway.app**: $5/month = $60/year

**vs. Alternatives**:
- Consultant: $15,000-$30,000 ❌
- DigitalOcean: $72/year (but more setup) ⚠️
- AWS/Azure: $100-300/year (complex) ❌
- Render paid: $168/year ($14/mo) ⚠️

**Railway is the sweet spot**: Cheap + Easy + Reliable

---

## Ready to Deploy?

1. Sign up at https://railway.app
2. Follow steps above
3. Deploy in 10 minutes
4. Share with West Windsor
5. ✅ Done!

**Total time**: 10 minutes setup + 30 minutes data ingestion = 40 minutes to production! 🚀
