import math
from collections import defaultdict
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from routing.data_sources import (
    read_aliases,
    read_canada_file,
    read_census_file,
    read_stations,
    read_usgs_file,
)
from routing.models import City, FuelStation
from routing.utils import normalize

DATA_DIR = Path(settings.BASE_DIR) / "data"
FUEL_CSV = DATA_DIR / "fuel-prices-for-be-assessment.csv"
CENSUS_PLACES = DATA_DIR / "2026_Gaz_place_national.txt"
CENSUS_COUSUBS = DATA_DIR / "2026_Gaz_cousubs_national.txt"
USGS_PLACES = DATA_DIR / "PopulatedPlaces_National.txt"
CANADA_PLACES = DATA_DIR / "canada_populated_places.csv"
ALIASES_CSV = DATA_DIR / "city_aliases.csv"


def approx_miles(a, b):
    d_lat = a[0] - b[0]
    d_lon = (a[1] - b[1]) * math.cos(math.radians(a[0]))
    return math.hypot(d_lat, d_lon) * 69


class Command(BaseCommand):
    help = "Load fuel stations and US cities into the database"

    def handle(self, *args, **options):
        fips_to_state = {}
        sources = [
            read_census_file(CENSUS_PLACES, fips_to_state),
            read_census_file(CENSUS_COUSUBS, fips_to_state),
        ]
        sources.append(read_usgs_file(USGS_PLACES, fips_to_state))
        sources.append(read_canada_file(CANADA_PLACES))
        aliases = read_aliases(ALIASES_CSV)

        def find_candidates(city, state):
            key = (normalize(city), state)
            key = (aliases.get(key, key[0]), state)
            for source in sources:
                if key in source:
                    return source[key]
            return []

        stations = read_stations(FUEL_CSV)
        rack_points = defaultdict(list)
        ambiguous = []
        missing = []

        for station in stations.values():
            candidates = find_candidates(station["city"], station["state"])
            if not candidates:
                missing.append(station)
            elif len(candidates) == 1:
                station["point"] = candidates[0]
                rack_points[station["rack_id"]].append(candidates[0])
            else:
                ambiguous.append((station, candidates))

        # Same name more than once in a state: pick the one nearest other stations on the same rack
        for station, candidates in ambiguous:
            peers = rack_points.get(station["rack_id"])
            if peers:
                centre = (
                    sum(p[0] for p in peers) / len(peers),
                    sum(p[1] for p in peers) / len(peers),
                )
                station["point"] = min(candidates, key=lambda p: approx_miles(p, centre))
            else:
                station["point"] = candidates[0]

        city_rows = {}
        for source in sources:
            for (name, state), points in source.items():
                if (name, state) not in city_rows:
                    city_rows[(name, state)] = City(
                        name=name, state=state, latitude=points[0][0], longitude=points[0][1]
                    )

        station_rows = [
            FuelStation(
                opis_id=s["opis_id"],
                name=s["name"],
                address=s["address"],
                city=s["city"],
                state=s["state"],
                rack_id=s["rack_id"],
                price=s["price"],
                latitude=s["point"][0],
                longitude=s["point"][1],
            )
            for s in stations.values()
            if "point" in s
        ]

        with transaction.atomic():
            FuelStation.objects.all().delete()
            City.objects.all().delete()
            City.objects.bulk_create(city_rows.values(), batch_size=5000)
            FuelStation.objects.bulk_create(station_rows, batch_size=1000)

        self.stdout.write(self.style.SUCCESS(
            f"Loaded {len(station_rows)} fuel stations and {len(city_rows)} cities "
            f"({len(ambiguous)} resolved by Rack ID)."
        ))
        for s in missing:
            self.stdout.write(self.style.WARNING(f"No coordinates for {s['city']}, {s['state']} (station {s['opis_id']})"))
