"""Package sizes, in comparable units.

"1 gal", "128 fl oz" and "3.78 L" are the same amount of milk written three
ways, and a shelf that cannot see that will happily conclude a household's
consumption changed when only the label did.

Everything reduces to one of three base units: `ml` for volume, `g` for mass,
`ct` for countable things. Amounts are only ever compared WITHIN one product,
so the rule that matters is consistency, not absolute truth — and when a
product's own history mixes dimensions (someone's milk recorded once by volume
and once by weight), the caller falls back to counting packages rather than
comparing millilitres to grams.
"""
import re

VOLUME = {"ml": 1.0, "milliliter": 1.0, "millilitre": 1.0, "cl": 10.0,
          "l": 1000.0, "lt": 1000.0, "ltr": 1000.0, "liter": 1000.0, "litre": 1000.0,
          "floz": 29.5735, "flozs": 29.5735,
          "pt": 473.176, "pint": 473.176, "qt": 946.353, "quart": 946.353,
          "gal": 3785.41, "gallon": 3785.41}
MASS = {"mg": 0.001, "g": 1.0, "gr": 1.0, "gram": 1.0, "grams": 1.0,
        "kg": 1000.0, "kilo": 1000.0, "kilogram": 1000.0,
        "oz": 28.3495, "ounce": 28.3495, "ounces": 28.3495,
        "lb": 453.592, "lbs": 453.592, "pound": 453.592, "pounds": 453.592}
COUNT = {"ct": 1.0, "count": 1.0, "pk": 1.0, "pack": 1.0, "pcs": 1.0, "piece": 1.0,
         "pieces": 1.0, "ea": 1.0, "each": 1.0, "roll": 1.0, "rolls": 1.0,
         "dozen": 12.0, "doz": 12.0}

_ALL = [(VOLUME, "ml"), (MASS, "g"), (COUNT, "ct")]

# "1 gal", "9.25oz", "2 x 500 ml", "12ct"
_SIZE_RE = re.compile(
    r"(?:(?P<mult>\d+(?:\.\d+)?)\s*[x×]\s*)?"
    r"(?P<n>\d+(?:\.\d+)?)\s*"
    r"(?P<u>fl\.?\s*oz|floz|[a-z]{1,7})\b", re.I)


def parse_size(text: str) -> tuple[float, str] | None:
    """One package's size, normalised. None when the text says nothing usable.

    Returns (amount, base_unit). "2 x 500ml" is one 1000 ml package, because a
    twin-pack bought once is one purchase of twice the stuff.
    """
    if not text:
        return None
    hay = text.lower().replace("fl. oz", "floz").replace("fl oz", "floz")
    for m in _SIZE_RE.finditer(hay):
        unit = re.sub(r"[^a-z]", "", m.group("u"))
        for table, base in _ALL:
            if unit in table:
                n = float(m.group("n")) * table[unit]
                mult = float(m.group("mult")) if m.group("mult") else 1.0
                return round(n * mult, 4), base
    return None
