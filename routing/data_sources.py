import csv
import re
from collections import defaultdict

from routing.utils import normalize

CANADIAN_PROVINCES = {
    "Alberta": "AB", "British Columbia": "BC", "Manitoba": "MB", "New Brunswick": "NB",
    "Newfoundland and Labrador": "NL", "Northwest Territories": "NT", "Nova Scotia": "NS",
    "Nunavut": "NU", "Ontario": "ON", "Prince Edward Island": "PE", "Quebec": "QC",
    "Saskatchewan": "SK", "Yukon": "YT",
}

PLACE_TYPE_SUFFIX = re.compile(
    r"\s+(city and borough|consolidated government|metropolitan government|unified government|"
    r"urban county|charter township|zona urbana|comunidad|municipality|corporation|village|borough|"
    r"city|town|township|CDP|CCD|plantation|UT|barrio|precinct|district)$"
)


def read_census_file(path, fips_to_state):
    places = defaultdict(list)
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="|"):
            state = row["USPS"]
            fips_to_state[row["GEOID"][:2]] = state
            point = (float(row["INTPTLAT"]), float(row["INTPTLONG"]))

            name = row["NAME"].strip().removesuffix(" (balance)")
            name = PLACE_TYPE_SUFFIX.sub("", name, count=1)

            names = [name]
            if "-" in name:
                names.append(name.split("-")[0])
            if name.endswith(" City"):
                names.append(name.removesuffix(" City"))

            for n in names:
                places[(normalize(n), state)].append(point)
    return places


def read_usgs_file(path, fips_to_state):
    places = defaultdict(list)
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f, delimiter="|"):
            state = fips_to_state.get(row["state_numeric"])
            if state is None or not row["prim_lat_dec"]:
                continue
            point = (float(row["prim_lat_dec"]), float(row["prim_long_dec"]))
            places[(normalize(row["feature_name"]), state)].append(point)
    return places


def read_canada_file(path):
    places = defaultdict(list)
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            province = CANADIAN_PROVINCES.get(row["Province - Territory"])
            if province is None:
                continue
            point = (float(row["Latitude"]), float(row["Longitude"]))
            places[(normalize(row["Geographical Name"]), province)].append(point)
    return places


def read_aliases(path):
    aliases = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            aliases[(normalize(row["csv_city"]), row["state"])] = normalize(row["official_name"])
    return aliases


def read_stations(path):
    stations = {}
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            state = row["State"].strip()
            opis_id = int(row["OPIS Truckstop ID"])
            price = float(row["Retail Price"])
            if opis_id in stations and stations[opis_id]["price"] <= price:
                continue

            stations[opis_id] = {
                "opis_id": opis_id,
                "name": row["Truckstop Name"].strip(),
                "address": row["Address"].strip(),
                "city": row["City"].strip(),
                "state": state,
                "rack_id": int(row["Rack ID"]),
                "price": price,
            }
    return stations
