"""
nexrad.py — Downloads the latest NEXRAD Level-II radar file from NOAA's
public S3 bucket and parses it into a 3D reflectivity volume array
suitable for Three.js volumetric rendering.

NEXRAD Level-II data is free and public:
  s3://noaa-nexrad-level2/<YYYY>/<MM>/<DD>/<STATION>/<files>

We use boto3 in anonymous (no-auth) mode — no AWS account needed.
"""

import io
import gzip
import struct
import logging
import math
import tempfile
import os
from datetime import datetime, timezone
from typing import Optional
import numpy as np

logger = logging.getLogger(__name__)

# ── S3 config (public bucket, anonymous access) ───────────────────────────
NEXRAD_BUCKET = "noaa-nexrad-level2"
NEXRAD_REGION = "us-east-1"

# ── Reflectivity thresholds (dBZ) ─────────────────────────────────────────
DBZ_MIN = -10.0   # Below this → 0 in volume
DBZ_MAX = 75.0    # Above this → clamp to 1
# Severe storm typically > 45 dBZ; tornado potential > 60 dBZ


async def fetch_latest_scan(station_id: str) -> bytes:
    """
    Find and download the most recent NEXRAD Level-II file for a station.
    Returns raw file bytes.
    """
    import boto3
    from botocore import UNSIGNED
    from botocore.config import Config

    s3 = boto3.client(
        "s3",
        region_name=NEXRAD_REGION,
        config=Config(signature_version=UNSIGNED)
    )

    now = datetime.now(timezone.utc)
    prefix = f"{now.year}/{now.month:02d}/{now.day:02d}/{station_id}/"

    logger.info(f"Listing S3: s3://{NEXRAD_BUCKET}/{prefix}")

    try:
        resp = s3.list_objects_v2(Bucket=NEXRAD_BUCKET, Prefix=prefix)
    except Exception as e:
        raise FileNotFoundError(f"Cannot list S3 for station {station_id}: {e}")

    contents = resp.get("Contents", [])

    # Filter to actual Level-II files (not _MDM metadata)
    files = [
        obj for obj in contents
        if not obj["Key"].endswith("_MDM") and not obj["Key"].endswith("/")
    ]

    if not files:
        # Try yesterday (UTC midnight edge case)
        from datetime import timedelta
        yesterday = now - timedelta(days=1)
        prefix_yd = f"{yesterday.year}/{yesterday.month:02d}/{yesterday.day:02d}/{station_id}/"
        resp2 = s3.list_objects_v2(Bucket=NEXRAD_BUCKET, Prefix=prefix_yd)
        files = [
            obj for obj in resp2.get("Contents", [])
            if not obj["Key"].endswith("_MDM") and not obj["Key"].endswith("/")
        ]

    if not files:
        raise FileNotFoundError(
            f"No NEXRAD data found for station {station_id}. "
            "Station may be offline or data not yet available."
        )

    # Latest file = last in sorted list
    latest = sorted(files, key=lambda x: x["LastModified"])[-1]
    key = latest["Key"]
    size_mb = latest["Size"] / 1024 / 1024
    logger.info(f"Downloading {key} ({size_mb:.1f} MB)")

    obj = s3.get_object(Bucket=NEXRAD_BUCKET, Key=key)
    raw = obj["Body"].read()
    logger.info(f"Downloaded {len(raw)} bytes")
    return raw


def parse_to_volume(raw_bytes: bytes, resolution: int = 64) -> list:
    """
    Parse NEXRAD Level-II binary data into a 3D reflectivity volume.

    Returns a flat list of floats (0.0–1.0) in Z→Y→X order,
    length = resolution³, ready for Three.js DataTexture3D.

    The volume represents a 460 km × 460 km × 20 km box
    centered on the radar station.
    """
    try:
        # Try parsing with pyart (most accurate)
        return _parse_with_pyart(raw_bytes, resolution)
    except ImportError:
        logger.warning("pyart not available, using native parser")
        return _parse_native(raw_bytes, resolution)
    except Exception as e:
        logger.warning(f"pyart parse failed ({e}), falling back to native parser")
        return _parse_native(raw_bytes, resolution)


def _parse_with_pyart(raw_bytes: bytes, resolution: int) -> list:
    """
    Parse using the ARM PyART library for full accuracy.
    Handles all NEXRAD message types and sweep geometries.
    """
    import pyart

    # Write to temp file (pyart needs a file path or file-like)
    with tempfile.NamedTemporaryFile(suffix=".ar2v", delete=False) as tmp:
        tmp.write(raw_bytes)
        tmp_path = tmp.name

    try:
        radar = pyart.io.read_nexrad_archive(tmp_path)
    finally:
        os.unlink(tmp_path)

    # Get base reflectivity (lowest elevation sweep)
    ref_field = None
    for candidate in ["reflectivity", "REF", "DBZ"]:
        if candidate in radar.fields:
            ref_field = candidate
            break

    if ref_field is None:
        raise ValueError("No reflectivity field found in radar data")

    # Build 3D Cartesian grid using PyART's map_to_grid
    grid = pyart.map.grid_from_radars(
        (radar,),
        grid_shape=(resolution // 2, resolution, resolution),  # Z, Y, X
        grid_limits=(
            (0, 20000),        # Z: 0–20 km altitude
            (-230000, 230000), # Y: ±230 km
            (-230000, 230000), # X: ±230 km
        ),
        fields=[ref_field],
        gridding_algo="map_gates_to_grid",
        weighting_function="Cressman",
    )

    data = grid.fields[ref_field]["data"]  # masked array, shape (Z/2, Y, X)

    # Normalize to 0–1 and upsample Z to resolution
    volume = np.ma.filled(data, fill_value=DBZ_MIN).astype(np.float32)
    volume = np.clip((volume - DBZ_MIN) / (DBZ_MAX - DBZ_MIN), 0.0, 1.0)

    # Upsample Z axis to resolution (simple linear interp)
    from scipy.ndimage import zoom
    target_shape = (resolution, resolution, resolution)
    if volume.shape != target_shape:
        factors = tuple(t / s for t, s in zip(target_shape, volume.shape))
        volume = zoom(volume, factors, order=1)

    logger.info(f"PyART volume: shape={volume.shape}, max_dbz_norm={volume.max():.3f}")
    return volume.flatten().tolist()


def _parse_native(raw_bytes: bytes, resolution: int) -> list:
    """
    Pure-Python NEXRAD Level-II parser.
    Handles Message Type 1 (legacy) and Message Type 31 (current standard).
    Outputs polar data then bins into Cartesian 3D volume.
    """
    # Decompress if needed (BZ2 compressed after header)
    data = _decompress_nexrad(raw_bytes)

    # Parse NEXRAD Archive II format
    gates = _extract_reflectivity_gates(data)

    if not gates:
        logger.warning("No reflectivity gates parsed, returning empty volume")
        return [0.0] * (resolution ** 3)

    # Convert polar → Cartesian volume
    volume = _polar_to_volume(gates, resolution)
    return volume.flatten().tolist()


def _decompress_nexrad(raw: bytes) -> bytes:
    """
    NEXRAD Level-II files have a 24-byte ASCII header, then BZ2-compressed records.
    Decompress and concatenate all records.
    """
    import bz2

    # First 24 bytes = Archive II header (ASCII)
    header = raw[:24]
    logger.debug(f"NEXRAD header: {header}")

    pos = 24
    records = [raw[:24]]  # Keep header

    while pos < len(raw):
        # Each compressed record starts with a 4-byte signed int (size)
        if pos + 4 > len(raw):
            break
        size = struct.unpack(">i", raw[pos:pos+4])[0]
        pos += 4

        if size == -1:
            # End of file sentinel
            break
        if size <= 0 or pos + size > len(raw):
            break

        compressed_block = raw[pos:pos+size]
        pos += size

        try:
            decompressed = bz2.decompress(compressed_block)
            records.append(decompressed)
        except Exception as e:
            logger.debug(f"BZ2 decompress failed at pos {pos}: {e}")
            records.append(compressed_block)  # Keep raw if not compressed

    return b"".join(records)


def _extract_reflectivity_gates(data: bytes) -> list:
    """
    Walk NEXRAD message records and extract reflectivity gate data.
    Returns list of dicts: {azimuth, elevation, range_start, range_step, values}
    """
    gates = []
    pos = 24  # Skip Archive II header

    while pos + 28 < len(data):
        try:
            # NEXRAD message header (28 bytes total with CTM header)
            # CTM header: 12 bytes
            # Message header: 16 bytes
            #   halfwords[0] = message size (halfwords)
            #   halfwords[1] = RDA channel
            #   halfwords[2] = message type
            #   halfwords[3] = id seq number
            #   halfwords[4] = Julian date
            #   halfwords[5-6] = ms since midnight
            #   halfwords[7] = num message segments
            #   halfwords[8] = message segment number

            # Skip CTM header (12 bytes)
            msg_start = pos + 12
            if msg_start + 16 > len(data):
                break

            msg_size_hw = struct.unpack(">H", data[msg_start:msg_start+2])[0]
            msg_type = struct.unpack(">B", data[msg_start+2:msg_start+3])[0]

            msg_size_bytes = max(msg_size_hw * 2, 28)

            if msg_type == 1:
                # Legacy Message Type 1 — Digital Reflectivity
                gate = _parse_msg1(data, msg_start)
                if gate:
                    gates.append(gate)

            elif msg_type == 31:
                # Current standard: Message Type 31 — Digital Radar Data
                gate = _parse_msg31(data, msg_start)
                if gate:
                    gates.append(gate)

            pos += 2432  # NEXRAD records are always 2432 bytes padded

        except struct.error:
            break
        except Exception as e:
            logger.debug(f"Gate parse error at pos {pos}: {e}")
            pos += 2432

    logger.info(f"Extracted {len(gates)} reflectivity radials")
    return gates


def _parse_msg1(data: bytes, offset: int) -> Optional[dict]:
    """Parse NEXRAD Message Type 1 (legacy digital reflectivity)."""
    try:
        # Message 1 structure (after 16-byte msg header):
        # offset+16: milliseconds from midnight (4 bytes)
        # offset+20: Julian date (2 bytes)
        # offset+22: azimuth (2 bytes, units of 1/8 degree)
        # offset+24: azimuth index (2 bytes)
        # offset+26: radial status (2 bytes)
        # offset+28: elevation angle (2 bytes, units of 1/8 degree)
        # offset+30: elevation number (2 bytes)
        # offset+32: range to first gate (2 bytes, km*1000)
        # offset+34: range step (2 bytes, km*1000)
        # offset+36: number of gates (2 bytes)
        # offset+38: pointer to reflectivity data (2 bytes, from msg start)

        base = offset + 16
        azimuth_raw = struct.unpack(">H", data[base+6:base+8])[0]
        elevation_raw = struct.unpack(">H", data[base+12:base+14])[0]
        range_first = struct.unpack(">H", data[base+16:base+18])[0]  # m
        range_step = struct.unpack(">H", data[base+18:base+20])[0]   # m
        n_gates = struct.unpack(">H", data[base+20:base+22])[0]
        ref_ptr = struct.unpack(">H", data[base+22:base+24])[0]      # halfwords from msg start

        azimuth = azimuth_raw * 0.125    # degrees
        elevation = elevation_raw * 0.125  # degrees

        # Reflectivity data starts at offset + ref_ptr*2
        data_start = offset + ref_ptr * 2
        data_end = data_start + n_gates

        if data_end > len(data) or n_gates == 0:
            return None

        raw_vals = struct.unpack(f">{n_gates}B", data[data_start:data_end])

        # Convert: value=0 → below threshold, value=1 → range folded
        # value 2–255 → dBZ = (value - 2) / 2 - 32.0
        dbz = []
        for v in raw_vals:
            if v < 2:
                dbz.append(float('nan'))
            else:
                dbz.append((v - 2) / 2.0 - 32.0)

        return {
            "azimuth": azimuth,
            "elevation": elevation,
            "range_start_m": range_first,
            "range_step_m": range_step,
            "dbz": dbz,
        }
    except Exception:
        return None


def _parse_msg31(data: bytes, offset: int) -> Optional[dict]:
    """Parse NEXRAD Message Type 31 (current standard digital radar data)."""
    try:
        # Message 31 header (after 16-byte msg header):
        # offset+16: radar id (4 chars)
        # offset+20: collection time (4 bytes, ms from midnight)
        # offset+24: Julian date (2 bytes)
        # offset+26: azimuth number (2 bytes)
        # offset+28: azimuth angle (4 bytes, float32 degrees)
        # offset+32: compression indicator (1 byte)
        # offset+33: spare (1 byte)
        # offset+34: radial length (2 bytes)
        # offset+36: azimuth resolution spacing (1 byte)
        # offset+37: radial status (1 byte)
        # offset+38: elevation number (1 byte)
        # offset+39: cut sector number (1 byte)
        # offset+40: elevation angle (4 bytes, float32 degrees)
        # offset+44: radial spot blanking (1 byte)
        # offset+45: azimuth indexing value (1 byte)
        # offset+46: data block count (2 bytes)
        # offset+48+: data block pointers

        base = offset + 16
        azimuth = struct.unpack(">f", data[base+12:base+16])[0]
        elevation = struct.unpack(">f", data[base+24:base+28])[0]
        n_data_blocks = struct.unpack(">H", data[base+30:base+32])[0]

        dbz = None

        # Walk data block pointers (4 bytes each, relative to msg start = offset+16)
        for i in range(min(n_data_blocks, 10)):
            ptr_offset = base + 32 + i * 4
            if ptr_offset + 4 > len(data):
                break
            block_ptr = struct.unpack(">I", data[ptr_offset:ptr_offset+4])[0]
            block_abs = offset + 16 + block_ptr

            if block_abs + 8 > len(data):
                continue

            # Data block header
            block_type = data[block_abs:block_abs+1]
            block_name = data[block_abs+1:block_abs+4]

            if block_name == b"REF":
                # Reflectivity block
                # +4: reserved (4 bytes)  → actually: gate count (2), first gate (2), gate size (2), rf threshold (2), snr threshold (2), flags (1), word size (1), scale (4), offset (4)
                n_gates = struct.unpack(">H", data[block_abs+8:block_abs+10])[0]
                range_first = struct.unpack(">H", data[block_abs+10:block_abs+12])[0]  # m
                range_step = struct.unpack(">H", data[block_abs+12:block_abs+14])[0]   # m
                word_size = data[block_abs+19]  # bits per gate (8 or 16)
                scale = struct.unpack(">f", data[block_abs+20:block_abs+24])[0]
                offset_val = struct.unpack(">f", data[block_abs+24:block_abs+28])[0]

                data_start = block_abs + 28
                if word_size == 16:
                    fmt = f">{n_gates}H"
                    sz = n_gates * 2
                else:
                    fmt = f">{n_gates}B"
                    sz = n_gates

                if data_start + sz > len(data):
                    continue

                raw_vals = struct.unpack(fmt, data[data_start:data_start+sz])
                dbz = []
                for v in raw_vals:
                    if v <= 1:
                        dbz.append(float('nan'))
                    else:
                        dbz.append((v - offset_val) / scale)

                range_start_m = range_first
                range_step_m = range_step
                break

        if dbz is None:
            return None

        return {
            "azimuth": azimuth,
            "elevation": elevation,
            "range_start_m": range_start_m,
            "range_step_m": range_step_m,
            "dbz": dbz,
        }
    except Exception:
        return None


def _polar_to_volume(gates: list, resolution: int) -> np.ndarray:
    """
    Convert polar (azimuth, elevation, range) reflectivity gates
    into a 3D Cartesian numpy volume of shape (resolution, resolution, resolution).

    The volume covers:
      X, Y: ±230 km from station
      Z:    0–20 km altitude

    Each voxel value is normalized dBZ: 0.0 (no echo) → 1.0 (75 dBZ)
    """
    vol = np.zeros((resolution, resolution, resolution), dtype=np.float32)
    count = np.zeros_like(vol, dtype=np.int32)

    MAX_RANGE_M = 230_000.0
    MAX_ALT_M = 20_000.0
    cell_size_xy = (2 * MAX_RANGE_M) / resolution  # m per cell
    cell_size_z = MAX_ALT_M / resolution

    for gate in gates:
        az_deg = gate["azimuth"]
        el_deg = gate["elevation"]
        az_rad = math.radians(az_deg)
        el_rad = math.radians(el_deg)

        r_start = gate["range_start_m"]
        r_step = gate["range_step_m"] if gate["range_step_m"] > 0 else 250

        for gi, dbz_val in enumerate(gate["dbz"]):
            if math.isnan(dbz_val) or dbz_val < DBZ_MIN:
                continue

            r = r_start + gi * r_step
            if r > MAX_RANGE_M:
                break

            # Polar → Cartesian (flat Earth approximation, good to ~250 km)
            # X = east, Y = north, Z = altitude
            ground_r = r * math.cos(el_rad)
            x_m = ground_r * math.sin(az_rad)   # East
            y_m = ground_r * math.cos(az_rad)   # North
            z_m = r * math.sin(el_rad)           # Altitude

            if z_m < 0 or z_m > MAX_ALT_M:
                continue

            # Convert to voxel indices
            xi = int((x_m + MAX_RANGE_M) / cell_size_xy)
            yi = int((y_m + MAX_RANGE_M) / cell_size_xy)
            zi = int(z_m / cell_size_z)

            xi = max(0, min(resolution-1, xi))
            yi = max(0, min(resolution-1, yi))
            zi = max(0, min(resolution-1, zi))

            # Normalize dBZ → 0–1
            norm = (dbz_val - DBZ_MIN) / (DBZ_MAX - DBZ_MIN)
            norm = max(0.0, min(1.0, norm))

            vol[zi, yi, xi] += norm
            count[zi, yi, xi] += 1

    # Average where multiple gates hit same voxel
    mask = count > 0
    vol[mask] /= count[mask]

    # Smooth slightly to reduce aliasing
    try:
        from scipy.ndimage import gaussian_filter
        vol = gaussian_filter(vol, sigma=0.8)
    except ImportError:
        pass

    logger.info(
        f"Volume stats: shape={vol.shape}, "
        f"non-zero voxels={mask.sum()}, "
        f"max={vol.max():.3f}"
    )
    return vol
