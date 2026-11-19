"""Merchant category codes (ISO 18245) → the bank's own category name for a row.

A card transaction carries the 4-digit code the card network assigned the merchant (5411
for a supermarket). Read as a ``category_hint`` it gives Lucius the bank's signal beside the
merchant name when categorising — a hint, never a category: the user's own categories decide.
Unknown codes give no hint rather than a guess.
"""

from __future__ import annotations

from typing import Optional

_CODES: dict[int, str] = {
    4111: "Commuter transport",
    4112: "Railways",
    4121: "Taxis and rideshare",
    4131: "Bus lines",
    4511: "Airlines",
    4722: "Travel agencies",
    4784: "Tolls",
    4789: "Transport services",
    4812: "Telecoms",
    4814: "Telecoms",
    4899: "Cable and streaming",
    4900: "Utilities",
    5300: "Wholesale clubs",
    5311: "Department stores",
    5331: "Variety stores",
    5411: "Grocery stores and supermarkets",
    5422: "Butchers",
    5441: "Confectioners",
    5451: "Dairy shops",
    5462: "Bakeries",
    5499: "Food shops",
    5511: "Car dealers",
    5521: "Car dealers",
    5532: "Car parts",
    5533: "Car parts",
    5541: "Fuel stations",
    5542: "Fuel stations",
    5651: "Clothing",
    5661: "Shoe shops",
    5712: "Furniture",
    5722: "Household appliances",
    5732: "Electronics",
    5734: "Software",
    5812: "Restaurants",
    5813: "Bars and pubs",
    5814: "Fast food",
    5912: "Pharmacies",
    5921: "Off-licences",
    5941: "Sporting goods",
    5942: "Bookshops",
    5943: "Stationery",
    5945: "Toys and hobbies",
    5977: "Cosmetics",
    5995: "Pet shops",
    5999: "Specialist shops",
    6011: "Cash withdrawals",
    6012: "Financial services",
    6300: "Insurance",
    6513: "Rent and property",
    7011: "Hotels",
    7230: "Hair and beauty",
    7298: "Health and beauty spas",
    7512: "Car rental",
    7523: "Parking",
    7832: "Cinemas",
    7922: "Theatre and events",
    7941: "Sports clubs",
    7997: "Gyms and clubs",
    7999: "Recreation",
    8011: "Doctors",
    8021: "Dentists",
    8043: "Opticians",
    8062: "Hospitals",
    8099: "Health services",
    8211: "Schools",
    8220: "Colleges and universities",
    8299: "Education services",
    8398: "Charities",
    9311: "Tax payments",
}

# Whole blocks the networks assign to one industry (airline, car-rental and hotel brands).
_RANGES: tuple[tuple[int, int, str], ...] = (
    (3000, 3299, "Airlines"),
    (3351, 3441, "Car rental"),
    (3501, 3999, "Hotels"),
    (5611, 5699, "Clothing"),
    (9211, 9399, "Government services"),
)


def merchant_category(code: object) -> Optional[str]:
    """The category name for a merchant category code, or ``None`` when absent or unknown."""
    try:
        mcc = int(str(code).strip())
    except (TypeError, ValueError):
        return None
    if mcc in _CODES:
        return _CODES[mcc]
    for low, high, name in _RANGES:
        if low <= mcc <= high:
            return name
    return None
