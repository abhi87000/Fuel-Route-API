from routing.services.corridor import find_stations_along_route, nearest_station, sample_route
from routing.services.fuel_optimizer import NoFeasiblePlan, plan_fuel_stops
from routing.services.geocoding import geocode
from routing.services.osrm import get_routes

MAX_RANGE_MILES = 500
MILES_PER_GALLON = 10

SELECTORS = {
    "time": lambda option: option["route"]["duration_hours"],
    "distance": lambda option: option["route"]["distance_miles"],
    "cost": lambda option: option["total_cost"],
}


def plan_trip(start, finish, optimize="time", start_fuel_percent=100):
    start_point = geocode(start)
    finish_point = geocode(finish)
    routes = get_routes(start_point, finish_point, alternatives=optimize != "time")
    start_fuel_gallons = MAX_RANGE_MILES / MILES_PER_GALLON * start_fuel_percent / 100
    start_price = nearest_station(*start_point)

    options, first_error = [], None
    for route in routes:
        stations = find_stations_along_route(route)
        try:
            fuel = plan_fuel_stops(
                stations, route["distance_miles"], MAX_RANGE_MILES, MILES_PER_GALLON, start_fuel_gallons
            )
        except NoFeasiblePlan as exc:
            first_error = first_error or exc
            continue
        starting = starting_fuel(route["distance_miles"], fuel, start_fuel_percent, start_price)
        options.append({
            "route": route,
            "fuel": fuel,
            "starting": starting,
            "total_cost": starting["cost"] + fuel["total_cost"],
            "stations_on_route": len(stations),
        })

    if not options:
        raise first_error

    best = min(options, key=SELECTORS[optimize])
    return build_response(start, finish, start_point, finish_point, optimize, len(routes), best)


def starting_fuel(distance_miles, fuel, percent, price_station):
    """The fuel already in the tank is charged at the price of the station nearest to the start.

    Only the part used on this trip is charged; whatever is left at the finish stays in the tank.
    """
    in_tank = fuel["start_fuel_gallons"]
    used = max(0.0, min(in_tank, distance_miles / MILES_PER_GALLON - fuel["total_gallons"]))
    price = price_station["price"] if price_station else 0.0
    return {
        "percent": percent,
        "gallons_in_tank": in_tank,
        "gallons_used": used,
        "gallons_left_at_finish": in_tank - used,
        "price_per_gallon": price,
        "cost": used * price,
        "priced_at": price_station,
    }


def build_response(start, finish, start_point, finish_point, optimize, routes_compared, best):
    route, fuel, starting = best["route"], best["fuel"], best["starting"]
    priced_at = starting["priced_at"]
    return {
        "start": {"query": start, "latitude": start_point[0], "longitude": start_point[1]},
        "finish": {"query": finish, "latitude": finish_point[0], "longitude": finish_point[1]},
        "optimize": optimize,
        "start_fuel_percent": starting["percent"],
        "routes_compared": routes_compared,
        "distance_miles": round(route["distance_miles"], 1),
        "duration_hours": round(route["duration_hours"], 1),
        "total_gallons": round(starting["gallons_used"] + fuel["total_gallons"], 2),
        "total_fuel_cost": round(best["total_cost"], 2),
        "stations_on_route": best["stations_on_route"],
        "assumptions": {
            "max_range_miles": MAX_RANGE_MILES,
            "miles_per_gallon": MILES_PER_GALLON,
            "starting_fuel": (
                "the fuel in the tank at the start is charged at the price of the station nearest to the start; "
                "only the part used on this trip is charged, whatever is left at the finish stays in the tank"
            ),
            "route_geometry": "simplified to about one point per mile",
        },
        "starting_fuel": {
            "percent": starting["percent"],
            "gallons_in_tank": round(starting["gallons_in_tank"], 2),
            "gallons_used": round(starting["gallons_used"], 2),
            "gallons_left_at_finish": round(starting["gallons_left_at_finish"], 2),
            "price_per_gallon": round(starting["price_per_gallon"], 3),
            "cost": round(starting["cost"], 2),
            "priced_at": None if priced_at is None else (
                f'{priced_at["name"]}, {priced_at["city"]}, {priced_at["state"]} '
                f'({priced_at["distance_miles"]:.1f} miles from the start)'
            ),
        },
        "fuel_stops": [format_stop(stop) for stop in fuel["stops"]],
        "route_geometry": {
            "type": "LineString",
            "coordinates": [[round(lon, 5), round(lat, 5)] for lat, lon, _ in sample_route(route["coordinates"])],
        },
    }


def format_stop(stop):
    return {
        "name": stop["name"],
        "address": stop["address"],
        "city": stop["city"],
        "state": stop["state"],
        "latitude": stop["latitude"],
        "longitude": stop["longitude"],
        "mile": round(stop["mile"], 1),
        "price_per_gallon": round(stop["price"], 3),
        "gallons": round(stop["gallons"], 2),
        "cost": round(stop["cost"], 2),
    }
