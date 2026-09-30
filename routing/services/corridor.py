import math
from collections import defaultdict

from routing.models import FuelStation

CORRIDOR_MILES = 10
SAMPLE_EVERY_MILES = 1
EARTH_RADIUS_MILES = 3958.8
MILES_PER_DEGREE = 69.0


def haversine_miles(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def sample_route(coordinates):
    """Odometer: turn [lon, lat] points into (lat, lon, mile) about one mile apart."""
    first_lon, first_lat = coordinates[0]
    samples = [(first_lat, first_lon, 0.0)]
    miles = 0.0
    prev_lat, prev_lon = first_lat, first_lon
    last_kept = 0.0

    for lon, lat in coordinates[1:]:
        miles += haversine_miles(prev_lat, prev_lon, lat, lon)
        prev_lat, prev_lon = lat, lon
        if miles - last_kept >= SAMPLE_EVERY_MILES:
            samples.append((lat, lon, miles))
            last_kept = miles

    if samples[-1][2] != miles:
        samples.append((prev_lat, prev_lon, miles))
    return samples


class Grid:
    """Hash map of ~10-mile squares; cell width is set at the route's northernmost latitude."""

    def __init__(self, max_abs_lat):
        self.lat_size = CORRIDOR_MILES / MILES_PER_DEGREE
        self.lon_size = CORRIDOR_MILES / (MILES_PER_DEGREE * math.cos(math.radians(max_abs_lat)))
        self.cells = defaultdict(list)

    def cell(self, lat, lon):
        return int(lat // self.lat_size), int(lon // self.lon_size)

    def add(self, lat, lon, item):
        self.cells[self.cell(lat, lon)].append(item)

    def nearby(self, lat, lon):
        row, col = self.cell(lat, lon)
        for d_row in (-1, 0, 1):
            for d_col in (-1, 0, 1):
                yield from self.cells.get((row + d_row, col + d_col), ())


def find_stations_along_route(route):
    samples = sample_route(route["coordinates"])
    measured_miles = samples[-1][2]
    scale = route["distance_miles"] / measured_miles if measured_miles else 1.0

    lats = [lat for lat, _, _ in samples]
    lons = [lon for _, lon, _ in samples]
    lat_margin = CORRIDOR_MILES / MILES_PER_DEGREE
    max_abs_lat = min(max(abs(min(lats)), abs(max(lats))) + lat_margin, 85)
    lon_margin = CORRIDOR_MILES / (MILES_PER_DEGREE * math.cos(math.radians(max_abs_lat)))

    grid = Grid(max_abs_lat)
    for sample in samples:
        grid.add(sample[0], sample[1], sample)

    candidates = FuelStation.objects.filter(
        latitude__range=(min(lats) - lat_margin, max(lats) + lat_margin),
        longitude__range=(min(lons) - lon_margin, max(lons) + lon_margin),
    ).values("id", "name", "address", "city", "state", "latitude", "longitude", "price")

    found = []
    for station in candidates:
        best_distance, best_mile = None, None
        for lat, lon, mile in grid.nearby(station["latitude"], station["longitude"]):
            distance = haversine_miles(station["latitude"], station["longitude"], lat, lon)
            if best_distance is None or distance < best_distance:
                best_distance, best_mile = distance, mile
        if best_distance is not None and best_distance <= CORRIDOR_MILES:
            found.append({**station, "mile": best_mile * scale, "off_route_miles": best_distance})

    found.sort(key=lambda s: s["mile"])
    return found


def nearest_station(lat, lon, search_degrees=3):
    fields = ("name", "city", "state", "latitude", "longitude", "price")
    nearby = FuelStation.objects.filter(
        latitude__range=(lat - search_degrees, lat + search_degrees),
        longitude__range=(lon - search_degrees, lon + search_degrees),
    ).values(*fields)
    best, best_distance = None, None
    for station in nearby or FuelStation.objects.values(*fields):
        distance = haversine_miles(lat, lon, station["latitude"], station["longitude"])
        if best_distance is None or distance < best_distance:
            best, best_distance = station, distance
    return None if best is None else {**best, "distance_miles": best_distance}
