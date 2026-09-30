# Fuel Route API

A Django REST API that takes a start and a finish location in the USA and returns:

- the driving route, as GeoJSON and as an interactive map page
- the most cost-effective places to refuel along it, for a vehicle with a **500-mile range**
- the total fuel cost, at **10 miles per gallon**

Fuel prices come from `data/fuel-prices-for-be-assessment.csv`. Each request makes **one** call to the routing API (OSRM). Everything else runs locally against SQLite.

## Quick start

Requires Python 3.12+.

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows (PowerShell: .\.venv\Scripts\Activate.ps1)
source .venv/bin/activate         # macOS / Linux

pip install -r requirements.txt
python manage.py migrate
python manage.py load_stations    # one-time: loads 6,738 stations with coordinates
python manage.py runserver
```

`load_stations` prints: `Loaded 6738 fuel stations and 208112 cities (84 resolved by Rack ID).`

## API

### `POST /api/route/` (also `GET /api/route/?start=…&finish=…&start_fuel_percent=…`)

```json
{
  "start": "Chicago, IL",
  "finish": "Houston, TX",
  "optimize": "time",
  "start_fuel_percent": 100
}
```

| Field | Required | Description |
|---|---|---|
| `start`, `finish` | yes | `"City, ST"`, any US city, town or census place |
| `start_fuel_percent` | no | Fuel in the tank at the start, 0–100 (default `100` = full, 500 miles). The truck can only drive this far before its first stop. The part of it used on the trip is charged at the price of the station nearest to the start |
| `optimize` | no | `time` (default): the fastest route. `distance` / `cost`: OSRM's alternative routes are compared (still in the same single call) and the shortest / cheapest in fuel is returned |

**Response** (abridged):

```json
{
  "map_url": "http://127.0.0.1:8000/api/map/?start=Chicago%2C+IL&finish=Houston%2C+TX&optimize=time&start_fuel_percent=100",
  "start": { "query": "Chicago, IL", "latitude": 41.837045, "longitude": -87.684939 },
  "finish": { "query": "Houston, TX", "latitude": 29.785743, "longitude": -95.388806 },
  "optimize": "time",
  "start_fuel_percent": 100,
  "routes_compared": 1,
  "distance_miles": 1083.1,
  "duration_hours": 19.9,
  "total_gallons": 108.31,
  "total_fuel_cost": "…",
  "stations_on_route": 179,
  "assumptions": {
    "max_range_miles": 500,
    "miles_per_gallon": 10,
    "starting_fuel": "the fuel in the tank at the start is charged at the price of the station nearest to the start; only the part used on this trip is charged, whatever is left at the finish stays in the tank",
    "route_geometry": "simplified to about one point per mile"
  },
  "starting_fuel": {
    "percent": 100, "gallons_in_tank": 50.0, "gallons_used": 50.0, "gallons_left_at_finish": 0.0,
    "price_per_gallon": 3.569, "cost": 178.45, "priced_at": "ROAD RANGER #187, Chicago, IL (0.0 miles from the start)"
  },
  "fuel_stops": [
    { "name": "…", "city": "…", "state": "…", "mile": 0.0, "price_per_gallon": 0.0, "gallons": 0.0, "cost": 0.0, "…": "…" }
  ],
  "route_geometry": { "type": "LineString", "coordinates": [[-87.68494, 41.83705], "…"] }
}
```

`route_geometry` is standard GeoJSON (`[longitude, latitude]`) and can be pasted into [geojson.io](https://geojson.io). It is simplified to about one point per mile to keep the response small; the full OSRM geometry is still used for finding stations.

### `GET /api/map/?start=…&finish=…&start_fuel_percent=…`

An HTML page drawing the route and the fuel stops (Leaflet + OpenStreetMap tiles). The API response links to it as `map_url`. The route is cached, so opening the map does not call OSRM again.

### Errors

| Status | When |
|---|---|
| 400 | Missing field, wrong `"City, ST"` format, location not found or outside the USA, or `start_fuel_percent` outside 0–100 |
| 422 | The trip is impossible: the starting fuel does not reach the first station, or there is a gap of more than 500 miles between stations |
| 503 | The routing service is unavailable |

## How it works

```
"Chicago, IL" ──► local City table (0 API calls) ──► OSRM (1 API call) ──► route line
                                                                             │
             FuelStation table ──► Part A: stations within 10 mi of the road, with mile markers
                                                                             │
                                   Part B: greedy — where to stop, how many gallons, total cost
```

1. **Geocoding without an API.** Start/finish are looked up in a `City` table built from US Census, USGS and Natural Resources Canada place-name files.
2. **Station coordinates.** The CSV has no coordinates, and its addresses are highway exits. Each station gets its town's coordinates at load time. All 6,738 stations are placed (100%), Canadian ones included.
3. **Part A: stations along the route.** A prefix sum gives each route point a mile marker. A hash-map grid of 10-mile squares finds, for each station, the nearest route point. Stations within 10 miles of the road are kept, sorted by mile.
4. **Part B: fuel stops.** This is the greedy solution to the *gas station problem*, proven optimal for a fixed route. The truck leaves on the fuel it starts with and cannot buy until the first station. At each station: if cheaper fuel is within reach, buy only enough to get there; otherwise fill up and go to the cheapest station in range. Checked against a brute-force solver on 1,500 random trips.
5. **Starting fuel:** the email does not specify it, so the optional `start_fuel_percent` says it (default 100 = full tank). The truck can only drive as far as that fuel lasts before its first stop; if it cannot reach any station, the API returns 422. No fuel is free: the starting fuel used on the trip is charged at the price of the station nearest to the start, so total gallons = miles ÷ 10.

Typical response time is under 0.1 s plus the OSRM call.

Full reasoning, alternatives considered, diagrams and edge cases: **[DESIGN_NOTES.md](DESIGN_NOTES.md)**.

## Tests

```bash
python manage.py test routing
```

12 tests cover the optimizer (including the worked example: 1,100 miles from an empty tank → 110 gal, $348.20), full and partly full tanks, starting fuel that cannot reach a station, gaps longer than 500 miles, the starting-fuel cost, and the API with OSRM mocked, so no network is needed.

A Postman collection is included: `postman_collection.json`, 17 requests in 5 folders, each with tests (run the whole collection with the Runner):

| Folder | Requests |
|---|---|
| 1. Trips (default: full tank) | Chicago → Houston, Los Angeles → New York, Dallas → Houston (under 500 miles, no stop), Chicago → Houston as GET |
| 2. Starting fuel | Chicago → Houston with 50% and 5%, Los Angeles → New York with 60% |
| 3. Bonus - optimize | `cost` and `distance` |
| 4. Map page | `/api/map/` for Los Angeles → New York |
| 5. Errors | 400: outside the USA, wrong format, missing finish, `start_fuel_percent` above 100, unknown `optimize`; 422: 0% and 10% tank from Los Angeles |

The tests check that total gallons = miles ÷ 10, total cost = starting fuel + stops, the first stop is within the starting range, `assumptions` comes before the stops and `route_geometry` is last, and each error returns the right status and message.

## Project structure

```
routing/
  models.py                  FuelStation, City
  data_sources.py            readers for the fuel CSV and the place-name files
  management/commands/
    load_stations.py         one-time loader
  services/
    geocoding.py             "City, ST" -> coordinates
    osrm.py                  the single routing API call (cached)
    corridor.py              Part A: stations along the route + mile markers; nearest station to the start
    fuel_optimizer.py        Part B: greedy fuel-stop algorithm (pure Python)
    route_planner.py         puts the steps together
  serializers.py             request validation
  views.py                   API view + map page
  templates/routing/map.html
```

## Data sources

- Fuel prices: provided CSV
- [US Census Gazetteer files](https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html) (Places, County Subdivisions)
- [USGS GNIS](https://www.usgs.gov/tools/geographic-names-information-system-gnis) Populated Places
- [Natural Resources Canada](https://natural-resources.canada.ca/earth-sciences/geography/download-geographical-names-data/9245) Geographical Names (filtered to populated places)
- Routing: [OSRM](https://project-osrm.org/) public demo server (no API key; max 1 request/second)
