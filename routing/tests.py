from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from routing.models import City, FuelStation
from routing.services.fuel_optimizer import NoFeasiblePlan, plan_fuel_stops


def stations(*pairs):
    return [{"mile": mile, "price": price, "name": f"S{mile}"} for mile, price in pairs]


class FuelOptimizerTests(SimpleTestCase):
    def test_worked_example_empty_tank(self):
        route = stations((0, 3.50), (180, 3.20), (420, 3.60), (560, 3.00), (850, 3.40), (1000, 3.80))
        plan = plan_fuel_stops(route, 1100, start_fuel_gallons=0)
        self.assertEqual([(s["mile"], round(s["gallons"], 2)) for s in plan["stops"]],
                         [(0, 18.0), (180, 38.0), (560, 50.0), (850, 4.0)])
        self.assertAlmostEqual(plan["total_gallons"], 110)
        self.assertAlmostEqual(plan["total_cost"], 348.20)

    def test_full_tank_short_trip_needs_no_stop(self):
        plan = plan_fuel_stops(stations((100, 3.00)), 400)
        self.assertEqual(plan["stops"], [])
        self.assertEqual(plan["total_cost"], 0)

    def test_full_tank_long_trip(self):
        plan = plan_fuel_stops(stations((300, 3.00), (600, 2.50)), 900)
        self.assertEqual([(s["mile"], round(s["gallons"], 2)) for s in plan["stops"]], [(300, 10.0), (600, 30.0)])
        self.assertAlmostEqual(plan["total_cost"], 105.0)

    def test_starting_fuel_does_not_reach_first_station(self):
        with self.assertRaises(NoFeasiblePlan):
            plan_fuel_stops(stations((120, 3.00)), 700, start_fuel_gallons=5)

    def test_gap_longer_than_range(self):
        with self.assertRaises(NoFeasiblePlan):
            plan_fuel_stops(stations((0, 3.00), (600, 3.00)), 1000, start_fuel_gallons=0)

    def test_zero_distance(self):
        self.assertEqual(plan_fuel_stops([], 0)["total_cost"], 0)


class RouteApiTests(TestCase):
    def setUp(self):
        City.objects.create(name="alpha", state="TX", latitude=30.0, longitude=-97.0)
        City.objects.create(name="beta", state="TX", latitude=30.0, longitude=-91.0)
        for i, (lon, price) in enumerate([(-96.99, 3.10), (-94.5, 2.90), (-92.0, 3.30)]):
            FuelStation.objects.create(opis_id=i, name=f"Stop {i}", address="I-10", city="x", state="TX",
                                       rack_id=1, price=price, latitude=30.0, longitude=lon)
        coordinates = [[-97.0 + k * 0.01, 30.0] for k in range(601)]
        self.route = {"distance_miles": 358.0, "duration_hours": 5.5, "coordinates": coordinates}

    def test_plan_route(self):
        with patch("routing.services.route_planner.get_routes", return_value=[self.route]) as mocked:
            response = self.client.post("/api/route/",
                                        {"start": "Alpha, TX", "finish": "Beta, TX", "start_fuel_percent": 10},
                                        content_type="application/json")
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertAlmostEqual(body["starting_fuel"]["gallons_used"], 5)
        self.assertAlmostEqual(body["starting_fuel"]["price_per_gallon"], 3.10)
        self.assertAlmostEqual(body["total_gallons"], 35.8)
        stops_cost = sum(stop["cost"] for stop in body["fuel_stops"])
        self.assertAlmostEqual(body["total_fuel_cost"], body["starting_fuel"]["cost"] + stops_cost, places=1)
        self.assertEqual([stop["name"] for stop in body["fuel_stops"]], ["Stop 0", "Stop 1"])
        self.assertLess(len(body["route_geometry"]["coordinates"]), len(self.route["coordinates"]))

    def test_full_tank_by_default(self):
        with patch("routing.services.route_planner.get_routes", return_value=[self.route]):
            response = self.client.post("/api/route/", {"start": "Alpha, TX", "finish": "Beta, TX"},
                                        content_type="application/json")
        body = response.json()
        self.assertEqual(body["start_fuel_percent"], 100)
        self.assertEqual(body["fuel_stops"], [])
        self.assertAlmostEqual(body["starting_fuel"]["gallons_used"], 35.8)
        self.assertAlmostEqual(body["starting_fuel"]["gallons_left_at_finish"], 14.2)
        self.assertAlmostEqual(body["total_fuel_cost"], 110.98)

    def test_empty_tank_cannot_leave(self):
        with patch("routing.services.route_planner.get_routes", return_value=[self.route]):
            response = self.client.post("/api/route/",
                                        {"start": "Alpha, TX", "finish": "Beta, TX", "start_fuel_percent": 0},
                                        content_type="application/json")
        self.assertEqual(response.status_code, 422)

    def test_start_fuel_percent_out_of_range(self):
        response = self.client.post("/api/route/",
                                    {"start": "Alpha, TX", "finish": "Beta, TX", "start_fuel_percent": 150},
                                    content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_location_outside_usa(self):
        response = self.client.post("/api/route/", {"start": "Toronto, ON", "finish": "Beta, TX"},
                                    content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_missing_field(self):
        response = self.client.post("/api/route/", {"start": "Alpha, TX"}, content_type="application/json")
        self.assertEqual(response.status_code, 400)
