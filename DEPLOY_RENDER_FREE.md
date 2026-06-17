# Deploy to Render.com (FREE)

**Cost**: $0 for 90 days, then $7/month if you want to keep it
**Time**: 15 minutes
**Perfect for**: West Windsor demo and grant application

---

## Prerequisites

- GitHub account
- Your code pushed to GitHub
- That's it!

---

## Step 1: Create Render Account

1. Go to https://render.com
2. Click "Get Started"
3. Sign up with GitHub (easiest)
4. Verify email

**Time**: 2 minutes

---

## Step 2: Create PostgreSQL Database

1. From Render dashboard, click "New +"
2. Select "PostgreSQL"
3. Configure:
   - **Name**: `nj-hin-database`
   - **Database**: `hin_db`
   - **User**: `hin_user`
   - **Region**: `Oregon (US West)` or closest to you
   - **Plan**: **Free** (select this!)

4. Click "Create Database"

5. Wait 2-3 minutes for database to provision

6. **IMPORTANT**: Copy these values (you'll need them):
   - Internal Database URL (starts with `postgres://`)
   - External Database URL
   - Hostname
   - Port
   - Database name
   - Username
   - Password

**Time**: 5 minutes

---

## Step 3: Enable PostGIS Extension

1. In your database dashboard, click "Shell" tab
2. Run this command:
   ```sql
   CREATE EXTENSION IF NOT EXISTS postgis;
   ```
3. You should see: `CREATE EXTENSION`

**Time**: 1 minute

---

## Step 4: Deploy Backend

1. From Render dashboard, click "New +"
2. Select "Web Service"
3. Connect your GitHub repository
4. Configure:
   - **Name**: `nj-hin-backend`
   - **Region**: Same as database
   - **Branch**: `claude/high-injury-network-generator-mTGsK`
   - **Root Directory**: `backend`
   - **Runtime**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - **Plan**: **Free**

5. **Environment Variables** - Click "Add Environment Variable" for each:
   ```
   DATABASE_URL = <paste Internal Database URL from Step 2>
   POSTGRES_HOST = <paste hostname from Step 2>
   POSTGRES_PORT = <paste port from Step 2>
   POSTGRES_DB = hin_db
   POSTGRES_USER = hin_user
   POSTGRES_PASSWORD = <paste password from Step 2>
   PYTHONPATH = /opt/render/project/src
   ```

6. Click "Create Web Service"

7. Wait 5-10 minutes for first deploy

8. Once deployed, copy the URL (like `https://nj-hin-backend.onrender.com`)

**Time**: 15 minutes (including build time)

---

## Step 5: Deploy Frontend

1. From Render dashboard, click "New +"
2. Select "Static Site"
3. Connect same GitHub repository
4. Configure:
   - **Name**: `nj-hin-frontend`
   - **Branch**: `claude/high-injury-network-generator-mTGsK`
   - **Root Directory**: `frontend`
   - **Build Command**: `npm install && npm run build`
   - **Publish Directory**: `build`

5. **Environment Variables** - Add this:
   ```
   REACT_APP_API_URL = <paste backend URL from Step 4>/api
   ```
   Example: `https://nj-hin-backend.onrender.com/api`

6. Click "Create Static Site"

7. Wait 5-10 minutes for build

8. Once deployed, you'll get a URL like `https://nj-hin-frontend.onrender.com`

**Time**: 15 minutes (including build time)

---

## Step 6: Ingest West Windsor Data

Now that everything is deployed, load the data:

### Option A: Use Render Shell (Easiest)

1. Go to your backend service in Render
2. Click "Shell" tab
3. Run:
   ```bash
   cd /opt/render/project/src/scripts
   python ingest_all_real_data.py \
     --municipality "West Windsor" \
     --county "Mercer" \
     --start-year 2017 \
     --end-year 2021
   ```

4. This will take 20-30 minutes
5. You can close the browser, it runs in background

### Option B: From Your Local Machine

1. Install dependencies locally:
   ```bash
   cd backend
   pip install -r requirements.txt
   ```

2. Set database URL:
   ```bash
   export DATABASE_URL="<External Database URL from Step 2>"
   ```

3. Run ingestion:
   ```bash
   cd scripts
   python ingest_all_real_data.py \
     --municipality "West Windsor" \
     --county "Mercer" \
     --start-year 2017 \
     --end-year 2021
   ```

**Time**: 30 minutes (mostly waiting for data download)

---

## Step 7: Test Your Deployment

1. Open your frontend URL: `https://nj-hin-frontend.onrender.com`

2. You should see the homepage

3. Select "West Windsor — Mercer County"

4. Years: 2017 - 2021

5. Click "Run Analysis"

6. Wait 5-10 minutes for analysis to complete

7. View interactive map with crash points

8. Click "Download PDF Report"

9. ✅ Success! You now have a deployed HIN Generator!

---

## Important Notes

### Free Tier Limitations

**Database**:
- ⚠️ **Expires after 90 days** (you'll get email warnings)
- 1 GB storage (plenty for West Windsor)
- Backups not included

**Backend Service**:
- ⚠️ **Sleeps after 15 minutes** of inactivity
- First request takes 30-60 seconds to wake up
- Subsequent requests are fast

**Frontend**:
- ✅ Always on, no sleep
- Fast global CDN

### Upgrading to Paid

If you want to keep it permanently:

**Database**: $7/month
- Never expires
- Daily backups
- More storage

**Backend**: $7/month
- No sleep
- Always fast
- Better performance

**Total**: $14/month for permanent production deployment

---

## Troubleshooting

### Backend won't start

Check logs in Render dashboard:
- Look for import errors
- Check environment variables are set
- Verify DATABASE_URL is correct

### Frontend can't connect to backend

Check `REACT_APP_API_URL`:
- Must end with `/api`
- Must be the backend URL (not frontend URL)
- Must be HTTPS (not HTTP)

### Data ingestion fails

Check:
- Database has PostGIS extension
- Internet access from Render (should work)
- Try with `--max-crashes 100` first to test

### "Module not found" errors

Check:
- Build command includes `pip install -r requirements.txt`
- `PYTHONPATH=/opt/render/project/src` is set
- Root directory is set to `backend`

---

## URLs to Share with West Windsor

After deployment, share these with your client:

**Application**: `https://nj-hin-frontend.onrender.com`

**API Docs**: `https://nj-hin-backend.onrender.com/docs`

**Instructions**:
1. Open application URL
2. Select "West Windsor — Mercer County"
3. Choose years (2017-2021)
4. Click "Run Analysis"
5. Wait 5-10 minutes
6. Download PDF report for grant

---

## Cost Summary

**Development/Demo (90 days)**:
- Database: FREE
- Backend: FREE
- Frontend: FREE
- **Total**: $0

**Production (permanent)**:
- Database: $7/month
- Backend: $7/month
- Frontend: FREE
- **Total**: $14/month

**vs. Consultant**: $15,000-$30,000 one-time
**Savings**: 99.4%+ 🎉

---

## Next Steps After Deployment

1. ✅ Test the application thoroughly
2. ✅ Generate a test PDF report
3. ✅ Share URL with West Windsor
4. ✅ Demo the system
5. ✅ Submit to SS4A grant with PDF

Then decide:
- Keep free for 90 days (demo only)
- Upgrade to $14/month (permanent)
- Or migrate to Railway ($5/month)

---

## Alternative: Railway.app ($5/month)

If you want permanent hosting RIGHT NOW without the 90-day limit:

1. Go to https://railway.app
2. Sign up with GitHub
3. Click "New Project"
4. Select "Deploy from GitHub repo"
5. Choose your repository
6. Railway auto-detects and deploys everything
7. Add PostgreSQL from "New" menu
8. Enable PostGIS extension
9. Run data ingestion from Railway shell

**Total time**: 10 minutes
**Cost**: $5/month (you get $5 free to start)

---

Ready to deploy? Start with Render.com free tier!
