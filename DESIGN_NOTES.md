# Fuel Route API — Design Notes

Running record of the approach, decisions, alternatives considered and edge cases.
Updated at every step of the build; the final README is drawn from this file.

_Last updated: 29 Sep 2026 — Step 2 (data model)_

---

## 1. The assignment

Build a Django API that takes a start and a finish location (both in the USA) and returns:

- the route, as a map
- the most cost-effective places to refuel along it — the vehicle's range is 500 miles, so long trips need several stops
- the total money spent on fuel, at 10 miles per gallon

Constraints: latest stable Django (6.1.1), fast responses, and few calls to the external map/routing API (1 is ideal, 2–3 acceptable). Fuel prices come from the provided CSV.

## 2. How the problem is interpreted

Two different problems could be meant:

| | Problem A — fixed route | Problem B — choose the route |
|---|---|---|
| Question | Where should the vehicle refuel along the normal driving route? | Which of all possible routes gives the lowest fuel bill? |
| Fits the brief? | Yes — "optimal location to fuel up **along the route**" | Not asked; needs many routing calls; ignores driving time |

**Decision:** Problem A is the core. A lightweight version of B is an optional bonus (D11) that still uses a single API call.

## 3. Solution overview

**One-time setup** — `python manage.py load_stations`

1. Read the fuel-price CSV and the US Census Gazetteer "Places" file.
2. Clean the CSV (dedupe, trim, drop Canada) and give each station coordinates from its City + State.
3. Insert stations and cities into the database (SQLite).

**Per request**

1. Resolve start and finish (e.g. `"Chicago, IL"`) to coordinates from the local `City` table — **0 API calls**.
2. **One** call to the OSRM routing API → route geometry (GeoJSON LineString), distance and duration.
3. Query the stations inside the route's bounding box (indexed), keep those within the corridor around the route line, and compute each one's mile marker along the route.
4. Run the fuel-stop optimizer.
5. Return JSON — route, fuel stops, gallons, total cost — plus an HTML map page for viewing.

**External API calls per request: 1.**

## 4. Data profile — `fuel-prices-for-be-assessment.csv`

| Metric | Value |
|---|---|
| Rows | 8,151 |
| Columns | OPIS Truckstop ID, Truckstop Name, Address, City, State, Rack ID, Retail Price |
| Latitude / longitude columns | None |
| Unique station IDs | 6,738 |
| Station IDs appearing more than once | 678 — same city/state every time, only the price differs |
| City values with trailing whitespace | 1,256 |
| Canadian rows (AB, BC, MB, NB, NS, ON, QC, SK, YT) | 620 |
| Unique US stations | 6,626 |
| Unique US city + state pairs | 3,813 |
| US stations whose address names a highway (I-, US-, SR-, HWY…) | 96.6% |
| US stations whose address gives an exit number | 56.4% |
| Price range (all rows) | $2.687 – $6.399 per gallon |

## 5. Decisions

### D1. Routing provider — OSRM public server

| Option | API key | Alternative routes on long trips | Notes |
|---|---|---|---|
| **OSRM** (`router.project-osrm.org`) | No | Yes — requested count, not guaranteed | Max 1 request/second, non-commercial use, no uptime guarantee |
| OpenRouteService | Free key | No — alternatives only for trips under 100 km | Has fastest / shortest preference |
| Google Directions | Billing account | Yes | Not free |

**Chosen: OSRM.** Reviewers can run the project without signing up for anything, and it supports the bonus (D11).
OSRM returns the **fastest** route (least driving time), which is also what map apps return by default.

### D2. Start/finish geocoded locally

Routing APIs accept coordinates, not place names. The naive flow is 3 calls: geocode start, geocode finish, then route.
We resolve `"City, ST"` against the Census Gazetteer Places file (every US incorporated place and census-designated place, with a representative latitude/longitude), loaded once into a `City` table, so only the routing call remains.

- Trade-off: input must be `"City, ST"` (or raw coordinates).
- Possible extension: fall back to a geocoding API only when the local lookup fails — worst case 3 calls, still within the brief.

### D3. Station coordinates precomputed from City + State

The Address column holds highway exits (`I-44, EXIT 283 & US-69`), which street geocoders cannot resolve. Geocoding 3,813 city/state pairs through a free API would be slow and rate-limited.
Instead each station takes the coordinates of its town from the Census file, computed once at load time. Limitation and mitigations: E1, E2.

### D4. Data cleaning rules

- One row per OPIS ID, keeping the **lowest** price (the brief defines optimal as cost-effective).
- Trim whitespace in all text fields.
- Drop Canadian rows — routes are US-only and the Census file covers only the US.
- Skip stations whose city is not in the Census file; the loader reports the count.

### D5. Storage

- SQLite file database (Django's default): zero setup for reviewers; switching to PostgreSQL is a settings change.
- Tables: `FuelStation` (index on latitude + longitude) and `City` (index on name + state; names stored lowercase).
- Stations store their own latitude/longitude instead of a foreign key to `City`: the corridor query reads one indexed table with no join, and station coordinates can later be replaced with exact positions (see Future improvements) without touching `City`.
- The loader is a Django management command, run once. At request time data is read through the ORM with a bounding-box query; the geometry and optimizer run in memory on that filtered list.

### D6. Price type

Stored as float; totals rounded to 2 decimals in the response. Prices are 8-decimal averages feeding an estimate, and mixing `Decimal` with float distance maths raises errors in Python.

### D7. Fuel-stop algorithm — greedy solution to the gas station problem

For a fixed route, the greedy strategy below is proven optimal (Khuller, Malekian & Mestre, *To Fill or Not to Fill: The Gas Station Problem*).
Parameters: range 500 miles, 10 mpg → 50-gallon tank. At each station:

1. If a cheaper station is within range, buy just enough fuel to reach the nearest such station.
2. Otherwise, fill the tank and drive to the cheapest station within range.
3. If the destination is within range and nothing cheaper lies before it, buy just enough to finish.

Worked example — 1,100-mile trip, tank starts empty:

| At mile | Price | Decision | Bought | Cost |
|---|---|---|---|---|
| 0 | $3.50 | Mile 180 is cheaper and in range → buy for 180 mi | 18 gal | $63.00 |
| 180 | $3.20 | Skip 420 ($3.60); mile 560 is cheaper → buy for 380 mi | 38 gal | $121.60 |
| 560 | $3.00 | Nothing cheaper within 500 mi, finish (540 mi) out of range → fill up, go to cheapest in range (850) | 50 gal | $150.00 |
| 850 | $3.40 | Arrive with 21 gal; finish 250 mi away, nothing cheaper ahead → buy 4 gal | 4 gal | $13.60 |
| **Total** | | | **110 gal** (= 1,100 ÷ 10) | **$348.20** |

### D8. Tank at the start — starts empty, first fill near the start *(pending confirmation)*

Alternative considered: starts full. Then trips under 500 miles show $0 and longer trips count only fuel bought en route, which looks like a bug when checked against miles ÷ 10 × price.

### D9. Route representation

GeoJSON LineString; coordinates are **[longitude, latitude]**. OSRM distance is in metres (÷ 1,609.34 → miles), duration in seconds. Turn-by-turn steps are not requested — not needed for the brief, and it keeps the response small.

### D10. Map output

- In the JSON response: route line and stops as GeoJSON — viewable by pasting into geojson.io.
- `/map/?start=…&finish=…`: an HTML page drawing the route and fuel stops with Leaflet and OpenStreetMap tiles.

### D11. Bonus — optional `optimize` parameter

`time` (default) | `distance` | `cost`

One OSRM call requesting alternatives → run corridor + optimizer on each candidate → pick the minimum by the chosen key, using a selector map (Strategy pattern) rather than an if/else chain.

- OSRM does not guarantee alternatives; the response reports how many routes were compared.
- `distance` means the shortest *among the routes offered* — OSRM has no pure shortest-distance mode.

## 6. Edge cases and limitations

| # | Case | Handling |
|---|---|---|
| E1 | Station placed at its town's centre, not its exact exit — may miss a station on the route, include one slightly off it, or shift its mile marker | Corridor of ~10 miles (configurable); mile marker taken from the nearest point on the route; optional range safety buffer (e.g. plan legs ≤ 480 mi). Worst case is a slightly costlier plan, not an infeasible one |
| E2 | Large cities: many stations share one point (Phoenix 22, San Antonio 19, Indianapolis 15) | Same mitigations as E1; the largest source of position error |
| E3 | Station's city not found in the Census file | Skipped at load; count reported |
| E4 | Canadian stations | Excluded |
| E5 | Start/finish not found, or outside the US | 400 error with a clear message *(planned)* |
| E6 | A stretch longer than 500 miles with no station in the corridor | Error explaining the trip cannot be completed on this range *(planned)* |
| E7 | Trip under 500 miles | One fill near the start (under D8) |
| E8 | OSRM returns no alternatives | Compare whatever is returned (bonus mode) |
| E9 | OSRM public server limits | Documented; acceptable for an assessment |

## 7. Future improvements

- Exact station coordinates: match highway + exit number against OpenStreetMap highway-exit data. Only the loader would change.
- Geocoding-API fallback for street addresses.
- Include detour distance when choosing a station.
- Cache routes for repeated start/finish pairs.
- Spatial index for stations: the (latitude, longitude) B-tree index narrows the bounding-box query on latitude and filters longitude within it; a true spatial index (PostGIS / SQLite R*Tree) would be the production choice.

## 8. Build log

- **Step 1** — Django 6.1.1 project (`config`) with a `routing` app, Django REST Framework 3.18.1, Python 3.12 virtual environment.
- **Step 2** *(in progress)* — `FuelStation` and `City` models; Census Gazetteer 2026 Places file as the coordinate source.

## References

- Khuller, Malekian, Mestre — [To Fill or Not to Fill: The Gas Station Problem](https://www.cs.umd.edu/projects/gas/gas-station.pdf)
- [OSRM API documentation](https://project-osrm.org/docs/v5.24.0/api/)
- [OSRM demo server usage policy](https://github.com/Project-OSRM/osrm-backend/wiki/Demo-server)
- [OpenRouteService API restrictions](https://openrouteservice.org/restrictions/)
- [US Census Gazetteer files](https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html)
