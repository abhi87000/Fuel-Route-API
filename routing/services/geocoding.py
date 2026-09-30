from routing.models import City
from routing.utils import normalize

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN",
    "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH",
    "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT",
    "VT", "VA", "WA", "WV", "WI", "WY",
}


class LocationNotFound(Exception):
    pass


def geocode(location):
    parts = [part.strip() for part in location.split(",")]
    if len(parts) != 2:
        raise LocationNotFound(f'Use the format "City, ST" - got "{location}"')

    city, state = parts[0], parts[1].upper()
    if state not in US_STATES:
        raise LocationNotFound(f"{state} is not a US state - start and finish must be in the USA")

    match = City.objects.filter(name=normalize(city), state=state).first()
    if match is None:
        raise LocationNotFound(f"Could not find {city}, {state}")
    return match.latitude, match.longitude