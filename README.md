# Great Lakes Water Levels

Live briefing for the **Great Lakes–St. Lawrence system**: CHS water levels on the classic system-profile graphic, plus maps for temperature, wind, and surface conditions.

**Site:** https://colinpjack.github.io/great-lakes-levels/  
GitHub Actions refreshes gauges about hourly, commits the generated page, and deploys GitHub Pages.

## What’s included

| File | Purpose |
|------|---------|
| `index.html` | Public briefing page |
| `profile_overlay.png` | Michigan Sea Grant–style profile with live levels |
| `map_levels.png` | Plan-view levels vs Low Water Datum |
| `map_temps.png` | GLSEA SST + buoy temperatures |
| `map_winds.png` | Open-Meteo / NDBC winds |
| `map_conditions.png` | SST vs last year + waves |
| `chart_levels.png` | ~30-day IGLD 1985 vs LWD |
| `update_site.py` | Fetches APIs and rebuilds the page |
| `data/snapshot.json` | Latest assembled numbers |
| `.github/workflows/update.yml` | Refresh, commit, deploy Pages |

## Local refresh

```bash
cd ~/Projects/GreatLakes
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python update_site.py
open index.html
```

## Data

- **Water levels:** [Canadian Hydrographic Service IWLS API](https://tides.gc.ca/en/web-services-offered-canadian-hydrographic-service) (`wlo` observations, IGLD 1985 daily means, `wlf` forecasts). Use is subject to the [CHS licence](https://tides.gc.ca/en/licence-agreement).
- **U.S. gauges / Michigan:** NOAA CO-OPS (IGLD).
- **Lake-average SST:** NOAA GLERL / CoastWatch GLSEA.
- **Winds and waves:** Open-Meteo and NDBC.
- **Profile graphic:** modified from Michigan Sea Grant (not to scale; printed elevations are chart datum).

Lake Michigan has no Canadian open-lake IWLS gauge. Michigan–Huron are hydraulically one lake; the Michigan card uses that shared surface plus NOAA Michigan stations.

Trend arrows are 24-hour (gauges) or 7-day (GLSEA temperature) change.

## Notes

- Schedule cron is **twice an hour** at :23 and :53 UTC (not :00 — GitHub often delays or drops jobs at the start of the hour). Cron is still best-effort.
- IWLS is rate-limited (3 requests/s, 30/min); the updater spaces CHS calls accordingly.
- Provisional public data — **not for navigation**. Official CHS publications prevail if values differ.

## Disclaimer

Awareness only. Follow [tides.gc.ca](https://tides.gc.ca/en) and NOAA / USACE Great Lakes products for official messages.
