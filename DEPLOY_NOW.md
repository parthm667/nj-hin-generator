# Deploy to Railway RIGHT NOW - Step by Step

> Historical deployment guide. Use [docs/DEPLOY.md](docs/DEPLOY.md) for the current Vercel + container setup, required CORS settings, and explicit schema initialization. Pricing and readiness claims below have not been revalidated.


**Time**: 15 minutes to live deployment
**Cost**: FREE for first month ($5 credit included)

Follow these EXACT steps. I'll tell you exactly what to click.

---

## ✅ Step 1: Create Railway Account (2 minutes)

1. Open your browser
2. Go to: **https://railway.app**
3. Click the **"Login"** button (top right)
4. Click **"Login with GitHub"**
5. Click **"Authorize Railway"** on GitHub
6. ✅ You're in! You should see the Railway dashboard

---

## ✅ Step 2: Create New Project (1 minute)

1. Click the big **"New Project"** button
2. Click **"Deploy from GitHub repo"**
3. If asked, click **"Configure GitHub App"**
4. Select **"Only select repositories"**
5. Choose your **"nj-hin-generator"** repository
6. Click **"Install & Authorize"**
7. Back on Railway, select **"nj-hin-generator"** from the list
8. Railway will start analyzing your repo...

---

## ✅ Step 3: Add PostgreSQL Database (2 minutes)

1. In your project, click **"+ New"** (top right)
2. Select **"Database"**
3. Click **"Add PostgreSQL"**
4. Wait ~30 seconds for it to provision
5. You'll see a purple PostgreSQL icon appear
6. Click on the **PostgreSQL service**
7. Click **"Variables"** tab
8. You'll see DATABASE_URL and other variables (Railway auto-created these)
9. Click **"Data"** tab
10. Click **"Query"** button
11. In the query box, type:
    ```sql
    CREATE EXTENSION IF NOT EXISTS postgis;
    ```
12. Click **"Run"**
13. You should see: `CREATE EXTENSION`
14. ✅ Database ready with PostGIS!

---

## ✅ Step 4: Configure Backend Service (3 minutes)

Railway should have auto-detected your backend. Let's configure it:

1. Click on your **backend** service (should say "nj-hin-generator" or similar)
2. Click **"Settings"** tab
3. Scroll down to **"Service Settings"**
4. Set **Root Directory**: `backend`
5. Set **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips='*'`
6. Click **"Variables"** tab
7. Click **"+ New Variable"**
8. Add this variable:
   - **Variable**: `PYTHONPATH`
   - **Value**: `/app`
9. Click **"Add"**
10. The DATABASE_URL should already be there (Railway auto-linked it)
11. Click **"Deployments"** tab
12. Click **"Deploy"** if it hasn't started automatically
13. Wait 3-5 minutes for build to complete
14. ✅ Backend deploying!

---

## ✅ Step 5: Configure Frontend Service (3 minutes)

1. If Railway didn't auto-detect frontend, click **"+ New"** → **"GitHub Repo"**
2. Select same repo, but we'll configure it for frontend
3. Click on the **frontend** service
4. Click **"Settings"** tab
5. Set **Root Directory**: `frontend`
6. Set **Build Command**: `npm install && npm run build`
7. Set **Start Command**: `npx serve -s build -l $PORT`
8. Click **"Variables"** tab
9. Click **"+ New Variable"**
10. We need to add the backend URL, but first let's get it...

---

## ✅ Step 6: Get Backend URL (1 minute)

1. Click on your **backend** service
2. Click **"Settings"** tab
3. Scroll to **"Networking"** section
4. Click **"Generate Domain"**
5. Copy the URL it gives you (something like `nj-hin-backend-production.up.railway.app`)
6. ✅ Keep this URL handy!

---

## ✅ Step 7: Set Frontend Environment Variable (1 minute)

1. Go back to **frontend** service
2. Click **"Variables"** tab
3. Click **"+ New Variable"**
4. Add:
   - **Variable**: `REACT_APP_API_URL`
   - **Value**: `https://<your-backend-url-from-step-6>/api`
   - Example: `https://nj-hin-backend-production.up.railway.app/api`
5. Click **"Add"**
6. Click **"Deployments"** tab
7. Click **"Deploy"**
8. Wait 3-5 minutes for build
9. ✅ Frontend deploying!

---

## ✅ Step 8: Generate Frontend URL (1 minute)

1. Click on **frontend** service
2. Click **"Settings"** tab
3. Scroll to **"Networking"**
4. Click **"Generate Domain"**
5. Copy the URL (something like `nj-hin-frontend-production.up.railway.app`)
6. ✅ This is your public URL!

---

## ✅ Step 9: Test Your Deployment (1 minute)

1. Open the frontend URL in your browser
2. You should see the NJ HIN Generator homepage!
3. ✅ It works!

**BUT WAIT** - You don't have data yet. Let's load West Windsor data...

---

## ✅ Step 10: Load West Windsor Data (30 minutes)

Now we need to ingest the crash data. You have 2 options:

### Option A: From Your Local Machine (Easier)

1. On your local computer, open terminal
2. Navigate to your project:
   ```bash
   cd /path/to/nj-hin-generator
   ```

3. Install Python dependencies:
   ```bash
   cd backend
   pip3 install -r requirements.txt
   ```

4. Get your Railway database URL:
   - Go to Railway dashboard
   - Click on **PostgreSQL** service
   - Click **"Connect"** tab
   - Copy the **"Postgres Connection URL"**

5. Set environment variable:
   ```bash
   export DATABASE_URL="<paste-the-url-here>"
   ```

6. Run data ingestion:
   ```bash
   cd scripts
   python3 ingest_all_real_data.py \
     --municipality "West Windsor" \
     --county "Mercer" \
     --start-year 2017 \
     --end-year 2021
   ```

7. Wait 20-30 minutes (grab coffee ☕)
8. ✅ Data loaded!

### Option B: Install Railway CLI

1. Install Railway CLI:
   ```bash
   # Mac/Linux
   curl -fsSL https://railway.app/install.sh | sh

   # Windows (PowerShell)
   iwr https://railway.app/install.ps1 | iex
   ```

2. Login:
   ```bash
   railway login
   ```

3. Link to project:
   ```bash
   cd /path/to/nj-hin-generator
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

5. Wait 20-30 minutes
6. ✅ Data loaded!

---

## ✅ Step 11: Test Full System (5 minutes)

1. Go to your frontend URL
2. Click on municipality dropdown
3. Select **"West Windsor — Mercer County"**
4. Set years: **2017** to **2021**
5. Click **"Run Analysis"**
6. Wait 5-10 minutes for analysis to complete
7. You should see the interactive map with crash points!
8. Click **"Download PDF Report"**
9. PDF downloads! 🎉
10. ✅ **EVERYTHING WORKS!**

---

## 🎉 You're Live!

**Your URLs**:
- Frontend: `https://nj-hin-frontend-production.up.railway.app`
- Backend API: `https://nj-hin-backend-production.up.railway.app`
- API Docs: `https://nj-hin-backend-production.up.railway.app/docs`

**Share with West Windsor**: Just send them the frontend URL!

---

## 💰 Cost Check

1. Go to Railway dashboard
2. Click **"Usage"** tab
3. You should see:
   - $5.00 free credit
   - ~$0.50 used so far
   - Estimated: $4-5/month

You're good for the **first month for FREE**!

---

## ⚠️ Troubleshooting

### Backend won't build

Check:
- Root directory is set to `backend`
- Start command is exactly: `uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips='*'`
- PYTHONPATH variable is set to `/app`

### Frontend can't connect to backend

Check:
- REACT_APP_API_URL ends with `/api`
- REACT_APP_API_URL starts with `https://`
- Backend has public domain generated

### Data ingestion fails

Check:
- DATABASE_URL is set correctly
- PostGIS extension is enabled (`CREATE EXTENSION postgis;`)
- You have internet access to data.nj.gov

### "Module not found" errors

Check:
- requirements.txt is in backend directory
- Build completed successfully (check logs)

---

## 🆘 Need Help?

**Text me back with**:
- Screenshot of error
- Which step you're on
- What happened

I'll help you fix it immediately!

---

## ✅ Quick Checklist

- [ ] Railway account created
- [ ] Project created from GitHub
- [ ] PostgreSQL added
- [ ] PostGIS extension enabled
- [ ] Backend deployed with PYTHONPATH variable
- [ ] Backend domain generated
- [ ] Frontend deployed with REACT_APP_API_URL
- [ ] Frontend domain generated
- [ ] West Windsor data ingested
- [ ] Test analysis created
- [ ] PDF downloaded successfully

**When all checked**: 🎉 **YOU'RE DEPLOYED!**

---

**Ready? Start with Step 1!** 🚀
