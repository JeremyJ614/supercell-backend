"""
noaa.py — Fetches active NWS alerts and enriches them with
nearest NEXRAD station IDs and polygon centroids.
"""

import math
import httpx
import logging
from typing import Optional

logger = logging.getLogger(__name__)

NOAA_ALERTS_URL = (
    "https://api.weather.gov/alerts/active"
    "?event=Tornado%20Warning,Severe%20Thunderstorm%20Warning,"
    "Tornado%20Watch,Severe%20Thunderstorm%20Watch"
)

# ── Complete CONUS WSR-88D NEXRAD station list ─────────────────────────────
# Format: (id, name, lat, lon)
NEXRAD_STATIONS = [
    ("KABR","Aberdeen SD",45.4558,-98.4132),("KABX","Albuquerque NM",35.1497,-106.824),
    ("KAKQ","Wakefield VA",36.9839,-77.0072),("KAMA","Amarillo TX",35.2333,-101.709),
    ("KAMX","Miami FL",25.6111,-80.4128),("KAPX","Gaylord MI",44.9072,-84.7197),
    ("KARX","La Crosse WI",43.8228,-91.1912),("KATX","Seattle WA",48.1947,-122.496),
    ("KBBX","Beale AFB CA",39.4956,-121.632),("KBGM","Binghamton NY",42.1997,-75.985),
    ("KBHX","Eureka CA",40.4986,-124.292),("KBIS","Bismarck ND",46.7708,-100.76),
    ("KBLX","Billings MT",45.8539,-108.607),("KBMX","Birmingham AL",33.1722,-86.7697),
    ("KBOX","Boston MA",41.9558,-71.1369),("KBRO","Brownsville TX",25.9161,-97.4189),
    ("KBUF","Buffalo NY",42.9494,-78.7369),("KBYX","Key West FL",24.5975,-81.7033),
    ("KCAE","Columbia SC",33.9486,-81.1183),("KCBW","Caribou ME",46.0392,-67.8069),
    ("KCBX","Boise ID",43.4911,-116.236),("KCCX","State College PA",40.9228,-78.0039),
    ("KCLE","Cleveland OH",41.4133,-81.8597),("KCLX","Charleston SC",32.6553,-81.0422),
    ("KCRI","Oklahoma City OK",35.2383,-97.4603),("KCRP","Corpus Christi TX",27.7839,-97.511),
    ("KCXX","Burlington VT",44.5111,-73.1664),("KCYS","Cheyenne WY",41.1519,-104.806),
    ("KDAX","Sacramento CA",38.5011,-121.678),("KDDC","Dodge City KS",37.7208,-99.9686),
    ("KDFX","Laughlin AFB TX",29.2731,-100.28),("KDGX","Brandon MS",32.2797,-89.9844),
    ("KDIX","Philadelphia PA",39.9469,-74.4108),("KDLH","Duluth MN",46.8369,-92.2097),
    ("KDMX","Des Moines IA",41.7311,-93.7228),("KDOX","Dover DE",38.8256,-75.44),
    ("KDTX","Detroit MI",42.6997,-83.4717),("KDVN","Davenport IA",41.6117,-90.5808),
    ("KDYX","Dyess AFB TX",32.5383,-99.2544),("KEAX","Kansas City MO",38.8103,-94.2644),
    ("KEMX","Tucson AZ",31.8933,-110.63),("KENX","Albany NY",42.5864,-74.0639),
    ("KEOX","Fort Rucker AL",31.4603,-85.4594),("KEPZ","El Paso TX",31.8731,-106.698),
    ("KESX","Las Vegas NV",35.7011,-114.892),("KEVX","Eglin AFB FL",30.5644,-85.9217),
    ("KEWX","San Antonio TX",29.7039,-98.0283),("KEYX","Edwards AFB CA",35.0978,-117.561),
    ("KFCX","Roanoke VA",37.0242,-80.2742),("KFDR","Frederick OK",34.3622,-98.9764),
    ("KFDX","Cannon AFB NM",34.6344,-103.63),("KFFC","Atlanta GA",33.3636,-84.5658),
    ("KFSD","Sioux Falls SD",43.5878,-96.7294),("KFSX","Flagstaff AZ",34.5744,-111.198),
    ("KFTG","Denver CO",39.7867,-104.546),("KFWS","Dallas/Ft Worth TX",32.5728,-97.3031),
    ("KGGW","Glasgow MT",48.2064,-106.625),("KGJX","Grand Junction CO",39.0622,-108.214),
    ("KGLD","Goodland KS",39.3669,-101.7),("KGRB","Green Bay WI",44.4986,-88.1111),
    ("KGRK","Fort Hood TX",30.7217,-97.3828),("KGRR","Grand Rapids MI",42.8939,-85.5447),
    ("KGSP","Greenville SC",34.8833,-82.2203),("KGWX","Columbus AFB MS",33.8969,-88.3294),
    ("KGYX","Portland ME",43.8914,-70.2569),("KHDX","Holloman AFB NM",32.5347,-106.123),
    ("KHGX","Houston TX",29.4719,-95.0792),("KHNX","San Joaquin Valley CA",36.3142,-119.632),
    ("KHPX","Fort Campbell KY",36.7369,-87.285),("KHTX","Huntsville AL",34.9306,-86.0836),
    ("KICT","Wichita KS",37.6544,-97.4428),("KICX","Cedar City UT",37.5908,-112.862),
    ("KILN","Cincinnati OH",39.4203,-83.8217),("KILX","Lincoln IL",40.1506,-89.3367),
    ("KIND","Indianapolis IN",39.7075,-86.2803),("KINX","Tulsa OK",36.175,-95.5644),
    ("KIWA","Phoenix AZ",33.2892,-111.67),("KIWX","Fort Wayne IN",41.4086,-85.7),
    ("KJAX","Jacksonville FL",30.4844,-81.7019),("KJGX","Robins AFB GA",32.675,-83.3511),
    ("KJKL","Jackson KY",37.5908,-83.3131),("KLBB","Lubbock TX",33.6542,-101.814),
    ("KLCH","Lake Charles LA",30.125,-93.2158),("KLIX","New Orleans LA",30.3367,-89.8256),
    ("KLNX","North Platte NE",41.9578,-100.576),("KLOT","Chicago IL",41.6044,-88.0847),
    ("KLRX","Elko NV",40.7397,-116.803),("KLSX","St Louis MO",38.6989,-90.6828),
    ("KLTX","Wilmington NC",33.9894,-78.4292),("KLVX","Louisville KY",37.9753,-85.9439),
    ("KLWX","Sterling VA",38.9753,-77.4775),("KLZK","Little Rock AR",34.8364,-92.2622),
    ("KMAF","Midland TX",31.9433,-102.189),("KMAX","Medford OR",42.0811,-122.717),
    ("KMBX","Minot AFB ND",48.3925,-100.865),("KMHX","Morehead City NC",34.7758,-76.8764),
    ("KMKX","Milwaukee WI",42.9678,-88.5506),("KMLB","Melbourne FL",28.1133,-80.6542),
    ("KMOB","Mobile AL",30.6797,-88.2397),("KMPX","Minneapolis MN",44.8489,-93.5653),
    ("KMQT","Marquette MI",46.5311,-87.5483),("KMRX","Knoxville TN",36.1683,-83.4017),
    ("KMSX","Missoula MT",47.0408,-113.986),("KMTX","Salt Lake City UT",41.2628,-112.448),
    ("KMUX","San Francisco CA",37.155,-121.898),("KMVX","Fargo ND",46.9explosions,-97.3253),
    ("KMXX","Maxwell AFB AL",32.5369,-85.7897),("KNKX","San Diego CA",32.9189,-117.042),
    ("KNQA","Memphis TN",35.3447,-89.8733),("KOAX","Omaha NE",41.3203,-96.3667),
    ("KOHX","Nashville TN",36.2472,-86.5625),("KOKX","New York NY",40.8656,-72.8639),
    ("KOTX","Spokane WA",47.6803,-117.627),("KPAH","Paducah KY",37.0683,-88.7719),
    ("KPBZ","Pittsburgh PA",40.5317,-80.2178),("KPDT","Pendleton OR",45.6906,-118.853),
    ("KPOE","Fort Polk LA",31.1556,-92.9758),("KPUX","Pueblo CO",38.4595,-104.182),
    ("KRAX","Raleigh NC",35.6656,-78.4897),("KRGX","Reno NV",39.7542,-119.462),
    ("KRIW","Riverton WY",43.0661,-108.477),("KRLX","Charleston WV",38.3explosions,-81.7236),
    ("KRTX","Portland OR",45.7150,-122.965),("KSFX","Pocatello ID",43.1056,-112.686),
    ("KSGF","Springfield MO",37.2353,-93.4003),("KSHV","Shreveport LA",32.4508,-93.8411),
    ("KSJT","San Angelo TX",31.3711,-100.492),("KSOX","Santa Ana CA",33.8175,-117.636),
    ("KSRX","Fort Smith AR",35.2908,-94.3619),("KTBW","Tampa FL",27.7056,-82.4017),
    ("KTFX","Great Falls MT",47.4597,-111.385),("KTLH","Tallahassee FL",30.3978,-84.3289),
    ("KTLX","Oklahoma City OK",35.3331,-97.2778),("KTWX","Topeka KS",38.9969,-96.2325),
    ("KTYX","Montague NY",43.7558,-75.68),("KUDX","Rapid City SD",44.125,-102.830),
    ("KUEX","Hastings NE",40.3211,-98.4417),("KVAX","Moody AFB GA",30.8903,-83.0019),
    ("KVBX","Vandenberg AFB CA",34.8381,-120.398),("KVNX","Vance AFB OK",36.7408,-98.1275),
    ("KVTX","Los Angeles CA",34.4117,-119.179),("KVWX","Evansville IN",38.2603,-87.7247),
    ("KYUX","Yuma AZ",32.4953,-114.657),
]


def get_nearest_station(lat: float, lon: float) -> dict:
    """Return the nearest NEXRAD station to the given lat/lon."""
    best = None
    best_dist = float("inf")
    for sid, name, slat, slon in NEXRAD_STATIONS:
        dist = _haversine(lat, lon, slat, slon)
        if dist < best_dist:
            best_dist = dist
            best = {"id": sid, "name": name, "lat": slat, "lon": slon, "distance_km": round(dist, 1)}
    return best


def _haversine(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in km."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _polygon_centroid(coordinates: list) -> Optional[tuple]:
    """Compute centroid of a GeoJSON polygon's outer ring."""
    try:
        ring = coordinates[0]
        lons = [p[0] for p in ring]
        lats = [p[1] for p in ring]
        return (sum(lats)/len(lats), sum(lons)/len(lons))
    except Exception:
        return None


async def get_active_storms() -> dict:
    """
    Fetch active NWS severe weather alerts and enrich each with:
    - centroid lat/lon
    - nearest NEXRAD station ID
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(NOAA_ALERTS_URL, headers={"User-Agent": "SupercellExplorer/1.0"})
        resp.raise_for_status()
        raw = resp.json()

    features = raw.get("features", [])[:20]
    enriched = []

    for f in features:
        props = f.get("properties", {})
        geometry = f.get("geometry")

        centroid = None
        station = None

        # Extract centroid from polygon geometry
        if geometry and geometry.get("type") == "Polygon":
            centroid = _polygon_centroid(geometry["coordinates"])
        elif geometry and geometry.get("type") == "MultiPolygon":
            # Use first polygon
            try:
                centroid = _polygon_centroid(geometry["coordinates"][0])
            except Exception:
                pass

        if centroid:
            lat, lon = centroid
            station_info = get_nearest_station(lat, lon)
            station = station_info["id"] if station_info else None

        enriched.append({
            "id": props.get("id", ""),
            "event": props.get("event", ""),
            "area": props.get("areaDesc", "").split(";")[0].strip(),
            "sender": props.get("senderName", "NWS"),
            "onset": props.get("onset", ""),
            "expires": props.get("expires", ""),
            "severity": props.get("severity", ""),
            "certainty": props.get("certainty", ""),
            "urgency": props.get("urgency", ""),
            "headline": props.get("headline", ""),
            "description": (props.get("description", "") or "")[:600],
            "centroid_lat": centroid[0] if centroid else None,
            "centroid_lon": centroid[1] if centroid else None,
            "nexrad_station": station,
        })

    return {"count": len(enriched), "alerts": enriched}
