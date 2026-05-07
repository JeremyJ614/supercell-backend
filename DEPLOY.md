# Supercell Explorer — Google Cloud Run Deployment Guide

## What you'll have when done:
- A live backend URL (e.g. https://supercell-api-xxxx-uc.a.run.app)
- Real NEXRAD radar data served as 3D volumes
- $0/month on Cloud Run free tier (2M requests/month free)

---

## STEP 1 — Install Google Cloud CLI

Go to: https://cloud.google.com/sdk/docs/install
Download and run the installer for your OS.

Then open a terminal and run:
```
gcloud init
```
Sign in with your Google account when prompted.

---

## STEP 2 — Create a Google Cloud Project

```bash
# Create a new project (pick any unique ID)
gcloud projects create supercell-explorer-2024 --name="Supercell Explorer"

# Set it as your active project
gcloud config set project supercell-explorer-2024

# Enable billing (required for Cloud Run, but free tier covers everything)
# Go to: https://console.cloud.google.com/billing
# Link your project to a billing account (needs a card on file, won't be charged)
```

---

## STEP 3 — Enable Required APIs

```bash
gcloud services enable run.googleapis.com
gcloud services enable cloudbuild.googleapis.com
gcloud services enable containerregistry.googleapis.com
```

---

## STEP 4 — Deploy the Backend

Navigate to your backend folder (wherever you saved it), then run:

```bash
# This single command builds the Docker image and deploys to Cloud Run
gcloud run deploy supercell-api \
  --source . \
  --region us-central1 \
  --platform managed \
  --allow-unauthenticated \
  --memory 2Gi \
  --cpu 1 \
  --timeout 120 \
  --max-instances 3 \
  --min-instances 0
```

- `--allow-unauthenticated` → lets your frontend call it without auth tokens
- `--memory 2Gi` → needed for NEXRAD parsing (numpy arrays can be large)
- `--max-instances 3` → stays within free tier limits
- `--min-instances 0` → scales to zero when not in use (free when idle)

The build takes 3–5 minutes the first time. When done you'll see:

```
Service URL: https://supercell-api-xxxx-uc.a.run.app
```

**Copy that URL — you'll need it in Step 6.**

---

## STEP 5 — Test the Backend

```bash
# Health check
curl https://supercell-api-xxxx-uc.a.run.app/health

# Get active storms
curl https://supercell-api-xxxx-uc.a.run.app/api/storms

# Get radar volume for OKC (replace KTLX with any storm's station)
curl "https://supercell-api-xxxx-uc.a.run.app/api/radar/KTLX?resolution=32"

# Interactive API docs (open in browser)
https://supercell-api-xxxx-uc.a.run.app/docs
```

---

## STEP 6 — Connect Frontend to Backend

In your `supercell-explorer.html`, find this line near the top of the `<script>` section:

```javascript
// ADD THIS near the top of your script section:
const BACKEND_URL = "https://supercell-api-xxxx-uc.a.run.app";
```

Then update the `fetchStorms()` function to use the backend instead of NOAA directly:

```javascript
async function fetchStorms() {
  const res = await fetch(`${BACKEND_URL}/api/storms`);
  const data = await res.json();
  // data.alerts is now enriched with nexrad_station, centroid_lat, centroid_lon
  ...
}
```

And add radar loading when a storm is selected:

```javascript
async function loadRadarVolume(stationId) {
  const res = await fetch(`${BACKEND_URL}/api/radar/${stationId}?resolution=64`);
  const data = await res.json();
  // data.volume is a flat Float32Array-ready list
  // Use it to drive Three.js DataTexture3D for real volumetric rendering
  applyVolumeToScene(data.volume, data.resolution);
}
```

---

## STEP 7 — Deploy Frontend (Free)

Option A — GitHub Pages (easiest):
1. Create a GitHub repo
2. Upload supercell-explorer.html as index.html
3. Go to repo Settings → Pages → Deploy from main branch
4. Your app is live at: https://yourusername.github.io/reponame

Option B — Netlify (drag and drop):
1. Go to netlify.com
2. Drag your HTML file onto the deploy zone
3. Live in 30 seconds

---

## Free Tier Limits (You're Safe)

| Resource | Free Allowance | Expected Usage |
|---|---|---|
| Cloud Run requests | 2M/month | ~1K/month |
| Cloud Run compute | 360K GB-seconds | ~5K GB-seconds |
| NOAA S3 downloads | Completely free | Free always |
| Egress (data out) | 1 GB/month | ~500 MB/month |

**Estimated monthly cost: $0.00**

---

## Updating the Backend Later

When you make changes to the code, just re-run the deploy command:
```bash
gcloud run deploy supercell-api --source . --region us-central1
```

---

## Troubleshooting

**"Container failed to start"** → Check logs:
```bash
gcloud run logs read supercell-api --region us-central1
```

**"No NEXRAD data found"** → The station may be offline or it's a quiet weather day.
Test with KTLX (Oklahoma City) — one of the busiest stations.

**Slow first response** → Cloud Run cold starts take 5–15 seconds after idle.
Normal — subsequent requests are fast.

**CORS errors in browser** → Make sure `allow_origins=["*"]` is set in main.py,
or replace * with your specific frontend URL.
