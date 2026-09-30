import requests
from django.core.cache import cache

OSRM_URL = "https://router.project-osrm.org/route/v1/driving/{coords}"
METERS_PER_MILE = 1609.344
CACHE_SECONDS = 60 * 60


class RoutingError(Exception):
    pass


def get_routes(start, finish, alternatives=False):
    coords = f"{start[1]},{start[0]};{finish[1]},{finish[0]}"
    cache_key = f"osrm:{coords}:{alternatives}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    params = {
        "overview": "full",
        "geometries": "geojson",
        "alternatives": "true" if alternatives else "false",
    }
    headers = {"User-Agent": "fuel-route-api (Spotter assessment)"}

    try:
        response = requests.get(OSRM_URL.format(coords=coords), params=params, headers=headers, timeout=15)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RoutingError(f"Routing service unavailable: {exc}") from exc

    data = response.json()
    if data.get("code") != "Ok" or not data.get("routes"):
        raise RoutingError(f"No route found ({data.get('code')})")

    routes = [
        {
            "distance_miles": route["distance"] / METERS_PER_MILE,
            "duration_hours": route["duration"] / 3600,
            "coordinates": route["geometry"]["coordinates"],
        }
        for route in data["routes"]
    ]
    cache.set(cache_key, routes, CACHE_SECONDS)
    return routes
