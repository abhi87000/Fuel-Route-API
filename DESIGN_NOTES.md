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

### Coordinate sources — Census Gazetteer (Places, County Subdivisions) + USGS GNIS Populated Places (measured)

| Metric | Value |
|---|---|
| Rows | 32,363 US places (incorporated places + census-designated places) |
| Format | Pipe-delimited (`|`); name in `NAME`, coordinates in `INTPTLAT` / `INTPTLONG` |
| Name format | Includes a type suffix — `Abbeville city`, `Abanda CDP`, `... town/village/borough` — stripped before matching |
| Same name twice in one state (after stripping) | 212 cases — why `City` has no unique constraint on (name, state) |
| Name normalisation for matching | lowercase, strip accents (Cañon → canon), drop spaces/punctuation (De Forest = DeForest), Saint/Fort/Mount → St/Ft/Mt, consolidated cities (Athens-Clarke County → Athens) |
| US stations placed — Places file only | 6,321 of 6,626 (95.4%) — misses concentrated in New England / mid-Atlantic townships, which the Census files as *county subdivisions* |
| County Subdivisions file added as a fallback (36,381 rows, same format; Places wins when both match) | **6,449 of 6,626 (97.3%)** |
| Still unmatched (177 stations, 133 city/state pairs) | Tiny unincorporated spots and postal names (e.g. Breezewood PA, Clines Corners NM, Jean NV) and neighbourhoods (Antioch TN). Skipped and listed by the loader |
| Pitfall found while testing | Strip the type suffix only once — stripping twice turned "Oklahoma City city" into "Oklahoma" |
| USGS GNIS "Populated Places" file added as third source (190,923 rows; includes unincorporated communities such as Breezewood PA, Jean NV; state given as FIPS code, mapped to USPS via the Census GEOID prefix) | 6,614 of 6,626 (99.8%) |
| Last 9 city names resolved with `data/city_aliases.csv`, each backed by the station's own address (e.g. Ottawa Lake MI, "US-23 EXIT 5" → Whiteford township; Willow Beach AZ, "US-93" → White Hills; Hot Springs National Park AR → Hot Springs) | **6,626 of 6,626 (100%)** |

**Canada:** NRCan Canadian Geographical Names Database, filtered to the 29,716 *Populated Place* entries and saved as `data/canada_populated_places.csv` (1.4 MB; the full 77 MB download is kept out of git). All 85 Canadian station towns match; 14 have duplicate names and use the Rack ID rule. **Total: 6,738 of 6,738 stations placed (6,626 US + 112 Canada).**

**Source priority:** Census Places → Census County Subdivisions → USGS GNIS → NRCan (Canada) → alias file applied first as a name translation.

**Ambiguous names** (same name more than once in a state — e.g. GNIS has 12 places called Antioch in Tennessee): pick the candidate nearest to the average position of other stations that share the same **Rack ID**. Rack ID is the OPIS wholesale fuel terminal a station's price is based on, so stations sharing a rack are in the same supply region. Spot checks: Antioch TN → Davidson County (Nashville), Chesterfield VA → Chesterfield County, Clear Brook VA → Frederick County (I-81).

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
- Canadian rows are **kept** (revised). Start and finish are in the US, but some US-to-US routes run through Canada — Seattle → Anchorage uses the Alaska Highway through BC and Yukon (the CSV has stations in Dawson Creek, Fort Nelson, Watson Lake, Whitehorse), and Detroit → Buffalo is often fastest through Ontario. 620 rows = 112 stations in 85 towns. Their prices sit on the same per-gallon scale as US prices (median $4.45 vs $3.40), so they are used as given. Coordinates come from Natural Resources Canada's Canadian Geographical Names Database.
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

Verified: the implementation reproduces the worked example below ($348.20) and matched a brute-force dynamic-programming solver on 60 random trips.

Worked example — 1,100-mile trip, tank starts empty:

| At mile | Price | Decision | Bought | Cost |
|---|---|---|---|---|
| 0 | $3.50 | Mile 180 is cheaper and in range → buy for 180 mi | 18 gal | $63.00 |
| 180 | $3.20 | Skip 420 ($3.60); mile 560 is cheaper → buy for 380 mi | 38 gal | $121.60 |
| 560 | $3.00 | Nothing cheaper within 500 mi, finish (540 mi) out of range → fill up, go to cheapest in range (850) | 50 gal | $150.00 |
| 850 | $3.40 | Arrive with 21 gal; finish 250 mi away, nothing cheaper ahead → buy 4 gal | 4 gal | $13.60 |
| **Total** | | | **110 gal** (= 1,100 ÷ 10) | **$348.20** |

### D8. Tank at the start — starts empty, first fill near the start *(decided)*

The truck starts empty; its first purchase is priced at the cheapest station within 25 miles of the start (counted as mile 0). If no station is that close, the API returns an error rather than guessing.

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

### D12. Code structure

```
routing/
  models.py                  FuelStation, City
  utils.py                   normalize() - shared name cleaning
  data_sources.py            readers for the CSV and the four place files
  management/commands/
    load_stations.py         one-time loader: match coordinates, save
  services/
    geocoding.py             "City, ST" -> coordinates (DB lookup, US only)
    osrm.py                  the single routing API call
    corridor.py              stations near the route + mile markers
    fuel_optimizer.py        greedy algorithm - pure logic, no Django/HTTP
    route_planner.py         orchestrates the services
  serializers.py             request validation
  views.py                   thin API view
```

SOLID applied the Python way: one reason to change per module; the optimizer takes and returns plain data (so it is unit-testable without a database or network); new `optimize` modes are new entries in a selector map. Plain functions and modules instead of Java-style interfaces - swapping the routing provider means replacing `osrm.py`.

## 6. Edge cases and limitations

| # | Case | Handling |
|---|---|---|
| E1 | Station placed at its town's centre, not its exact exit — may miss a station on the route, include one slightly off it, or shift its mile marker | Corridor of ~10 miles (configurable); mile marker taken from the nearest point on the route; optional range safety buffer (e.g. plan legs ≤ 480 mi). Worst case is a slightly costlier plan, not an infeasible one |
| E2 | Large cities: many stations share one point (Phoenix 22, San Antonio 19, Indianapolis 15) | Same mitigations as E1; the largest source of position error |
| E2b | Duplicate place names within a state | Disambiguated by Rack ID proximity (see coordinate sources) |
| E3 | Station's city not in any source | Resolved to 100% with three sources + a 9-row alias file; the loader still reports any future misses instead of failing |
| E4 | Canadian stations | Included — needed for US-to-US routes that cross Canada (Alaska Highway, Detroit–Buffalo via Ontario); placed with the NRCan Canadian Geographical Names file |
| E5 | Start/finish not found, or outside the US | 400 error with a clear message *(planned)* |
| E6 | A stretch longer than 500 miles with no station in the corridor | `NoFeasiblePlan` error naming the mile where the gap starts |
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
