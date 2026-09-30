class NoFeasiblePlan(Exception):
    pass


def plan_fuel_stops(stations, total_miles, max_range=500, mpg=10, start_fuel_gallons=None):
    """Greedy gas-station algorithm. `stations` must be sorted by `mile`.

    The start is not a station: the truck leaves with `start_fuel_gallons` (a full tank by default)
    and cannot buy anything until it reaches the first station.
    """
    tank_size = max_range / mpg
    start_fuel = tank_size if start_fuel_gallons is None else start_fuel_gallons
    if total_miles <= 0:
        return {"stops": [], "total_gallons": 0.0, "total_cost": 0.0, "start_fuel_gallons": start_fuel}

    ahead = [s for s in stations if 0 <= s["mile"] < total_miles]
    positions = [0.0] + [s["mile"] for s in ahead]
    route_stations = [None] + ahead

    stops = []
    fuel = start_fuel
    current = 0

    def buy(index, gallons):
        if gallons > 1e-9:
            station = route_stations[index]
            stops.append({**station, "gallons": gallons, "cost": gallons * station["price"]})

    while True:
        here = positions[current]
        at_start = current == 0
        price = float("inf") if at_start else route_stations[current]["price"]
        reach = here + (fuel * mpg if at_start else max_range)

        in_range = []
        next_cheaper = None
        for j in range(current + 1, len(positions)):
            if positions[j] > reach:
                break
            in_range.append(j)
            if next_cheaper is None and route_stations[j]["price"] < price:
                next_cheaper = j

        if next_cheaper is not None:
            needed = (positions[next_cheaper] - here) / mpg
            if not at_start:
                buy(current, max(0.0, needed - fuel))
            fuel = max(fuel, needed) - needed
            current = next_cheaper
        elif total_miles <= reach:
            needed = (total_miles - here) / mpg
            if not at_start:
                buy(current, max(0.0, needed - fuel))
            break
        elif in_range:
            cheapest = min(in_range, key=lambda j: (route_stations[j]["price"], -positions[j]))
            buy(current, tank_size - fuel)
            fuel = tank_size - (positions[cheapest] - here) / mpg
            current = cheapest
        elif at_start:
            first = f"the first station is at mile {positions[1]:.0f}" if len(positions) > 1 else "there is no station on the route"
            raise NoFeasiblePlan(
                f"The starting fuel ({start_fuel:.1f} gal, {start_fuel * mpg:.0f} miles of range) is not enough - {first}"
            )
        else:
            raise NoFeasiblePlan(
                f"No fuel station within {max_range} miles after mile {here:.0f} - "
                f"the trip cannot be completed on this range"
            )

    return {
        "stops": stops,
        "total_gallons": sum(s["gallons"] for s in stops),
        "total_cost": sum(s["cost"] for s in stops),
        "start_fuel_gallons": start_fuel,
    }
