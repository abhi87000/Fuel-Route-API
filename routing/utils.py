import re
import unicodedata


def normalize(name):
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = name.lower()
    name = re.sub(r"\b(saint|sainte)\b", "st", name)
    name = re.sub(r"\bfort\b", "ft", name)
    name = re.sub(r"\bmount\b", "mt", name)
    return re.sub(r"[^a-z0-9]", "", name)
