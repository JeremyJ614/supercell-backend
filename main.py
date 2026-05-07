"""
Supercell Explorer — Backend API
Fetches NEXRAD Level-II radar from NOAA S3, parses into 3D volume data,
and serves it to the frontend for volumetric Three.js rendering.
"""

import os
import logging
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn

from app.noaa import get_active_storms, get_nearest_station
from app.nexrad import fetch_latest_scan, parse_to_volume
from app.cache import cache_get, cache_set

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Supercell Explorer API",
    description="Live NEXRAD radar → 3D volumetric data for supercell visualization",
    version="1.0.0"
)

# ── CORS: allow your frontend (update origin when you deploy) ──────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten this to your frontend URL after deploy
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ══════════════════════════════════════════════════════════════════════════════
# HEALTH CHECK
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/health")
async def health():
    return {"status": "ok", "service": "supercell-explorer-backend"}


# ══════════════════════════════════════════════════════════════════════════════
# GET ACTIVE STORMS  (wraps NOAA alerts API with station lookup)
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/api/storms")
async def storms():
    """
    Returns active NWS severe weather alerts enriched with:
    - nearest NEXRAD station ID
    - centroid lat/lon for the alert polygon
    """
    cache_key = "active_storms"
    cached = cache_get(cache_key)
    if cached:
        return cached

    try:
        data = await get_active_storms()
        cache_set(cache_key, data, ttl=60)  # Cache 60 seconds
        return data
    except Exception as e:
        logger.error(f"Storm fetch error: {e}")
        raise HTTPException(status_code=502, detail=f"NOAA API error: {str(e)}")


# ══════════════════════════════════════════════════════════════════════════════
# GET NEXRAD VOLUME FOR A STATION
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/api/radar/{station_id}")
async def radar_volume(
    station_id: str,
    resolution: int = Query(default=64, ge=16, le=128, description="Volume grid resolution (N³)")
):
    """
    Downloads the latest NEXRAD Level-II scan for a given station (e.g. KTLX),
    parses it into a 3D reflectivity volume, and returns JSON the frontend
    can use to drive Three.js volumetric rendering.

    Resolution is clamped: 16 (fast/low) → 128 (detailed/slow).
    64 is recommended for Cloud Run free tier.
    """
    station_id = station_id.upper().strip()
    if len(station_id) != 4:
        raise HTTPException(status_code=400, detail="Station ID must be 4 characters, e.g. KTLX")

    cache_key = f"radar_{station_id}_{resolution}"
    cached = cache_get(cache_key)
    if cached:
        logger.info(f"Cache hit: {cache_key}")
        return cached

    try:
        logger.info(f"Fetching NEXRAD scan for {station_id}")
        raw_file = await fetch_latest_scan(station_id)

        logger.info(f"Parsing to {resolution}³ volume")
        volume = parse_to_volume(raw_file, resolution=resolution)

        result = {
            "station": station_id,
            "resolution": resolution,
            "volume": volume,  # flat float32 array, row-major Z→Y→X
        }

        cache_set(cache_key, result, ttl=300)  # Cache 5 minutes (radar updates ~5-6 min)
        return result

    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Radar parse error for {station_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Radar processing error: {str(e)}")


# ══════════════════════════════════════════════════════════════════════════════
# GET NEAREST NEXRAD STATION FOR A LAT/LON
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/api/station")
async def nearest_station(
    lat: float = Query(..., description="Latitude"),
    lon: float = Query(..., description="Longitude")
):
    """
    Returns the nearest NEXRAD WSR-88D station ID for a given coordinate.
    Useful for looking up radar for a specific storm's centroid.
    """
    try:
        station = get_nearest_station(lat, lon)
        return {"station_id": station["id"], "name": station["name"],
                "lat": station["lat"], "lon": station["lon"],
                "distance_km": station["distance_km"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════════════════════
# LOCAL DEV ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)
