#!/usr/bin/env python3
"""
Refresh the Great Lakes public briefing.

Primary water levels: Canadian Hydrographic Service IWLS API
(https://api-iwls.dfo-mpo.gc.ca). Supplementary temps, winds, waves:
NOAA CO-OPS, NDBC, NOAA GLERL/CoastWatch GLSEA, Open-Meteo.
"""

from __future__ import annotations

import hashlib
import json
import math
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import Polygon  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
ASSETS = ROOT / "assets"
PROFILE_SRC = ASSETS / "great-lakes-profile.jpg"
INDEX = ROOT / "index.html"
SNAPSHOT = DATA / "snapshot.json"
HISTORY_JSON = DATA / "history_monthly.json"
HISTORY_JS = ROOT / "history.js"
PROFILE_PNG = ROOT / "profile_overlay.png"
MAP_LEVELS = ROOT / "map_levels.png"
MAP_TEMPS = ROOT / "map_temps.png"
MAP_WINDS = ROOT / "map_winds.png"
MAP_COND = ROOT / "map_conditions.png"
CHART_PNG = ROOT / "chart_levels.png"

IWLS = "https://api-iwls.dfo-mpo.gc.ca"
UA = "great-lakes-levels/1.0 (+https://github.com/colinpjack/great-lakes-levels)"
TZ = ZoneInfo("America/Toronto")
IWLS_GAP_S = 2.2  # stay under 30 requests / minute

# Official Low Water Datum / chart datum, IGLD 1985 (metres).
LAKES = {
    "superior": {
        "label": "Superior",
        "full": "Lake Superior",
        "lwd_m": 183.2,
        "lwd_ft": 601.1,
        "color": "#3d7ea6",
    },
    "michigan": {
        "label": "Michigan",
        "full": "Lake Michigan",
        "lwd_m": 176.0,
        "lwd_ft": 577.5,
        "color": "#2f6f7e",
        "note": "Hydraulically one lake with Huron",
    },
    "huron": {
        "label": "Huron",
        "full": "Lake Huron",
        "lwd_m": 176.0,
        "lwd_ft": 577.5,
        "color": "#4a90a4",
        "note": "Hydraulically one lake with Michigan",
    },
    "st_clair": {
        "label": "St. Clair",
        "full": "Lake St. Clair",
        "lwd_m": 174.4,
        "lwd_ft": 572.2,
        "color": "#5fa8bc",
    },
    "erie": {
        "label": "Erie",
        "full": "Lake Erie",
        "lwd_m": 173.5,
        "lwd_ft": 569.2,
        "color": "#6aa8b8",
    },
    "ontario": {
        "label": "Ontario",
        "full": "Lake Ontario",
        "lwd_m": 74.2,
        "lwd_ft": 243.4,
        "color": "#1a3a4a",
    },
}

# Overlay anchors on the Michigan Sea Grant profile, as fractions of image size.
# Centres sit in the lake water (Superior, Michigan hole, Erie, Ontario).
PROFILE_ANCHORS = {
    "superior": (0.112, 0.555),
    "huron": (0.278, 0.57),
    "michigan": (0.278, 0.685),
    "st_clair": (0.418, 0.22),
    "erie": (0.448, 0.43),
    "ontario": (0.555, 0.57),
}

# Water-basin clips for the century animation (fractions of the profile graphic).
# y_lwd is the printed water surface (chart datum / Low Water Datum).
PROFILE_BASINS = [
    {
        "key": "superior",
        "y_lwd": 0.300,
        "label": (0.11, 0.50),
        "poly": [
            (0.012, 0.42), (0.018, 0.300), (0.205, 0.300), (0.220, 0.34),
            (0.210, 0.50), (0.175, 0.775), (0.115, 0.775), (0.055, 0.58),
            (0.020, 0.48),
        ],
    },
    {
        "key": "michigan_huron",
        "y_lwd": 0.334,
        "label": (0.292, 0.48),
        "poly": [
            (0.232, 0.334), (0.358, 0.334), (0.355, 0.415), (0.328, 0.430),
            (0.322, 0.690), (0.275, 0.690), (0.268, 0.430), (0.235, 0.400),
        ],
    },
    {
        "key": "st_clair",
        "y_lwd": 0.338,
        "label": (0.384, 0.28),
        "poly": [
            (0.368, 0.338), (0.398, 0.338), (0.396, 0.385), (0.370, 0.385),
        ],
    },
    {
        "key": "erie",
        "y_lwd": 0.348,
        "label": (0.452, 0.41),
        "poly": [
            (0.405, 0.348), (0.498, 0.348), (0.492, 0.490), (0.455, 0.475),
            (0.408, 0.410),
        ],
    },
    {
        "key": "ontario",
        "y_lwd": 0.418,
        "label": (0.560, 0.54),
        "poly": [
            (0.508, 0.418), (0.618, 0.418), (0.608, 0.675), (0.545, 0.640),
            (0.512, 0.500),
        ],
    },
]

# NOAA CO-OPS master / long-record gauges for the 1918–present animation.
HISTORY_GAUGES = [
    {"key": "superior", "label": "Superior", "ids": ["9099064"], "names": ["Duluth"], "lwd": 183.2, "color": "#3d7ea6"},
    {"key": "michigan_huron", "label": "Michigan–Huron", "ids": ["9075014"], "names": ["Harbor Beach"], "lwd": 176.0, "color": "#2f6f7e"},
    {"key": "st_clair", "label": "St. Clair", "ids": ["9034052", "9014070"], "names": ["St Clair Shores", "Algonac"], "lwd": 174.4, "color": "#5fa8bc"},
    {"key": "erie", "label": "Erie", "ids": ["9063063"], "names": ["Cleveland"], "lwd": 173.5, "color": "#6aa8b8"},
    {"key": "ontario", "label": "Ontario", "ids": ["9052030"], "names": ["Oswego"], "lwd": 74.2, "color": "#1a3a4a"},
]

HISTORY_NARRATIVE = [
    (1926, "Dry 1920s: Superior and St. Clair sit near record lows"),
    (1934, "Dust Bowl years: Erie falls toward its lowest monthly means"),
    (1964, "Michigan–Huron’s crisis low — the benchmark drought year"),
    (1973, "Ontario’s record-high period after a wet early 1970s"),
    (1986, "Mid-1980s high water: Superior near its record high"),
    (1997, "Another high-water peak on the middle lakes"),
    (2013, "Michigan–Huron near the 1964 low after a long decline"),
    (2020, "Record highs on Michigan–Huron, St. Clair, and Erie"),
]

# CHS IWLS stations (operating permanent gauges on the lakes and connecting rivers).
CHS_STATIONS = [
    {"code": "10050", "id": "5cebf1e23d0f4a073c4bc0fc", "name": "Thunder Bay", "lake": "superior", "kind": "lake", "primary": True, "lat": 48.409, "lon": -89.217},
    {"code": "10220", "id": "5cebf1e43d0f4a073c4bc376", "name": "Rossport", "lake": "superior", "kind": "lake", "lat": 48.834, "lon": -87.520},
    {"code": "10750", "id": "5cebf1e43d0f4a073c4bc37e", "name": "Michipicoten", "lake": "superior", "kind": "lake", "lat": 47.961, "lon": -84.901},
    {"code": "10920", "id": "5cebf1e43d0f4a073c4bc385", "name": "Gros Cap", "lake": "st_marys", "kind": "channel", "lat": 46.529, "lon": -84.586},
    {"code": "10980", "id": "5cebf1e43d0f4a073c4bc387", "name": "Sault Ste. Marie above locks", "lake": "st_marys", "kind": "channel", "primary": True, "lat": 46.512, "lon": -84.367},
    {"code": "11010", "id": "5cebf1e43d0f4a073c4bc389", "name": "Sault Ste. Marie below locks", "lake": "st_marys", "kind": "channel", "primary": True, "lat": 46.511, "lon": -84.343},
    {"code": "11070", "id": "5cebf1e43d0f4a073c4bc38f", "name": "Thessalon", "lake": "huron", "kind": "lake", "lat": 46.254, "lon": -83.551},
    {"code": "11195", "id": "5cebf1e43d0f4a073c4bc391", "name": "Little Current", "lake": "huron", "kind": "lake", "lat": 45.981, "lon": -81.924},
    {"code": "11690", "id": "5cebf1e43d0f4a073c4bc39d", "name": "Tobermory", "lake": "huron", "kind": "lake", "primary": True, "lat": 45.257, "lon": -81.663},
    {"code": "11860", "id": "5cebf1e43d0f4a073c4bc3a3", "name": "Goderich", "lake": "huron", "kind": "lake", "lat": 43.745, "lon": -81.728},
    {"code": "11500", "id": "5cebf1e43d0f4a073c4bc397", "name": "Collingwood", "lake": "huron", "kind": "lake", "lat": 44.508, "lon": -80.220},
    {"code": "11375", "id": "5cebf1e43d0f4a073c4bc487", "name": "Parry Sound", "lake": "huron", "kind": "lake", "lat": 45.339, "lon": -80.036},
    {"code": "11445", "id": "5cebf1e23d0f4a073c4bc102", "name": "Midland", "lake": "huron", "kind": "lake", "lat": 44.753, "lon": -79.888},
    {"code": "11940", "id": "5cebf1e43d0f4a073c4bc3a9", "name": "Point Edward", "lake": "st_clair_river", "kind": "channel", "lat": 42.991, "lon": -82.421},
    {"code": "11950", "id": "5cebf1e43d0f4a073c4bc3af", "name": "Port Lambton", "lake": "st_clair_river", "kind": "channel", "lat": 42.657, "lon": -82.507},
    {"code": "11965", "id": "5cebf1e43d0f4a073c4bc3b6", "name": "Belle River", "lake": "st_clair", "kind": "lake", "primary": True, "lat": 42.296, "lon": -82.711},
    {"code": "11995", "id": "5cebf1e43d0f4a073c4bc3bc", "name": "Amherstburg", "lake": "detroit_river", "kind": "channel", "lat": 42.144, "lon": -83.114},
    {"code": "12005", "id": "5cebf1e43d0f4a073c4bc3be", "name": "Bar Point", "lake": "detroit_river", "kind": "channel", "lat": 42.062, "lon": -83.115},
    {"code": "12065", "id": "5cebf1e43d0f4a073c4bc3c0", "name": "Kingsville", "lake": "erie", "kind": "lake", "lat": 42.027, "lon": -82.735},
    {"code": "12250", "id": "5cebf1e43d0f4a073c4bc3c2", "name": "Erieau", "lake": "erie", "kind": "lake", "lat": 42.260, "lon": -81.915},
    {"code": "12400", "id": "5cebf1e43d0f4a073c4bc3c8", "name": "Port Stanley", "lake": "erie", "kind": "lake", "primary": True, "lat": 42.658, "lon": -81.214},
    {"code": "12710", "id": "5cebf1e43d0f4a073c4bc4ac", "name": "Port Dover", "lake": "erie", "kind": "lake", "lat": 42.781, "lon": -80.202},
    {"code": "12865", "id": "5cebf1e43d0f4a073c4bc3cf", "name": "Port Colborne", "lake": "erie", "kind": "lake", "lat": 42.874, "lon": -79.253},
    {"code": "13030", "id": "5cebf1e43d0f4a073c4bc3d8", "name": "Port Weller", "lake": "ontario", "kind": "lake", "lat": 43.237, "lon": -79.220},
    {"code": "13150", "id": "5cebf1e43d0f4a073c4bc3df", "name": "Burlington", "lake": "ontario", "kind": "lake", "lat": 43.299, "lon": -79.793},
    {"code": "13320", "id": "5cebf1e43d0f4a073c4bc3e5", "name": "Toronto", "lake": "ontario", "kind": "lake", "primary": True, "lat": 43.640, "lon": -79.380},
    {"code": "13590", "id": "5cebf1e43d0f4a073c4bc3eb", "name": "Cobourg", "lake": "ontario", "kind": "lake", "lat": 43.956, "lon": -78.164},
    {"code": "13988", "id": "5cebf1e43d0f4a073c4bc3f1", "name": "Kingston", "lake": "ontario", "kind": "lake", "lat": 44.218, "lon": -76.518},
    {"code": "14400", "id": "5cebf1e43d0f4a073c4bc3f7", "name": "Brockville", "lake": "st_lawrence", "kind": "channel", "lat": 44.587, "lon": -75.682},
    {"code": "14600", "id": "5cebf1e43d0f4a073c4bc470", "name": "Iroquois above", "lake": "st_lawrence", "kind": "channel", "primary": True, "lat": 44.822, "lon": -75.321},
    {"code": "14602", "id": "5cebf1e03d0f4a073c4bbd5d", "name": "Iroquois below", "lake": "st_lawrence", "kind": "channel", "primary": True, "lat": 44.835, "lon": -75.309},
    {"code": "14870", "id": "5cebf1e03d0f4a073c4bbd70", "name": "Cornwall", "lake": "st_lawrence", "kind": "channel", "lat": 45.015, "lon": -74.712},
]

NOAA_STATIONS = [
    {"id": "9099064", "name": "Duluth", "lake": "superior"},
    {"id": "9087057", "name": "Milwaukee", "lake": "michigan"},
    {"id": "9087044", "name": "Calumet Harbor", "lake": "michigan"},
    {"id": "9087023", "name": "Ludington", "lake": "michigan"},
    {"id": "9075080", "name": "Mackinaw City", "lake": "huron"},
    {"id": "9075014", "name": "Harbor Beach", "lake": "huron"},
    {"id": "9034052", "name": "St. Clair Shores", "lake": "st_clair"},
    {"id": "9063063", "name": "Toledo", "lake": "erie"},
    {"id": "9063053", "name": "Marblehead", "lake": "erie"},
    {"id": "9052058", "name": "Rochester", "lake": "ontario"},
    {"id": "9052030", "name": "Oswego", "lake": "ontario"},
]

LAKE_POINTS = {
    "superior": (47.7, -87.5),
    "michigan": (44.0, -87.0),
    "huron": (45.0, -82.4),
    "st_clair": (42.47, -82.67),
    "erie": (42.2, -81.2),
    "ontario": (43.6, -77.9),
}

# Simplified shorelines for schematic maps (lon, lat).
LAKE_POLYS = {
    "superior": [
        (-92.10, 46.75), (-91.20, 46.90), (-90.40, 46.50), (-89.90, 46.85),
        (-88.90, 47.00), (-87.90, 46.85), (-86.80, 46.50), (-84.80, 46.48),
        (-84.55, 46.65), (-84.85, 47.05), (-85.70, 47.50), (-86.60, 47.60),
        (-87.50, 48.05), (-88.10, 48.35), (-89.20, 48.80), (-89.85, 48.05),
        (-90.20, 48.00), (-90.90, 47.45), (-92.10, 46.75),
    ],
    "michigan": [
        (-87.05, 41.62), (-86.50, 41.76), (-86.70, 42.30), (-86.25, 42.80),
        (-86.50, 43.60), (-86.55, 44.50), (-85.70, 44.90), (-85.10, 44.88),
        (-85.00, 45.40), (-85.05, 45.85), (-85.60, 45.95), (-86.10, 45.35),
        (-86.90, 45.35), (-87.65, 45.08), (-87.95, 44.55), (-87.70, 43.70),
        (-87.90, 42.90), (-87.50, 41.80), (-87.05, 41.62),
    ],
    "huron": [
        (-84.75, 45.95), (-84.10, 46.05), (-83.40, 46.10), (-82.40, 46.05),
        (-81.70, 46.00), (-80.90, 45.90), (-80.40, 45.55), (-80.10, 45.20),
        (-79.90, 44.85), (-80.20, 44.50), (-80.60, 44.55), (-81.20, 44.70),
        (-81.70, 44.75), (-81.75, 44.20), (-81.85, 43.60), (-82.40, 43.05),
        (-82.55, 43.00), (-82.45, 43.60), (-82.80, 44.10), (-83.50, 44.00),
        (-83.90, 43.85), (-83.50, 44.60), (-83.00, 45.20), (-83.50, 45.55),
        (-84.20, 45.80), (-84.75, 45.95),
    ],
    "st_clair": [
        (-82.90, 42.55), (-82.55, 42.65), (-82.40, 42.55), (-82.45, 42.32),
        (-82.68, 42.30), (-82.90, 42.40), (-82.90, 42.55),
    ],
    "erie": [
        (-83.45, 41.70), (-83.10, 41.55), (-82.20, 41.45), (-81.20, 41.40),
        (-80.20, 41.60), (-79.10, 42.20), (-78.90, 42.85), (-79.20, 42.90),
        (-80.50, 42.55), (-81.30, 42.70), (-82.20, 42.35), (-83.10, 42.05),
        (-83.45, 41.70),
    ],
    "ontario": [
        (-79.80, 43.30), (-79.35, 43.25), (-78.20, 43.25), (-76.80, 43.25),
        (-76.20, 43.50), (-76.20, 44.15), (-76.80, 44.20), (-77.90, 44.10),
        (-79.10, 43.85), (-79.80, 43.55), (-79.80, 43.30),
    ],
}

ctx = ssl.create_default_context()
try:
    import certifi

    ctx = ssl.create_default_context(cafile=certifi.where())
except Exception:
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

_last_iwls = 0.0


def _asset_v(path: Path) -> str:
    if not path.exists():
        return "0"
    return hashlib.md5(path.read_bytes()).hexdigest()[:8]


def _iwls_wait() -> None:
    global _last_iwls
    gap = IWLS_GAP_S - (time.monotonic() - _last_iwls)
    if gap > 0:
        time.sleep(gap)
    _last_iwls = time.monotonic()


def fetch_bytes(url: str, *, attempts: int = 4, timeout: float = 45, iwls: bool = False) -> bytes:
    last: Exception | None = None
    for i in range(attempts):
        try:
            if iwls:
                _iwls_wait()
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                return resp.read()
        except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
            last = e
            if isinstance(e, urllib.error.HTTPError) and e.code in (400, 401, 403, 404):
                raise
            if i + 1 >= attempts:
                break
            wait = min(20, 2**i)
            print(f"  fetch retry {i + 1}/{attempts} {type(e).__name__}: {e}; sleep {wait}s", flush=True)
            time.sleep(wait)
    raise urllib.error.URLError(f"Failed after {attempts} attempts: {last}")


def fetch_json(url: str, **kw) -> dict | list:
    raw = fetch_bytes(url, **kw)
    return json.loads(raw.decode("utf-8"))


def fetch_text(url: str, **kw) -> str:
    return fetch_bytes(url, **kw).decode("utf-8", errors="replace")


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    s = raw.strip()
    try:
        if s.endswith("Z"):
            return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def fmt_edt(dt: datetime | None) -> str:
    if isinstance(dt, str):
        dt = _parse_iso(dt)
    if dt is None:
        return "unavailable"
    return dt.astimezone(TZ).strftime("%Y-%m-%d %H:%M")


def ticker_markup(delta: float | None, *, flat: float, digits: int = 1, unit: str = "") -> str:
    if delta is None or (isinstance(delta, float) and math.isnan(delta)):
        return '<span class="ticker flat" title="No prior reading">–</span>'
    if abs(delta) < flat:
        return '<span class="ticker flat" title="Little change">–</span>'
    arrow = "▲" if delta > 0 else "▼"
    cls = "up" if delta > 0 else "down"
    return (
        f'<span class="ticker {cls}" title="Change vs previous period">'
        f"{arrow} {delta:+.{digits}f}{unit}</span>"
    )


def trend_word(delta: float | None, *, flat: float, unit: str) -> str:
    if delta is None:
        return "n/a"
    if abs(delta) < flat:
        return f"steady ({delta:+.1f}{unit})"
    direction = "rising" if delta > 0 else "falling"
    return f"{direction} {delta:+.1f}{unit}"


def load_font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _median(vals: list[float]) -> float:
    s = sorted(vals)
    n = len(s)
    if n == 0:
        raise ValueError("empty")
    if n % 2:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------


def fetch_iwls_latest() -> dict[str, dict]:
    print("IWLS latest observations…")
    rows = fetch_json(f"{IWLS}/api/v1/stations/data/latest", iwls=True)
    out: dict[str, dict] = {}
    for row in rows:
        sid = row.get("stationId")
        pts = row.get("measurementDTOs") or []
        if not sid or not pts:
            continue
        last = pts[-1]
        out[sid] = {
            "value": float(last["value"]),
            "when": _parse_iso(last.get("eventDate")),
            "qc": str(last.get("qcFlagCode") or ""),
        }
    print(f"  {len(out)} stations with latest wlo")
    return out


def fetch_station_meta(stn: dict) -> dict:
    meta = fetch_json(f"{IWLS}/api/v1/stations/{stn['id']}/metadata", iwls=True)
    offset = None
    for d in meta.get("datums") or []:
        if d.get("code") in ("IGLD85", "IGLD1985", "CDIGLD1985"):
            offset = float(d["offset"])
            break
    if offset is None:
        for d in meta.get("datums") or []:
            if d.get("code") == "IGLD85" or "IGLD" in str(d.get("code") or ""):
                offset = float(d["offset"])
                break
    return {
        "offset_igld85": offset,
        "status": meta.get("status"),
        "lat": meta.get("latitude"),
        "lon": meta.get("longitude"),
        "datums": meta.get("datums") or [],
    }


def load_or_fetch_meta(stations: list[dict]) -> dict[str, dict]:
    cache_path = DATA / "station_meta.json"
    cached: dict[str, dict] = {}
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
        except Exception:
            cached = {}
    missing = [s for s in stations if s["code"] not in cached or cached[s["code"]].get("offset_igld85") is None]
    if missing:
        print(f"IWLS metadata for {len(missing)} stations…")
        for s in missing:
            try:
                cached[s["code"]] = fetch_station_meta(s)
                print(f"  {s['code']} {s['name']} IGLD offset={cached[s['code']].get('offset_igld85')}")
            except Exception as e:
                print(f"  meta failed {s['code']} {s['name']}: {e}")
        DATA.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cached, indent=2, default=str))
    return cached


def fetch_igld_daily(stn: dict, days: int = 28) -> list[dict]:
    # IWLS rejects windows longer than 31 days (measured in EST).
    to = date.today() + timedelta(days=1)
    frm = to - timedelta(days=min(days, 28))
    url = (
        f"{IWLS}/api/v1/stations/{stn['id']}/stats/calculate-daily-means-igld85"
        f"?from={frm.isoformat()}&to={to.isoformat()}"
    )
    rows = fetch_json(url, iwls=True)
    out = []
    for r in rows or []:
        if r.get("dailyMean_IGLD85") is None:
            continue
        out.append(
            {
                "date": r["date"][:10],
                "igld85": float(r["dailyMean_IGLD85"]),
                "cd": float(r.get("dailyMean_CDIGLD85") or r.get("dailyMean") or 0),
            }
        )
    return out


def fetch_hourly_wlo(stn: dict, days: int = 3) -> list[dict]:
    now = datetime.now(timezone.utc)
    frm = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    to = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    url = (
        f"{IWLS}/api/v1/stations/{stn['id']}/data?time-series-code=wlo"
        f"&from={frm}&to={to}&resolution=SIXTY_MINUTES"
    )
    rows = fetch_json(url, iwls=True)
    out = []
    for r in rows or []:
        if r.get("value") is None:
            continue
        out.append({"when": _parse_iso(r.get("eventDate")), "cd": float(r["value"])})
    return [p for p in out if p["when"]]


def fetch_wlf(stn: dict) -> list[dict]:
    now = datetime.now(timezone.utc)
    frm = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    to = (now + timedelta(days=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = (
        f"{IWLS}/api/v1/stations/{stn['id']}/data?time-series-code=wlf"
        f"&from={frm}&to={to}&resolution=SIXTY_MINUTES"
    )
    try:
        rows = fetch_json(url, iwls=True)
    except Exception as e:
        print(f"  wlf failed {stn['name']}: {e}")
        return []
    out = []
    for r in rows or []:
        if r.get("value") is None:
            continue
        out.append({"when": _parse_iso(r.get("eventDate")), "cd": float(r["value"])})
    return [p for p in out if p["when"]]


def fetch_noaa_station(stn: dict) -> dict:
    sid = stn["id"]
    base = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
    common = f"station={sid}&units=metric&time_zone=gmt&format=json&application=great-lakes-levels"
    out: dict = {"id": sid, "name": stn["name"], "lake": stn["lake"], "source": "NOAA CO-OPS"}
    try:
        js = fetch_json(f"{base}?date=latest&product=water_level&datum=IGLD&{common}", timeout=30)
        if js.get("data"):
            d = js["data"][-1]
            out["igld85"] = float(d["v"])
            out["when"] = _parse_iso(d.get("t") + "Z" if d.get("t") and "Z" not in d["t"] else d.get("t"))
            meta = js.get("metadata") or {}
            out["name"] = meta.get("name") or stn["name"]
            if meta.get("lat"):
                out["lat"] = float(meta["lat"])
            if meta.get("lon"):
                out["lon"] = float(meta["lon"])
    except Exception as e:
        print(f"  NOAA level {sid}: {e}")
    try:
        js = fetch_json(f"{base}?date=recent&product=water_level&datum=IGLD&{common}", timeout=30)
        vals = []
        for d in js.get("data") or []:
            try:
                vals.append((_parse_iso(d["t"] + "Z" if "Z" not in d["t"] else d["t"]), float(d["v"])))
            except Exception:
                continue
        vals = [(t, v) for t, v in vals if t]
        if len(vals) >= 2:
            latest_t, latest_v = vals[-1]
            target = latest_t - timedelta(hours=24)
            prev = min(vals, key=lambda p: abs(p[0] - target))
            out["d24_cm"] = (latest_v - prev[1]) * 100
            if "igld85" not in out:
                out["igld85"] = latest_v
                out["when"] = latest_t
    except Exception as e:
        print(f"  NOAA recent {sid}: {e}")
    for product, key in (("wind", "wind"), ("water_temperature", "wtemp")):
        try:
            js = fetch_json(f"{base}?date=latest&product={product}&{common}", timeout=20)
            if js.get("error") or not js.get("data"):
                continue
            d = js["data"][-1]
            if product == "wind":
                if d.get("s") not in (None, ""):
                    out["wind_ms"] = float(d["s"])
                if d.get("d") not in (None, ""):
                    out["wind_dir"] = float(d["d"])
            else:
                if d.get("v") not in (None, ""):
                    out["wtemp_c"] = float(d["v"])
        except Exception:
            continue
    return out


def fetch_ndbc() -> list[dict]:
    print("NDBC latest observations…")
    try:
        text = fetch_text("https://www.ndbc.noaa.gov/data/latest_obs/latest_obs.txt", timeout=40)
    except Exception as e:
        print(f"  NDBC failed: {e}")
        return []
    buoys = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        p = line.split()
        if len(p) < 17:
            continue
        try:
            lat, lon = float(p[1]), float(p[2])
        except ValueError:
            continue
        if not (41.0 <= lat <= 49.5 and -93.0 <= lon <= -75.0):
            continue
        # Keep Great Lakes buoys (45xxx); drop land C-MAN / METAR rows in the bbox.
        if not (p[0].startswith("45") and p[0][2:].isdigit()):
            continue

        def num(i: int) -> float | None:
            if i >= len(p) or p[i] in ("MM", "999", "99.0"):
                return None
            try:
                return float(p[i])
            except ValueError:
                return None

        when = None
        try:
            when = datetime(int(p[3]), int(p[4]), int(p[5]), int(p[6]), int(p[7]), tzinfo=timezone.utc)
        except Exception:
            pass
        lake = _lake_for_point(lat, lon)
        buoys.append(
            {
                "id": p[0],
                "lat": lat,
                "lon": lon,
                "lake": lake,
                "when": when,
                "wind_dir": num(8),
                "wind_ms": num(9),
                "gust_ms": num(10),
                "wvht_m": num(11),
                "dpd_s": num(12),
                "atmp_c": num(16),
                "wtemp_c": num(17),
            }
        )
    print(f"  {len(buoys)} Great Lakes NDBC platforms")
    return buoys


def _lake_for_point(lat: float, lon: float) -> str:
    if lat >= 46.3:
        return "superior"
    if lon <= -85.0 and 41.5 <= lat <= 46.2:
        return "michigan"
    if 42.25 <= lat <= 42.7 and -83.1 <= lon <= -82.3:
        return "st_clair"
    if lat <= 42.95 and lon <= -78.8:
        return "erie"
    if 43.1 <= lat <= 44.3 and lon >= -80.0:
        return "ontario"
    return "huron"


def fetch_glsea() -> dict:
    print("GLSEA lake-average SST…")
    url = "https://apps.glerl.noaa.gov/coastwatch/ftp/glsea/avgtemps/glsea-temps_1024_3.dat"
    text = fetch_text(url, timeout=40)
    rows = []
    for line in text.splitlines():
        p = line.split()
        if len(p) < 8 or not p[0].isdigit():
            continue
        year, doy = int(p[0]), int(p[1])
        rows.append(
            {
                "year": year,
                "doy": doy,
                "superior": float(p[2]),
                "michigan": float(p[3]),
                "huron": float(p[4]),
                "erie": float(p[5]),
                "ontario": float(p[6]),
                "st_clair": float(p[7]),
            }
        )
    if not rows:
        return {}
    latest = rows[-1]
    d7 = rows[-8] if len(rows) >= 8 else rows[0]
    last_year_rows = [r for r in rows if r["year"] == latest["year"] - 1]
    last_year = None
    if last_year_rows:
        last_year = min(last_year_rows, key=lambda r: abs(r["doy"] - latest["doy"]))
    return {"latest": latest, "d7": d7, "last_year": last_year, "history": rows[-40:]}


def fetch_open_meteo() -> dict[str, dict]:
    print("Open-Meteo winds and waves…")
    out: dict[str, dict] = {}
    for lake, (lat, lon) in LAKE_POINTS.items():
        rec: dict = {"lat": lat, "lon": lon}
        try:
            js = fetch_json(
                "https://api.open-meteo.com/v1/forecast?"
                + urllib.parse.urlencode(
                    {
                        "latitude": lat,
                        "longitude": lon,
                        "current": "wind_speed_10m,wind_direction_10m,temperature_2m",
                        "wind_speed_unit": "ms",
                    }
                ),
                timeout=25,
            )
            cur = js.get("current") or {}
            rec["wind_ms"] = cur.get("wind_speed_10m")
            rec["wind_dir"] = cur.get("wind_direction_10m")
            rec["atmp_c"] = cur.get("temperature_2m")
        except Exception as e:
            print(f"  Open-Meteo wind {lake}: {e}")
        try:
            js = fetch_json(
                "https://marine-api.open-meteo.com/v1/marine?"
                + urllib.parse.urlencode(
                    {
                        "latitude": lat,
                        "longitude": lon,
                        "current": "wave_height,wave_direction,wave_period",
                    }
                ),
                timeout=25,
            )
            cur = js.get("current") or {}
            rec["wvht_m"] = cur.get("wave_height")
            rec["wave_dir"] = cur.get("wave_direction")
            rec["wave_period"] = cur.get("wave_period")
        except Exception as e:
            print(f"  Open-Meteo marine {lake}: {e}")
        out[lake] = rec
    return out


# ---------------------------------------------------------------------------
# Century history (NOAA CO-OPS monthly means)
# ---------------------------------------------------------------------------


def _month_index(year: int, month: int, start_year: int = 1918) -> int:
    return (year - start_year) * 12 + (month - 1)


def _index_to_year_month(idx: int, start_year: int = 1918) -> tuple[int, int]:
    return start_year + idx // 12, idx % 12 + 1


def _fill_short_gaps(vals: list[float | None], max_gap: int = 18) -> list[float | None]:
    out = list(vals)
    n = len(out)
    i = 0
    while i < n:
        if out[i] is not None:
            i += 1
            continue
        j = i
        while j < n and out[j] is None:
            j += 1
        gap = j - i
        left = out[i - 1] if i > 0 else None
        right = out[j] if j < n else None
        if gap <= max_gap and left is not None and right is not None:
            for k in range(i, j):
                t = (k - i + 1) / (gap + 1)
                out[k] = left + (right - left) * t
        i = j
    return out


def _rolling_mean(vals: list[float | None], win: int = 12) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(vals)):
        chunk = [v for v in vals[max(0, i - win + 1) : i + 1] if v is not None]
        out.append(round(sum(chunk) / len(chunk), 3) if chunk else None)
    return out


def _annual_means(vals: list[float | None], start_year: int) -> tuple[list[int], list[float | None]]:
    n_years = math.ceil(len(vals) / 12)
    years, annual = [], []
    for yi in range(n_years):
        chunk = [v for v in vals[yi * 12 : yi * 12 + 12] if v is not None]
        years.append(start_year + yi)
        annual.append(round(sum(chunk) / len(chunk), 3) if len(chunk) >= 6 else None)
    return years, annual


def fetch_noaa_monthly(station_id: str, begin: date, end: date) -> dict[tuple[int, int], float]:
    out: dict[tuple[int, int], float] = {}
    year = begin.year
    while date(year, 1, 1) <= end:
        chunk_end_year = min(year + 9, end.year)
        b = date(year, 1, 1) if year > begin.year else begin
        e = date(chunk_end_year, 12, 31) if chunk_end_year < end.year else end
        url = (
            "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?"
            + urllib.parse.urlencode(
                {
                    "product": "monthly_mean",
                    "application": "great-lakes-levels",
                    "station": station_id,
                    "begin_date": b.strftime("%Y%m%d"),
                    "end_date": e.strftime("%Y%m%d"),
                    "datum": "IGLD",
                    "units": "metric",
                    "time_zone": "gmt",
                    "format": "json",
                }
            )
        )
        try:
            js = fetch_json(url, timeout=40)
        except Exception as exc:
            print(f"    NOAA monthly {station_id} {b.year}-{e.year}: {exc}")
            year = chunk_end_year + 1
            continue
        if js.get("error"):
            year = chunk_end_year + 1
            continue
        for row in js.get("data") or []:
            try:
                msl = row.get("MSL")
                if msl in (None, ""):
                    continue
                out[(int(row["year"]), int(row["month"]))] = float(msl)
            except (TypeError, ValueError, KeyError):
                continue
        year = chunk_end_year + 1
        time.sleep(0.12)
    return out


def fetch_history(existing: dict | None = None) -> dict:
    print("NOAA CO-OPS monthly means for century animation…")
    start_year = 1918
    today = datetime.now(timezone.utc).date()
    end = today.replace(day=1) - timedelta(days=1)  # last complete month
    if end.year < start_year:
        end = today
    n_months = _month_index(end.year, end.month, start_year) + 1

    cached_lakes = ((existing or {}).get("lakes") or {}) if existing else {}
    lakes_out: dict[str, dict] = {}

    for spec in HISTORY_GAUGES:
        key = spec["key"]
        merged: dict[tuple[int, int], float] = {}
        prior = cached_lakes.get(key) or {}
        prior_monthly = prior.get("monthly") or []
        prior_start = int((existing or {}).get("start_year") or start_year)
        for i, val in enumerate(prior_monthly):
            if val is None:
                continue
            y, m = _index_to_year_month(i, prior_start)
            merged[(y, m)] = float(val)

        last_cached = None
        if merged:
            last_cached = max(merged)
        fetch_from = date(start_year, 1, 1)
        if last_cached and last_cached[0] >= today.year - 2:
            fetch_from = date(max(start_year, last_cached[0] - 1), 1, 1)
            print(f"  {spec['label']}: refresh {fetch_from.year}–{end.year}")
        else:
            print(f"  {spec['label']}: backfill {start_year}–{end.year}")

        for sid in spec["ids"]:
            got = fetch_noaa_monthly(sid, fetch_from, end)
            print(f"    station {sid}: {len(got)} months")
            # Prefer the first station when both have a value.
            for ym, val in got.items():
                if sid == spec["ids"][0] or ym not in merged:
                    merged[ym] = val

        monthly: list[float | None] = []
        for i in range(n_months):
            y, m = _index_to_year_month(i, start_year)
            monthly.append(merged.get((y, m)))
        monthly = _fill_short_gaps(monthly)
        smooth = _rolling_mean(monthly, 12)
        years, annual = _annual_means(monthly, start_year)
        present = [v for v in monthly if v is not None]
        mean_v = round(sum(present) / len(present), 3) if present else None
        lakes_out[key] = {
            "label": spec["label"],
            "lwd": spec["lwd"],
            "color": spec["color"],
            "stations": list(zip(spec["ids"], spec["names"])),
            "mean": mean_v,
            "monthly": [None if v is None else round(v, 3) for v in monthly],
            "smooth": smooth,
            "annual": annual,
        }

    years = list(range(start_year, start_year + math.ceil(n_months / 12)))
    events = [{"year": y, "text": t} for y, t in HISTORY_NARRATIVE]
    for spec in HISTORY_GAUGES:
        lake = lakes_out[spec["key"]]
        numbered = [(y, v) for y, v in zip(years, lake["annual"]) if v is not None]
        if not numbered:
            continue
        ymin, vmin = min(numbered, key=lambda p: p[1])
        ymax, vmax = max(numbered, key=lambda p: p[1])
        events.append({"year": ymin, "text": f"{lake['label']} lowest annual mean in this series ({vmin:.2f} m IGLD)"})
        events.append({"year": ymax, "text": f"{lake['label']} highest annual mean in this series ({vmax:.2f} m IGLD)"})
    # Keep one caption per year so the 30-second play is readable.
    by_year: dict[int, str] = {}
    for spec_year, text in HISTORY_NARRATIVE:
        by_year[spec_year] = text
    for ev in events:
        by_year.setdefault(ev["year"], ev["text"])
    events = [{"year": y, "text": t} for y, t in sorted(by_year.items())]

    return {
        "source": "NOAA CO-OPS monthly mean sea level, IGLD 1985. Master / long-record gauges; a close stand-in for coordinated lake-wide averages.",
        "start_year": start_year,
        "end": f"{end.year}-{end.month:02d}",
        "n_months": n_months,
        "duration_s": 30,
        "basins": PROFILE_BASINS,
        "lakes": lakes_out,
        "years": years,
        "events": events,
    }


# ---------------------------------------------------------------------------
# Assemble snapshot
# ---------------------------------------------------------------------------


def build_snapshot() -> dict:
    DATA.mkdir(parents=True, exist_ok=True)
    latest = fetch_iwls_latest()
    meta = load_or_fetch_meta(CHS_STATIONS)

    stations: list[dict] = []
    daily_by_code: dict[str, list[dict]] = {}
    print("IWLS IGLD 1985 daily means…")
    for stn in CHS_STATIONS:
        rec = {**stn, "source": "CHS IWLS"}
        m = meta.get(stn["code"]) or {}
        rec["lat"] = m.get("lat") or stn.get("lat")
        rec["lon"] = m.get("lon") or stn.get("lon")
        rec["offset_igld85"] = m.get("offset_igld85")
        rec["status"] = m.get("status")
        lv = latest.get(stn["id"])
        if lv:
            rec["cd"] = lv["value"]
            rec["when"] = lv["when"]
            rec["qc"] = lv["qc"]
            if rec["offset_igld85"] is not None:
                rec["igld85"] = rec["cd"] + rec["offset_igld85"]
        try:
            daily = fetch_igld_daily(stn)
            daily_by_code[stn["code"]] = daily
            rec["daily"] = daily
            if daily:
                rec["d7_cm"] = (daily[-1]["igld85"] - daily[max(0, len(daily) - 8)]["igld85"]) * 100
                if rec.get("d24_cm") is None and len(daily) >= 2:
                    rec["d24_cm"] = (daily[-1]["igld85"] - daily[-2]["igld85"]) * 100
                if rec.get("igld85") is None:
                    rec["igld85"] = daily[-1]["igld85"]
                    rec["cd"] = daily[-1]["cd"]
        except Exception as e:
            print(f"  daily failed {stn['code']} {stn['name']}: {e}")
        stations.append(rec)

    print("IWLS hourly + forecast for primary gauges…")
    for rec in stations:
        if not rec.get("primary"):
            continue
        offset = rec.get("offset_igld85")
        try:
            hourly = fetch_hourly_wlo(rec)
            rec["hourly"] = [{"when": p["when"], "igld85": p["cd"] + (offset or 0)} for p in hourly]
            if hourly and offset is not None:
                latest_h = hourly[-1]
                target = latest_h["when"] - timedelta(hours=24)
                prev = min(hourly, key=lambda p: abs(p["when"] - target))
                rec["d24_cm"] = (latest_h["cd"] - prev["cd"]) * 100
        except Exception as e:
            print(f"  hourly failed {rec['name']}: {e}")
        try:
            fc = fetch_wlf(rec)
            rec["forecast"] = [{"when": p["when"], "igld85": p["cd"] + (offset or 0), "cd": p["cd"]} for p in fc]
        except Exception as e:
            print(f"  forecast failed {rec['name']}: {e}")

    print("NOAA CO-OPS complementary gauges…")
    noaa = []
    for stn in NOAA_STATIONS:
        rec = fetch_noaa_station(stn)
        noaa.append(rec)
        time.sleep(0.25)

    ndbc = fetch_ndbc()
    glsea = fetch_glsea()
    meteo = fetch_open_meteo()

    lakes = {}
    for key, spec in LAKES.items():
        chs = [s for s in stations if s.get("lake") == key and s.get("igld85") is not None]
        # Michigan is US-only in CHS; use Huron CHS (same surface) plus NOAA Michigan.
        extra = []
        if key == "michigan":
            extra = [s for s in stations if s.get("lake") == "huron" and s.get("igld85") is not None]
            extra += [s for s in noaa if s.get("lake") == "michigan" and s.get("igld85") is not None]
        if key == "huron":
            extra = [s for s in noaa if s.get("lake") == "huron" and s.get("igld85") is not None]
        pool = chs + extra
        if not pool:
            lakes[key] = {"key": key, **spec}
            continue
        igld_vals = [s["igld85"] for s in pool]
        igld = _median(igld_vals)
        d24s = [s["d24_cm"] for s in pool if s.get("d24_cm") is not None]
        d7s = [s["d7_cm"] for s in pool if s.get("d7_cm") is not None]
        whens = [s["when"] for s in pool if s.get("when")]
        series: dict[str, list[float]] = {}
        for s in chs + extra:
            for row in s.get("daily") or []:
                series.setdefault(row["date"], []).append(row["igld85"])
        daily = [{"date": d, "igld85": mean(v)} for d, v in sorted(series.items())]
        fc_end = None
        fcs = []
        for s in chs:
            if s.get("forecast"):
                fcs.append(s["forecast"][-1]["igld85"])
        if fcs:
            fc_end = mean(fcs)
        sst = None
        sst_d7 = None
        sst_ly = None
        if glsea.get("latest"):
            sst = glsea["latest"].get(key)
            sst_d7 = (glsea.get("d7") or {}).get(key)
            sst_ly = (glsea.get("last_year") or {}).get(key)
        wind = meteo.get(key) or {}
        buoy_w = [b["wtemp_c"] for b in ndbc if b.get("lake") == key and b.get("wtemp_c") is not None]
        buoy_wave = [b["wvht_m"] for b in ndbc if b.get("lake") == key and b.get("wvht_m") is not None]
        lakes[key] = {
            "key": key,
            **spec,
            "igld85": igld,
            "vs_lwd_cm": (igld - spec["lwd_m"]) * 100,
            "d24_cm": mean(d24s) if d24s else None,
            "d7_cm": mean(d7s) if d7s else None,
            "outlook_cm": ((fc_end - igld) * 100) if fc_end is not None else None,
            "n": len(pool),
            "when": max(whens) if whens else None,
            "daily": daily,
            "sst_c": sst,
            "sst_d7_c": (sst - sst_d7) if sst is not None and sst_d7 is not None else None,
            "sst_vs_ly_c": (sst - sst_ly) if sst is not None and sst_ly is not None else None,
            "wind_ms": wind.get("wind_ms"),
            "wind_dir": wind.get("wind_dir"),
            "wvht_m": wind.get("wvht_m") if wind.get("wvht_m") is not None else (mean(buoy_wave) if buoy_wave else None),
            "buoy_wtemp_c": mean(buoy_w) if buoy_w else None,
            "atmp_c": wind.get("atmp_c"),
        }

    generated = datetime.now(timezone.utc)
    snapshot = {
        "generated": generated,
        "generated_edt": fmt_edt(generated),
        "stations": stations,
        "noaa": noaa,
        "ndbc": ndbc,
        "lakes": lakes,
        "glsea": {k: glsea[k] for k in ("latest", "d7", "last_year") if k in glsea},
        "meteo": meteo,
    }
    return snapshot


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _arrow(delta: float | None, flat: float) -> tuple[str, str]:
    if delta is None:
        return "–", "#5a7a86"
    if abs(delta) < flat:
        return "–", "#5a7a86"
    if delta > 0:
        return "▲", "#0b6e4f"
    return "▼", "#c45c26"


def render_profile(snap: dict) -> None:
    print("Rendering profile overlay…")
    im = Image.open(PROFILE_SRC).convert("RGBA")
    scale = 2
    im = im.resize((im.width * scale, im.height * scale), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(im, "RGBA")
    font_sm = load_font(13)
    font_md = load_font(15, bold=True)
    font_lg = load_font(18, bold=True)
    font_tiny = load_font(12)

    w, h = im.size
    # Caption bar
    draw.rectangle((0, h - 36, w, h), fill=(26, 58, 74, 210))
    draw.text(
        (16, h - 28),
        "Live water levels  ·  IGLD 1985  ·  vs Low Water Datum (chart datum)  ·  ▲▼ 24h / 7d   ·   Graphic modified from Michigan Sea Grant",
        font=font_tiny,
        fill=(183, 208, 218, 255),
    )

    for key, (fx, fy) in PROFILE_ANCHORS.items():
        lake = snap["lakes"].get(key) or {}
        igld = lake.get("igld85")
        x, y = int(fx * w), int(fy * h)
        card_w, card_h = (148, 78) if key == "st_clair" else (158, 82)
        x0, y0 = x - card_w // 2, y - card_h // 2
        x0 = max(8, min(w - card_w - 8, x0))
        y0 = max(8, min(h - card_h - 44, y0))
        draw.rounded_rectangle((x0, y0, x0 + card_w, y0 + card_h), radius=8, fill=(16, 40, 52, 220), outline=(142, 184, 200, 220), width=2)
        title = lake.get("label") or key.title()
        draw.text((x0 + 8, y0 + 4), title, font=font_md, fill=(255, 255, 255, 255))
        if igld is None:
            draw.text((x0 + 8, y0 + 28), "no data", font=font_sm, fill=(183, 208, 218, 255))
            continue
        vs = lake.get("vs_lwd_cm") or 0
        draw.text((x0 + 8, y0 + 22), f"{igld:.2f} m IGLD", font=font_lg, fill=(255, 255, 255, 255))
        vs_color = (125, 206, 160, 255) if vs >= 0 else (224, 122, 95, 255)
        draw.text((x0 + 8, y0 + 42), f"{vs:+.0f} cm vs LWD", font=font_sm, fill=vs_color)
        a24, c24 = _arrow(lake.get("d24_cm"), 0.4)
        a7, c7 = _arrow(lake.get("d7_cm"), 1.0)
        d24 = lake.get("d24_cm")
        d7 = lake.get("d7_cm")
        t24 = "n/a" if d24 is None else f"{d24:+.1f} cm"
        t7 = "n/a" if d7 is None else f"{d7:+.1f} cm"
        draw.text((x0 + 8, y0 + 58), f"{a24} 24h {t24}  {a7} 7d {t7}", font=font_tiny, fill=(183, 208, 218, 255))

    im.convert("RGB").save(PROFILE_PNG, "PNG", optimize=True)


def _style_map(ax, title: str) -> None:
    ax.set_xlim(-92.6, -75.8)
    ax.set_ylim(41.2, 49.2)
    ax.set_aspect("equal")
    ax.set_facecolor("#eef2f4")
    ax.set_title(title, fontsize=12, color="#1a3a4a", pad=8, fontweight="bold")
    ax.set_xlabel("Longitude", fontsize=8, color="#5a7a86")
    ax.set_ylabel("Latitude", fontsize=8, color="#5a7a86")
    ax.tick_params(labelsize=7, colors="#5a7a86")
    for spine in ax.spines.values():
        spine.set_color("#d5dde3")


def _draw_lakes(ax, facecolor="#c5dce4", edge="#2f6f7e") -> None:
    for key, poly in LAKE_POLYS.items():
        ax.add_patch(
            Polygon([(x, y) for x, y in poly], closed=True, facecolor=facecolor, edgecolor=edge, linewidth=0.8, zorder=1)
        )


def _cmap_color(val: float, vmin: float, vmax: float, cmap_name: str) -> tuple:
    cmap = plt.get_cmap(cmap_name)
    if vmax == vmin:
        t = 0.5
    else:
        t = max(0.0, min(1.0, (val - vmin) / (vmax - vmin)))
    r, g, b, a = cmap(t)
    return (r, g, b, a)


def render_map_levels(snap: dict) -> None:
    print("Rendering levels map…")
    fig, ax = plt.subplots(figsize=(11.2, 7.2), dpi=120)
    _draw_lakes(ax)
    _style_map(ax, "Water levels vs Low Water Datum (IGLD 1985)")
    # Color lakes by vs LWD
    vs_vals = [snap["lakes"][k].get("vs_lwd_cm") for k in LAKE_POLYS if snap["lakes"].get(k, {}).get("vs_lwd_cm") is not None]
    vmin, vmax = (-40, 80) if not vs_vals else (min(-10, min(vs_vals) - 5), max(40, max(vs_vals) + 5))
    for key, poly in LAKE_POLYS.items():
        vs = (snap["lakes"].get(key) or {}).get("vs_lwd_cm")
        if vs is None:
            continue
        color = _cmap_color(vs, vmin, vmax, "RdYlBu_r")
        ax.add_patch(Polygon([(x, y) for x, y in poly], closed=True, facecolor=color, edgecolor="#1a3a4a", linewidth=0.9, zorder=2, alpha=0.85))
        lat, lon = LAKE_POINTS[key]
        lake = snap["lakes"][key]
        a, _ = _arrow(lake.get("d24_cm"), 0.4)
        label = f"{lake['label']}\n{lake['igld85']:.2f} m\n{lake['vs_lwd_cm']:+.0f} cm {a}"
        ax.text(lon, lat, label, ha="center", va="center", fontsize=7.5, color="#1a3a4a", zorder=5, fontweight="bold")

    for s in snap["stations"]:
        if s.get("lon") is None or s.get("lat") is None or s.get("igld85") is None:
            continue
        ax.plot(s["lon"], s["lat"], "o", color="#1a3a4a", markersize=3.2, zorder=4)
    sm = plt.cm.ScalarMappable(cmap=plt.get_cmap("RdYlBu_r"), norm=plt.Normalize(vmin=vmin, vmax=vmax))
    sm.set_array([])
    cb = fig.colorbar(sm, ax=ax, fraction=0.03, pad=0.02)
    cb.set_label("cm above (+) / below (−) LWD", fontsize=8)
    fig.tight_layout()
    fig.savefig(MAP_LEVELS, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def render_map_temps(snap: dict) -> None:
    print("Rendering temperature map…")
    fig, ax = plt.subplots(figsize=(11.2, 7.2), dpi=120)
    _draw_lakes(ax, facecolor="#d9e6ec")
    _style_map(ax, "Surface water temperature (GLSEA + buoys)")
    temps = [snap["lakes"][k].get("sst_c") for k in LAKE_POLYS if snap["lakes"].get(k, {}).get("sst_c") is not None]
    vmin, vmax = (0, 25) if not temps else (min(temps) - 1, max(temps) + 1)
    for key, poly in LAKE_POLYS.items():
        sst = (snap["lakes"].get(key) or {}).get("sst_c")
        if sst is None:
            continue
        color = _cmap_color(sst, vmin, vmax, "YlOrRd")
        ax.add_patch(Polygon([(x, y) for x, y in poly], closed=True, facecolor=color, edgecolor="#1a3a4a", linewidth=0.9, zorder=2, alpha=0.9))
        lat, lon = LAKE_POINTS[key]
        lake = snap["lakes"][key]
        a, _ = _arrow(lake.get("sst_d7_c"), 0.15)
        ly = lake.get("sst_vs_ly_c")
        ly_s = "" if ly is None else f"\nvs LY {ly:+.1f}°"
        ax.text(lon, lat, f"{lake['label']}\n{sst:.1f}°C {a}{ly_s}", ha="center", va="center", fontsize=7.5, color="#1a3a4a", zorder=5, fontweight="bold")

    for b in snap["ndbc"]:
        wt = b.get("wtemp_c")
        if wt is None or not (0.0 <= wt <= 32.0):
            continue
        ax.plot(b["lon"], b["lat"], "o", color="#1a3a4a", markersize=4, zorder=4)
        ax.text(b["lon"] + 0.12, b["lat"] + 0.08, f"{b['wtemp_c']:.1f}°", fontsize=6, color="#243036", zorder=5)
    sm = plt.cm.ScalarMappable(cmap=plt.get_cmap("YlOrRd"), norm=plt.Normalize(vmin=vmin, vmax=vmax))
    sm.set_array([])
    cb = fig.colorbar(sm, ax=ax, fraction=0.03, pad=0.02)
    cb.set_label("Lake-average SST (°C)", fontsize=8)
    fig.tight_layout()
    fig.savefig(MAP_TEMPS, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _wind_uv(speed: float | None, direction_from: float | None) -> tuple[float, float] | None:
    if speed is None or direction_from is None:
        return None
    # Meteorological direction is where wind comes FROM; plot toward.
    rad = math.radians(direction_from)
    u = -speed * math.sin(rad)
    v = -speed * math.cos(rad)
    return u, v


def render_map_winds(snap: dict) -> None:
    print("Rendering wind map…")
    fig, ax = plt.subplots(figsize=(11.2, 7.2), dpi=120)
    _draw_lakes(ax, facecolor="#cfe0e8")
    _style_map(ax, "Winds over the lakes (Open-Meteo + NDBC)")
    speeds = []
    for key, pt in LAKE_POINTS.items():
        w = snap["lakes"][key]
        uv = _wind_uv(w.get("wind_ms"), w.get("wind_dir"))
        if not uv:
            continue
        speeds.append(w["wind_ms"])
        lat, lon = pt
        ax.annotate(
            "",
            xy=(lon + uv[0] * 0.12, lat + uv[1] * 0.12),
            xytext=(lon, lat),
            arrowprops=dict(arrowstyle="-|>", color="#1a3a4a", lw=1.8),
            zorder=5,
        )
        a, _ = _arrow(None, 1)  # unused
        ax.text(lon, lat - 0.35, f"{w['label']}\n{w['wind_ms']:.1f} m/s", ha="center", fontsize=7, color="#1a3a4a", zorder=5)

    for b in snap["ndbc"]:
        uv = _wind_uv(b.get("wind_ms"), b.get("wind_dir"))
        if not uv:
            continue
        ax.annotate(
            "",
            xy=(b["lon"] + uv[0] * 0.10, b["lat"] + uv[1] * 0.10),
            xytext=(b["lon"], b["lat"]),
            arrowprops=dict(arrowstyle="-|>", color="#c45c26", lw=1.1),
            zorder=4,
        )
        ax.plot(b["lon"], b["lat"], "o", color="#c45c26", markersize=3, zorder=4)
    ax.text(-92.3, 41.45, "Black arrows: Open-Meteo 10 m wind at lake centres. Orange: NDBC buoys. Arrow points downwind.", fontsize=7.5, color="#5a7a86")
    fig.tight_layout()
    fig.savefig(MAP_WINDS, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def render_map_conditions(snap: dict) -> None:
    print("Rendering conditions / water-quality map…")
    fig, ax = plt.subplots(figsize=(11.2, 7.2), dpi=120)
    _draw_lakes(ax, facecolor="#d9e6ec")
    _style_map(ax, "Surface conditions: SST vs last year, waves")
    anoms = [snap["lakes"][k].get("sst_vs_ly_c") for k in LAKE_POLYS if snap["lakes"].get(k, {}).get("sst_vs_ly_c") is not None]
    vmin, vmax = (-4, 4) if not anoms else (min(-3, min(anoms)), max(3, max(anoms)))
    for key, poly in LAKE_POLYS.items():
        lake = snap["lakes"].get(key) or {}
        anom = lake.get("sst_vs_ly_c")
        color = "#c5dce4" if anom is None else _cmap_color(anom, vmin, vmax, "RdBu_r")
        ax.add_patch(Polygon([(x, y) for x, y in poly], closed=True, facecolor=color, edgecolor="#1a3a4a", linewidth=0.9, zorder=2, alpha=0.9))
        lat, lon = LAKE_POINTS[key]
        a, _ = _arrow(lake.get("sst_d7_c"), 0.15)
        wave = lake.get("wvht_m")
        wave_s = "waves n/a" if wave is None else f"waves {wave:.1f} m"
        sst = lake.get("sst_c")
        sst_s = "SST n/a" if sst is None else f"{sst:.1f}°C"
        ly = "n/a" if anom is None else f"{anom:+.1f}° vs LY"
        ax.text(lon, lat, f"{lake.get('label', key)}\n{sst_s} {a}\n{ly}\n{wave_s}", ha="center", va="center", fontsize=7, color="#1a3a4a", zorder=5, fontweight="bold")

    for b in snap["ndbc"]:
        if b.get("wvht_m") is None:
            continue
        if not str(b.get("id", "")).startswith(("4500", "4501", "451")):
            continue
        ax.plot(b["lon"], b["lat"], "o", color="#1a3a4a", markersize=4, zorder=4)
        ax.text(b["lon"] + 0.1, b["lat"] + 0.08, f"{b['wvht_m']:.1f} m", fontsize=6, color="#243036")
    sm = plt.cm.ScalarMappable(cmap=plt.get_cmap("RdBu_r"), norm=plt.Normalize(vmin=vmin, vmax=vmax))
    sm.set_array([])
    cb = fig.colorbar(sm, ax=ax, fraction=0.03, pad=0.02)
    cb.set_label("SST anomaly vs same day last year (°C)", fontsize=8)
    fig.tight_layout()
    fig.savefig(MAP_COND, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def render_level_chart(snap: dict) -> None:
    print("Rendering 30-day level chart…")
    fig, ax = plt.subplots(figsize=(11.2, 5.4), dpi=120)
    ax.set_facecolor("#ffffff")
    for key, spec in LAKES.items():
        if key == "michigan":
            continue
        daily = (snap["lakes"].get(key) or {}).get("daily") or []
        if len(daily) < 2:
            continue
        xs = [datetime.fromisoformat(r["date"]).date() for r in daily]
        ys = [(r["igld85"] - spec["lwd_m"]) * 100 for r in daily]
        label = "Michigan–Huron" if key == "huron" else spec["label"]
        ax.plot(xs, ys, color=spec["color"], lw=2.0, label=label)
        a, _ = _arrow(snap["lakes"][key].get("d7_cm"), 1.0)
        ax.scatter(xs[-1], ys[-1], color=spec["color"], zorder=3)
        ax.annotate(a, (xs[-1], ys[-1]), textcoords="offset points", xytext=(6, 4), fontsize=9, color=spec["color"])
    ax.axhline(0, color="#8aa0aa", lw=1, ls="--")
    ax.set_title("Daily mean water level vs Low Water Datum (last ~30 days)", fontsize=12, color="#1a3a4a")
    ax.set_ylabel("cm above (+) / below (−) LWD")
    ax.grid(True, color="#e4ebef", lw=0.8)
    ax.legend(frameon=False, ncol=3, fontsize=8, loc="upper left")
    ax.tick_params(colors="#5a7a86")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(CHART_PNG, bbox_inches="tight", facecolor="white")
    plt.close(fig)


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------


def _esc(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _freshness_class(dt: datetime | None, now: datetime) -> str:
    if isinstance(dt, str):
        dt = _parse_iso(dt)
    if dt is None:
        return "fresh-unknown"
    age_h = (now - dt).total_seconds() / 3600
    if age_h <= 3:
        return "fresh-ok"
    if age_h <= 12:
        return "fresh-warn"
    return "fresh-stale"


def _age_label(dt: datetime | None, now: datetime) -> str:
    if isinstance(dt, str):
        dt = _parse_iso(dt)
    if dt is None:
        return "unknown"
    secs = max(0, int((now - dt).total_seconds()))
    if secs < 90:
        return "just now"
    if secs < 3600:
        return f"{secs // 60} min ago"
    if secs < 86400:
        h = secs // 3600
        return f"{h} h ago"
    return f"{secs // 86400} d ago"


def _station_row(s: dict, now: datetime) -> str:
    igld = s.get("igld85")
    cd = s.get("cd")
    vs = None
    lwd = LAKES.get(s.get("lake") or "", {}).get("lwd_m")
    if igld is not None and lwd is not None and s.get("kind") == "lake":
        vs = (igld - lwd) * 100
    return (
        "<tr>"
        f"<td>{_esc(s.get('name',''))}</td>"
        f"<td>{_esc(s.get('code', s.get('id','')))}</td>"
        f"<td>{_esc(s.get('lake','').replace('_',' '))}</td>"
        f"<td class='num'>{'' if cd is None else f'{cd:.3f}'}</td>"
        f"<td class='num'>{'' if igld is None else f'{igld:.3f}'}</td>"
        f"<td class='num'>{'' if vs is None else f'{vs:+.1f}'}</td>"
        f"<td>{ticker_markup(s.get('d24_cm'), flat=0.4, digits=1, unit=' cm')}</td>"
        f"<td>{ticker_markup(s.get('d7_cm'), flat=1.0, digits=1, unit=' cm')}</td>"
        f"<td class='tag'>{_age_label(s.get('when'), now)}</td>"
        "</tr>"
    )


def _find(stations: list[dict], code: str) -> dict | None:
    return next((s for s in stations if s.get("code") == code), None)


def render_html(snap: dict) -> None:
    now = snap["generated"]
    if isinstance(now, str):
        now = _parse_iso(now) or datetime.now(timezone.utc)
        snap["generated"] = now
    lakes = snap["lakes"]
    profile_src = f"profile_overlay.png?v={_asset_v(PROFILE_PNG)}"
    history = {}
    if HISTORY_JSON.exists():
        try:
            history = json.loads(HISTORY_JSON.read_text())
        except Exception:
            history = {}
    history_json = json.dumps(history, separators=(",", ":"))
    hist_v = _asset_v(HISTORY_JS) if HISTORY_JS.exists() else "0"
    profile_raw = "assets/great-lakes-profile.jpg"
    map_l = f"map_levels.png?v={_asset_v(MAP_LEVELS)}"
    map_t = f"map_temps.png?v={_asset_v(MAP_TEMPS)}"
    map_w = f"map_winds.png?v={_asset_v(MAP_WINDS)}"
    map_c = f"map_conditions.png?v={_asset_v(MAP_COND)}"
    chart_src = f"chart_levels.png?v={_asset_v(CHART_PNG)}"

    kpis = []
    for key in ("superior", "michigan", "huron", "st_clair", "erie", "ontario"):
        lake = lakes.get(key) or {}
        igld = lake.get("igld85")
        val = "—" if igld is None else f"{igld:.2f}"
        vs = lake.get("vs_lwd_cm")
        vs_s = "" if vs is None else f"{vs:+.0f} cm vs LWD"
        note = lake.get("note") or f"chart datum {lake.get('lwd_m')} m"
        kpis.append(
            f"""
                <div class="kpi">
                  <p class="kpi-label">{_esc(lake.get('full') or key)}</p>
                  <p class="kpi-value">{val}<span style="font-size:13px;font-weight:600;color:#5a7a86;"> m</span></p>
                  <div>{ticker_markup(lake.get('d24_cm'), flat=0.4, digits=1, unit=' cm')}</div>
                  <p class="kpi-sub">{vs_s}<br>7d {trend_word(lake.get('d7_cm'), flat=1.0, unit=' cm')}<br>{_esc(note)}</p>
                </div>"""
        )

    sst_kpis = []
    for key in ("superior", "michigan", "huron", "st_clair", "erie", "ontario"):
        lake = lakes.get(key) or {}
        sst = lake.get("sst_c")
        val = "—" if sst is None else f"{sst:.1f}"
        sst_kpis.append(
            f"""
                <div class="kpi">
                  <p class="kpi-label">{_esc(lake.get('label') or key)} SST</p>
                  <p class="kpi-value">{val}<span style="font-size:13px;font-weight:600;color:#5a7a86;"> °C</span></p>
                  <div>{ticker_markup(lake.get('sst_d7_c'), flat=0.15, digits=1, unit='°')}</div>
                  <p class="kpi-sub">7-day change<br>vs last year {'' if lake.get('sst_vs_ly_c') is None else f"{lake['sst_vs_ly_c']:+.1f}°"}</p>
                </div>"""
        )

    wind_kpis = []
    for key in ("superior", "michigan", "huron", "st_clair", "erie", "ontario"):
        lake = lakes.get(key) or {}
        ws = lake.get("wind_ms")
        val = "—" if ws is None else f"{ws:.1f}"
        wind_kpis.append(
            f"""
                <div class="kpi">
                  <p class="kpi-label">{_esc(lake.get('label') or key)} wind</p>
                  <p class="kpi-value">{val}<span style="font-size:13px;font-weight:600;color:#5a7a86;"> m/s</span></p>
                  <div>{ticker_markup(None, flat=9, digits=1)}</div>
                  <p class="kpi-sub">from {'' if lake.get('wind_dir') is None else f"{lake['wind_dir']:.0f}°"} · 10 m<br>waves {'' if lake.get('wvht_m') is None else f"{lake['wvht_m']:.1f} m"}</p>
                </div>"""
        )

    # Status: how many lakes above LWD
    above = [k for k, v in lakes.items() if v.get("vs_lwd_cm") is not None and v["vs_lwd_cm"] >= 0]
    below = [k for k, v in lakes.items() if v.get("vs_lwd_cm") is not None and v["vs_lwd_cm"] < 0]
    if below:
        status = f"{len(above)} lakes above LWD · {', '.join(LAKES[k]['label'] for k in below)} below chart datum"
    else:
        status = "All mapped lakes are above Low Water Datum (chart datum)"

    primaries = [s for s in snap["stations"] if s.get("primary") and s.get("kind") == "lake"]
    fresh_html = []
    for s in primaries[:5]:
        when = s.get("when") or now
        if isinstance(when, datetime):
            when_iso = when.isoformat()
        else:
            when_iso = str(when)
        cls = _freshness_class(when, now)
        fresh_html.append(
            f"""
                      <div class="gauge-fresh" data-as-of="{when_iso}">
                        <p class="gauge-fresh-name">{_esc(s['name'])}</p>
                        <p class="gauge-fresh-age {cls}">Checking…</p>
                        <p class="gauge-fresh-when">{_esc(fmt_edt(when))} EDT</p>
                      </div>"""
        )

    soo_a = _find(snap["stations"], "10980")
    soo_b = _find(snap["stations"], "11010")
    iro_a = _find(snap["stations"], "14600")
    iro_b = _find(snap["stations"], "14602")
    erie = lakes.get("erie") or {}
    ont = lakes.get("ontario") or {}
    niagara = None
    if erie.get("igld85") is not None and ont.get("igld85") is not None:
        niagara = erie["igld85"] - ont["igld85"]

    def drop_row(label: str, a: dict | None, b: dict | None) -> str:
        if not a or not b or a.get("igld85") is None or b.get("igld85") is None:
            return f"<tr><td>{_esc(label)}</td><td class='num'>—</td><td class='num'>—</td><td class='num'>—</td><td>—</td></tr>"
        drop = a["igld85"] - b["igld85"]
        dlt = None
        if a.get("d24_cm") is not None and b.get("d24_cm") is not None:
            dlt = a["d24_cm"] - b["d24_cm"]
        return (
            "<tr>"
            f"<td>{_esc(label)}</td>"
            f"<td class='num'>{a['igld85']:.3f}</td>"
            f"<td class='num'>{b['igld85']:.3f}</td>"
            f"<td class='num'>{drop:.3f}</td>"
            f"<td>{ticker_markup(dlt, flat=0.5, digits=1, unit=' cm')}</td>"
            "</tr>"
        )

    chs_rows = "\n                ".join(_station_row(s, now) for s in snap["stations"])
    noaa_rows = "\n                ".join(
        _station_row(
            {
                **s,
                "code": s.get("id"),
                "kind": "lake",
                "d7_cm": None,
                "cd": None,
            },
            now,
        )
        for s in snap["noaa"]
        if s.get("igld85") is not None
    )

    ndbc_rows = []
    for b in snap["ndbc"]:
        ndbc_rows.append(
            "<tr>"
            f"<td>{_esc(b['id'])}</td>"
            f"<td>{_esc(b.get('lake',''))}</td>"
            f"<td class='num'>{'' if b.get('wtemp_c') is None else f'{b['wtemp_c']:.1f}'}</td>"
            f"<td class='num'>{'' if b.get('wind_ms') is None else f'{b['wind_ms']:.1f}'}</td>"
            f"<td class='num'>{'' if b.get('wvht_m') is None else f'{b['wvht_m']:.1f}'}</td>"
            f"<td class='tag'>{_age_label(b.get('when'), now)}</td>"
            "</tr>"
        )

    glance_day = now.astimezone(TZ).strftime("%B %-d") if hasattr(now.astimezone(TZ), "strftime") else now.astimezone(TZ).strftime("%B %d")
    # %-d is POSIX; on Windows it would fail — GH Actions is Ubuntu.
    try:
        glance_day = now.astimezone(TZ).strftime("%B %-d")
    except ValueError:
        glance_day = now.astimezone(TZ).strftime("%B %d")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="1800">
  <title>Great Lakes Water Levels</title>
  <meta name="description" content="Live Great Lakes water levels from the Canadian Hydrographic Service, with temperature, wind, and surface-condition maps. Updated hourly.">
  <link rel="icon" href="favicon.svg" type="image/svg+xml">
  <style>
    .kpi-grid {{ display:grid; grid-template-columns:1fr 1fr 1fr; gap:12px; }}
    .kpi {{ background:#f4f8f9; border:1px solid #d7e4e8; border-radius:10px; padding:14px 12px; text-align:center; }}
    .kpi-label {{ margin:0 0 8px 0; font-family:Arial,Helvetica,sans-serif; font-size:10px; letter-spacing:0.08em; text-transform:uppercase; color:#5a7a86; }}
    .kpi-value {{ margin:0; font-family:Arial,Helvetica,sans-serif; font-size:24px; font-weight:700; color:#1a3a4a; line-height:1.1; }}
    .kpi-sub {{ margin:8px 0 0 0; font-family:Arial,Helvetica,sans-serif; font-size:11px; color:#6a7c84; line-height:1.35; }}
    .ticker {{ display:inline-block; margin-top:6px; font-family:Arial,Helvetica,sans-serif; font-size:12px; font-weight:700; letter-spacing:0.02em; }}
    .ticker.up {{ color:#0b6e4f; }}
    .ticker.down {{ color:#c45c26; }}
    .ticker.flat {{ color:#5a7a86; font-size:14px; }}
    .data-fresh {{ font-family:Arial,Helvetica,sans-serif; text-align:left; width:100%; margin:16px 0 0 0; }}
    .data-fresh-heading {{ margin:0 0 8px 0; font-size:10px; letter-spacing:0.1em; text-transform:uppercase; color:#8eb8c8; }}
    .data-fresh-row {{ display:flex; flex-wrap:wrap; gap:10px 8px; }}
    .gauge-fresh {{ flex:1 1 0; min-width:110px; margin:0; padding:0 10px 0 0; box-sizing:border-box; }}
    .gauge-fresh-name {{ margin:0; font-size:10px; letter-spacing:0.04em; text-transform:uppercase; color:#8eb8c8; }}
    .gauge-fresh-age {{ margin:3px 0 0 0; font-size:13px; font-weight:700; line-height:1.2; }}
    .gauge-fresh-age.fresh-ok {{ color:#7dcea0; }}
    .gauge-fresh-age.fresh-warn {{ color:#f4d35e; }}
    .gauge-fresh-age.fresh-stale {{ color:#e07a5f; }}
    .gauge-fresh-age.fresh-unknown {{ color:#b7d0da; }}
    .gauge-fresh-when {{ margin:2px 0 0 0; font-size:10px; color:#7a96a3; line-height:1.25; }}
    .data-table {{ width:100%; border-collapse:collapse; font-family:Arial,Helvetica,sans-serif; font-size:13px; color:#243036; }}
    .data-table th {{ text-align:left; padding:8px 6px; border-bottom:2px solid #d5dde3; color:#5a7a86; font-size:11px; letter-spacing:0.06em; text-transform:uppercase; font-weight:700; }}
    .data-table td {{ padding:7px 6px; border-bottom:1px solid #e4ebef; }}
    .data-table td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
    .data-table th.num {{ text-align:right; }}
    .data-table td.tag {{ color:#6a7c84; font-size:12px; }}
    .chart-thumb {{ cursor:zoom-in; max-width:100%; height:auto; border:1px solid #d5dde3; border-radius:6px; display:block; transition:opacity .15s ease; }}
    .chart-thumb:hover {{ opacity:0.92; }}
    .lightbox {{ display:none; position:fixed; inset:0; z-index:1000; background:rgba(10,20,28,0.92); align-items:center; justify-content:center; padding:24px; box-sizing:border-box; }}
    .lightbox.open {{ display:flex; }}
    .lightbox img {{ max-width:min(1400px,96vw); max-height:92vh; width:auto; height:auto; border-radius:6px; box-shadow:0 12px 40px rgba(0,0,0,0.45); background:#fff; }}
    .lightbox-close {{ position:fixed; top:16px; right:20px; border:0; background:rgba(255,255,255,0.12); color:#fff; font:600 14px/1 Arial,Helvetica,sans-serif; padding:10px 14px; border-radius:8px; cursor:pointer; }}
    .lightbox-hint {{ position:fixed; bottom:16px; left:50%; transform:translateX(-50%); color:rgba(255,255,255,0.7); font:12px/1.4 Arial,Helvetica,sans-serif; }}
    .toc a {{ color:#2f6f7e; margin-right:14px; font-family:Arial,Helvetica,sans-serif; font-size:13px; }}
    .history-stage {{ position:relative; background:#0b2230; border-radius:8px; overflow:hidden; border:1px solid #1a3a4a; max-width:100%; }}
    .history-stage img {{ width:100%; max-width:100%; height:auto; display:block; }}
    .history-stage canvas {{ position:absolute; left:0; top:0; width:100%; height:100%; pointer-events:none; display:block; }}
    .history-wrap {{ max-width:100%; overflow:hidden; }}
    .history-hud {{ position:absolute; left:16px; top:10px; color:#fff; text-shadow:0 2px 10px rgba(0,0,0,0.55); z-index:2; pointer-events:none; }}
    .history-year {{ margin:0; font-family:Georgia,serif; font-size:44px; line-height:1; font-weight:normal; }}
    .history-month {{ margin:4px 0 0 0; font-family:Arial,Helvetica,sans-serif; font-size:12px; letter-spacing:0.12em; text-transform:uppercase; color:#b7d0da; }}
    .history-event {{ margin:10px 0 0 0; font-family:Arial,Helvetica,sans-serif; font-size:13px; max-width:62%; line-height:1.35; color:#e8f2f6; }}
    .history-play-fab {{ position:absolute; right:16px; bottom:16px; z-index:3; border:0; background:#2f6f7e; color:#fff; font:700 14px/1 Arial,Helvetica,sans-serif; padding:12px 16px; border-radius:8px; cursor:pointer; box-shadow:0 6px 20px rgba(0,0,0,0.25); }}
    .history-play-fab:hover {{ background:#1a3a4a; }}
    .history-controls {{ display:flex; align-items:center; gap:12px; padding:10px 0 6px 0; font-family:Arial,Helvetica,sans-serif; }}
    .history-controls button {{ border:1px solid #2f6f7e; background:#2f6f7e; color:#fff; font:700 13px/1 Arial,Helvetica,sans-serif; padding:10px 14px; border-radius:8px; cursor:pointer; min-width:108px; }}
    .history-controls input[type=range] {{ flex:1; accent-color:#2f6f7e; }}
    .history-readout {{ font-size:13px; color:#1a3a4a; font-variant-numeric:tabular-nums; min-width:88px; text-align:right; }}
    #history-chart {{ display:block; width:100% !important; max-width:100%; height:168px !important; border:1px solid #d5dde3; border-radius:6px; background:#f4f8f9; }}
    @media (max-width:720px) {{
      .kpi-grid {{ grid-template-columns:1fr 1fr; }}
      .history-year {{ font-size:32px; }}
      .history-event {{ max-width:90%; font-size:12px; }}
      .gauge-fresh {{ flex:1 1 calc(50% - 8px); }}
    }}
    @media (max-width:420px) {{
      .kpi-grid {{ grid-template-columns:1fr; }}
      .kpi-value {{ font-size:22px; }}
    }}
  </style>
</head>
<body style="margin:0;padding:0;background:#eef2f4;font-family:Georgia,'Times New Roman',serif;">
  <div id="chart-lightbox" class="lightbox" role="dialog" aria-modal="true" aria-label="Full screen figure" onclick="if(event.target===this)closeChart()">
    <button type="button" class="lightbox-close" onclick="closeChart()" aria-label="Close">Close ✕</button>
    <img id="lightbox-img" src="{profile_src}" alt="Full screen figure">
    <div class="lightbox-hint">Click outside or press Esc to close</div>
  </div>
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#eef2f4;padding:24px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="980" cellspacing="0" cellpadding="0" style="max-width:980px;width:100%;table-layout:fixed;background:#ffffff;border-radius:8px;overflow:hidden;border:1px solid #d5dde3;">
          <tr>
            <td style="background:#1a3a4a;padding:22px 32px 18px 32px;">
                    <p style="margin:0 0 6px 0;font-family:Arial,Helvetica,sans-serif;font-size:12px;letter-spacing:0.12em;text-transform:uppercase;color:#8eb8c8;">Great Lakes · St. Lawrence system</p>
                    <h1 style="margin:0;font-family:Georgia,serif;font-size:28px;line-height:1.25;font-weight:normal;color:#ffffff;">Great Lakes Water Levels</h1>
                    <p style="margin:10px 0 0 0;font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#b7d0da;">Updated { _esc(snap['generated_edt']) } EDT · CHS IWLS gauges, refreshed about hourly</p>
                    <div class="data-fresh" title="How fresh each primary gauge reading is">
                      <p class="data-fresh-heading">Gauge freshness</p>
                      <div class="data-fresh-row">
                      {''.join(fresh_html)}
                      </div>
                    </div>
            </td>
          </tr>
          <tr>
            <td style="background:#2f6f7e;padding:14px 32px;font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#ffffff;">
              <strong>Status:</strong> {_esc(status)} · Not for navigation · Official: <a href="https://tides.gc.ca/en" style="color:#ffffff;">tides.gc.ca</a>
            </td>
          </tr>
          <tr>
            <td style="padding:22px 32px 8px 32px;font-family:Georgia,serif;font-size:16px;line-height:1.55;color:#243036;">
              <p style="margin:0 0 12px 0;">Live briefing for the Great Lakes. Water levels are Canadian Hydrographic Service observations (IWLS), shown on IGLD 1985 and compared with Low Water Datum. Michigan and Huron share a surface. Tickers are 24-hour change unless noted. <span style="color:#5a7a86;">Click figures for full screen.</span></p>
              <p class="toc" style="margin:0;">
                <a href="#profile">Profile</a>
                <a href="#history">1918–now</a>
                <a href="#levels">Levels map</a>
                <a href="#temps">Temperature</a>
                <a href="#winds">Winds</a>
                <a href="#conditions">Conditions</a>
                <a href="#channels">Channels</a>
                <a href="#stations">Stations</a>
              </p>
            </td>
          </tr>
          <tr>
            <td style="padding:12px 32px 8px 32px;">
              <p style="margin:0 0 12px 0;font-family:Arial,Helvetica,sans-serif;font-size:12px;letter-spacing:0.1em;text-transform:uppercase;color:#5a7a86;">{glance_day} — lake levels <span style="letter-spacing:0;text-transform:none;color:#8a9aa2;">· vs last 24 hours</span></p>
              <div class="kpi-grid">
                {''.join(kpis)}
              </div>
            </td>
          </tr>
          <tr>
            <td id="profile" style="padding:20px 32px 8px 32px;font-family:Georgia,serif;font-size:16px;color:#243036;">
              <h2 style="margin:0 0 8px 0;font-size:20px;color:#1a3a4a;">System profile with live levels</h2>
              <p style="margin:0 0 12px 0;font-size:15px;">Current IGLD 1985 elevation on each lake, centimetres above or below Low Water Datum, and 24-hour / 7-day trend. Schematic is not to scale. Surface elevations on the original drawing are chart-datum values.</p>
            </td>
          </tr>
          <tr>
            <td style="padding:0 24px 16px 24px;" align="center">
              <img src="{profile_src}" width="932" class="chart-thumb" alt="Great Lakes system profile with live water levels overlaid — click to enlarge" onclick="openChart('{profile_src}')" title="Click to view full screen">
            </td>
          </tr>
          <tr>
            <td id="history" style="padding:16px 32px 8px 32px;font-family:Georgia,serif;font-size:16px;color:#243036;">
              <h2 style="margin:0 0 8px 0;font-size:20px;color:#1a3a4a;">A century of lake levels</h2>
              <p style="margin:0 0 12px 0;font-size:15px;">Play 1918 through last month in 30 seconds. Water surfaces on the schematic move with NOAA monthly means (12-month average so the seasonal cycle does not flicker). Vertical motion is exaggerated — real changes are about a metre. Tickers are year-over-year. Michigan and Huron share one surface.</p>
            </td>
          </tr>
          <tr>
            <td style="padding:0 24px 8px 24px;">
              <div class="history-wrap">
              <div class="history-stage" id="history-stage">
                <img id="history-bg" src="{profile_raw}" width="932" alt="Great Lakes system profile used as the animation backdrop">
                <canvas id="history-overlay" aria-hidden="true"></canvas>
                <div class="history-hud">
                  <p class="history-year" id="history-year">1918</p>
                  <p class="history-month" id="history-month">Jan</p>
                  <p class="history-event" id="history-event">Annual cycle removed — 12-month mean, IGLD 1985</p>
                </div>
                <button type="button" class="history-play-fab" id="history-play-fab">Play 30 seconds</button>
              </div>
              <div class="history-controls">
                <button type="button" id="history-toggle">Play 30s</button>
                <input type="range" id="history-scrub" min="0" max="1000" value="0" aria-label="Scrub lake-level history">
                <span class="history-readout" id="history-readout">1918 Jan</span>
              </div>
              <canvas id="history-chart" width="932" height="168" aria-label="Hydrograph of centimetres versus each lake’s long-term mean"></canvas>
              <div class="kpi-grid history-kpis" id="history-kpis"></div>
              </div>
            </td>
          </tr>
          <tr>
            <td id="levels" style="padding:8px 32px 8px 32px;font-family:Georgia,serif;font-size:16px;color:#243036;">
              <h2 style="margin:0 0 8px 0;font-size:20px;color:#1a3a4a;">Levels map</h2>
              <p style="margin:0 0 12px 0;font-size:15px;">Lake fill is centimetres versus Low Water Datum. Dots are CHS gauges. Trend mark is 24-hour change.</p>
            </td>
          </tr>
          <tr>
            <td style="padding:0 24px 16px 24px;" align="center">
              <img src="{map_l}" width="932" class="chart-thumb" alt="Great Lakes map coloured by water level versus Low Water Datum" onclick="openChart('{map_l}')">
            </td>
          </tr>
          <tr>
            <td style="padding:8px 32px 8px 32px;">
              <h2 style="margin:0 0 8px 0;font-size:20px;color:#1a3a4a;font-family:Georgia,serif;">30-day lake levels</h2>
              <p style="margin:0 0 12px 0;font-size:15px;color:#243036;">CHS IGLD 1985 daily means, plotted as centimetres above or below Low Water Datum. End mark is the 7-day trend.</p>
            </td>
          </tr>
          <tr>
            <td style="padding:0 24px 16px 24px;" align="center">
              <img src="{chart_src}" width="932" class="chart-thumb" alt="Thirty-day Great Lakes water levels versus Low Water Datum" onclick="openChart('{chart_src}')">
            </td>
          </tr>
          <tr>
            <td id="temps" style="padding:12px 32px 8px 32px;">
              <p style="margin:0 0 12px 0;font-family:Arial,Helvetica,sans-serif;font-size:12px;letter-spacing:0.1em;text-transform:uppercase;color:#5a7a86;">Lake-average surface temperature <span style="letter-spacing:0;text-transform:none;color:#8a9aa2;">· vs 7 days ago</span></p>
              <div class="kpi-grid">
                {''.join(sst_kpis)}
              </div>
            </td>
          </tr>
          <tr>
            <td style="padding:16px 32px 8px 32px;font-family:Georgia,serif;font-size:16px;color:#243036;">
              <h2 style="margin:0 0 8px 0;font-size:20px;color:#1a3a4a;">Temperature map</h2>
              <p style="margin:0 0 12px 0;font-size:15px;">Fill is NOAA CoastWatch GLSEA lake-average SST. Dots are NDBC buoy water temperatures. Trend is the 7-day GLSEA change; vs LY is the same day last year.</p>
            </td>
          </tr>
          <tr>
            <td style="padding:0 24px 16px 24px;" align="center">
              <img src="{map_t}" width="932" class="chart-thumb" alt="Great Lakes surface temperature map" onclick="openChart('{map_t}')">
            </td>
          </tr>
          <tr>
            <td id="winds" style="padding:12px 32px 8px 32px;">
              <p style="margin:0 0 12px 0;font-family:Arial,Helvetica,sans-serif;font-size:12px;letter-spacing:0.1em;text-transform:uppercase;color:#5a7a86;">Winds and waves</p>
              <div class="kpi-grid">
                {''.join(wind_kpis)}
              </div>
            </td>
          </tr>
          <tr>
            <td style="padding:16px 32px 8px 32px;font-family:Georgia,serif;font-size:16px;color:#243036;">
              <h2 style="margin:0 0 8px 0;font-size:20px;color:#1a3a4a;">Wind map</h2>
              <p style="margin:0 0 12px 0;font-size:15px;">Arrows point downwind. Lake-centre winds are Open-Meteo 10 m; orange arrows are NDBC buoys.</p>
            </td>
          </tr>
          <tr>
            <td style="padding:0 24px 16px 24px;" align="center">
              <img src="{map_w}" width="932" class="chart-thumb" alt="Great Lakes wind map" onclick="openChart('{map_w}')">
            </td>
          </tr>
          <tr>
            <td id="conditions" style="padding:16px 32px 8px 32px;font-family:Georgia,serif;font-size:16px;color:#243036;">
              <h2 style="margin:0 0 8px 0;font-size:20px;color:#1a3a4a;">Surface conditions</h2>
              <p style="margin:0 0 12px 0;font-size:15px;">Lake fill is SST versus the same day last year (a simple thermal anomaly). Wave height is Open-Meteo marine / NDBC. For western Lake Erie cyanobacteria, see the <a href="https://coastalscience.noaa.gov/science-areas/habs/hab-forecasts/lake-erie/" style="color:#2f6f7e;">NOAA NCCOS HAB forecast</a>.</p>
            </td>
          </tr>
          <tr>
            <td style="padding:0 24px 16px 24px;" align="center">
              <img src="{map_c}" width="932" class="chart-thumb" alt="Great Lakes surface condition map" onclick="openChart('{map_c}')">
            </td>
          </tr>
          <tr>
            <td id="channels" style="padding:16px 32px 8px 32px;font-family:Georgia,serif;font-size:16px;color:#243036;">
              <h2 style="margin:0 0 8px 0;font-size:20px;color:#1a3a4a;">Connecting channels</h2>
              <p style="margin:0 0 12px 0;font-size:14px;color:#5a7078;font-family:Arial,Helvetica,sans-serif;">Drop is upstream minus downstream IGLD 1985 elevation. Tickers are the 24-hour change in that drop.</p>
              <table class="data-table" role="table">
                <thead>
                  <tr>
                    <th scope="col">Reach</th>
                    <th class="num" scope="col">Upstream (m)</th>
                    <th class="num" scope="col">Downstream (m)</th>
                    <th class="num" scope="col">Drop (m)</th>
                    <th scope="col">24h</th>
                  </tr>
                </thead>
                <tbody>
                {drop_row("St. Marys (Sault Ste. Marie locks)", soo_a, soo_b)}
                {drop_row("Iroquois Dam (St. Lawrence)", iro_a, iro_b)}
                <tr><td>Niagara (Erie − Ontario lake means)</td>
                    <td class="num">{'' if erie.get('igld85') is None else f"{erie['igld85']:.3f}"}</td>
                    <td class="num">{'' if ont.get('igld85') is None else f"{ont['igld85']:.3f}"}</td>
                    <td class="num">{'' if niagara is None else f"{niagara:.3f}"}</td>
                    <td>{ticker_markup(None if erie.get('d24_cm') is None or ont.get('d24_cm') is None else erie['d24_cm']-ont['d24_cm'], flat=0.5, digits=1, unit=' cm')}</td></tr>
                </tbody>
              </table>
            </td>
          </tr>
          <tr>
            <td id="stations" style="padding:16px 32px 8px 32px;font-family:Georgia,serif;font-size:16px;color:#243036;">
              <h2 style="margin:0 0 8px 0;font-size:20px;color:#1a3a4a;">CHS IWLS stations</h2>
              <p style="margin:0 0 12px 0;font-size:14px;color:#5a7078;font-family:Arial,Helvetica,sans-serif;">Official water level (wlo) relative to chart datum, converted to IGLD 1985 with the station datum offset. Lake rows also show centimetres versus that lake’s Low Water Datum.</p>
              <table class="data-table" role="table">
                <thead>
                  <tr>
                    <th scope="col">Station</th>
                    <th scope="col">Code</th>
                    <th scope="col">Water body</th>
                    <th class="num" scope="col">CD (m)</th>
                    <th class="num" scope="col">IGLD85 (m)</th>
                    <th class="num" scope="col">vs LWD (cm)</th>
                    <th scope="col">24h</th>
                    <th scope="col">7d</th>
                    <th scope="col">Age</th>
                  </tr>
                </thead>
                <tbody>
                {chs_rows}
                </tbody>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:16px 32px 8px 32px;font-family:Georgia,serif;font-size:16px;color:#243036;">
              <h2 style="margin:0 0 8px 0;font-size:20px;color:#1a3a4a;">NOAA CO-OPS stations (U.S. shore)</h2>
              <p style="margin:0 0 12px 0;font-size:14px;color:#5a7078;font-family:Arial,Helvetica,sans-serif;">Complementary IGLD water levels, including Lake Michigan (no CHS open-lake gauge). 24h ticker from recent six-minute series.</p>
              <table class="data-table" role="table">
                <thead>
                  <tr>
                    <th scope="col">Station</th>
                    <th scope="col">ID</th>
                    <th scope="col">Lake</th>
                    <th class="num" scope="col">CD (m)</th>
                    <th class="num" scope="col">IGLD85 (m)</th>
                    <th class="num" scope="col">vs LWD (cm)</th>
                    <th scope="col">24h</th>
                    <th scope="col">7d</th>
                    <th scope="col">Age</th>
                  </tr>
                </thead>
                <tbody>
                {noaa_rows}
                </tbody>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:16px 32px 8px 32px;font-family:Georgia,serif;font-size:16px;color:#243036;">
              <h2 style="margin:0 0 8px 0;font-size:20px;color:#1a3a4a;">NDBC buoys</h2>
              <table class="data-table" role="table">
                <thead>
                  <tr>
                    <th scope="col">ID</th>
                    <th scope="col">Lake</th>
                    <th class="num" scope="col">Water °C</th>
                    <th class="num" scope="col">Wind m/s</th>
                    <th class="num" scope="col">Waves m</th>
                    <th scope="col">Age</th>
                  </tr>
                </thead>
                <tbody>
                {''.join(ndbc_rows) or '<tr><td colspan="6">No Great Lakes buoy reports in the latest NDBC bulletin.</td></tr>'}
                </tbody>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:20px 32px 28px 32px;font-family:Arial,Helvetica,sans-serif;font-size:12px;line-height:1.5;color:#5a7078;">
              <p style="margin:0 0 8px 0;"><strong>Data:</strong> Water levels from the <a href="https://tides.gc.ca/en/web-services-offered-canadian-hydrographic-service" style="color:#2f6f7e;">Canadian Hydrographic Service IWLS API</a> (licence: <a href="https://tides.gc.ca/en/licence-agreement" style="color:#2f6f7e;">tides.gc.ca licence</a>). U.S. gauges and the century animation: NOAA CO-OPS monthly means (IGLD 1985). SST: NOAA GLERL/CoastWatch GLSEA. Winds/waves: Open-Meteo and NDBC. Profile graphic modified from Michigan Sea Grant; not to scale.</p>
              <p style="margin:0;">Provisional public data for awareness only — not for navigation or flood warning. In case of disparity, official CHS publications prevail. Quality flags follow UNESCO IOC standards on IWLS points.</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
  <script>
    function openChart(src) {{
      var box = document.getElementById('chart-lightbox');
      document.getElementById('lightbox-img').src = src;
      box.classList.add('open');
    }}
    function closeChart() {{
      document.getElementById('chart-lightbox').classList.remove('open');
    }}
    document.addEventListener('keydown', function(e) {{ if (e.key === 'Escape') closeChart(); }});
    function refreshFreshness() {{
      var now = Date.now();
      document.querySelectorAll('.gauge-fresh').forEach(function(el) {{
        var iso = el.getAttribute('data-as-of');
        var ageEl = el.querySelector('.gauge-fresh-age');
        if (!iso || !ageEl) return;
        var t = Date.parse(iso);
        if (isNaN(t)) {{ ageEl.textContent = 'unknown'; ageEl.className = 'gauge-fresh-age fresh-unknown'; return; }}
        var h = (now - t) / 3600000;
        var mins = Math.round((now - t) / 60000);
        var label = mins < 2 ? 'just now' : (mins < 60 ? mins + ' min ago' : (mins < 1440 ? Math.round(mins/60) + ' h ago' : Math.round(mins/1440) + ' d ago'));
        ageEl.textContent = label;
        ageEl.className = 'gauge-fresh-age ' + (h <= 3 ? 'fresh-ok' : (h <= 12 ? 'fresh-warn' : 'fresh-stale'));
      }});
    }}
    refreshFreshness();
    setInterval(refreshFreshness, 30000);
  </script>
  <script type="application/json" id="gl-history">{history_json}</script>
  <script src="history.js?v={hist_v}"></script>
</body>
</html>
"""
    INDEX.write_text(html)


def _json_ready(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _json_ready(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_ready(v) for v in obj]
    return obj


def main() -> None:
    print("Building Great Lakes briefing…")
    DATA.mkdir(parents=True, exist_ok=True)
    from_snap = "--from-snapshot" in sys.argv
    skip_history = "--skip-history" in sys.argv
    if from_snap and SNAPSHOT.exists():
        print("Loading existing snapshot (skip live fetches)…")
        snap = json.loads(SNAPSHOT.read_text())
        if isinstance(snap.get("generated"), str):
            snap["generated"] = _parse_iso(snap["generated"]) or datetime.now(timezone.utc)
        render_profile(snap)
    else:
        snap = build_snapshot()
        SNAPSHOT.write_text(json.dumps(_json_ready(snap), indent=2))
        render_profile(snap)
        render_map_levels(snap)
        render_map_temps(snap)
        render_map_winds(snap)
        render_map_conditions(snap)
        render_level_chart(snap)
    if skip_history:
        print("Skipping history fetch")
    else:
        existing_history = None
        if HISTORY_JSON.exists():
            try:
                existing_history = json.loads(HISTORY_JSON.read_text())
            except Exception:
                existing_history = None
        history = fetch_history(existing_history)
        HISTORY_JSON.write_text(json.dumps(history, indent=2))
    render_html(snap)
    print(f"Wrote {INDEX}")
    print("Done.")


if __name__ == "__main__":
    main()
