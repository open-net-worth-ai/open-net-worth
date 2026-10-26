"""Collections: a watch collection beside the seeded Tudor, and a Pokémon card binder in a
custom category — so the Assets register shows a category with many items, a custom category,
and a long-tail spread of values. Each item is one current valuation and a photo of that exact
item, written through ``apply_owned_asset_change`` like every other owned asset in the book.

Watch photos are Wikimedia Commons files in photos/ (credits in photos/CREDITS.md). A card's
photo is its scan from the Pokémon TCG image CDN, fetched while the book is built: card art is
copyrighted, so no scan is committed or shipped in the image. An offline build, or a fetch that
fails, falls back to one freely licensed photo of a card pile (``CARD_FALLBACK``)."""

from __future__ import annotations

import logging
from datetime import date

from backend.scripts.demo._timeline import add_months

_log = logging.getLogger(__name__)

WATCHES: tuple[tuple[str, float, str], ...] = (
    ("Seiko 5 SNK809", 80.0, "seiko-snk809.jpg"),
    ("Seiko Prospex Turtle", 450.0, "seiko-turtle.jpg"),
    ("Tissot PRX Powermatic 80", 650.0, "tissot-prx.jpg"),
    ("Omega Speedmaster Professional Moonwatch", 6500.0, "omega-speedmaster.jpg"),
    ("Rolex Submariner Date", 14000.0, "rolex-submariner.jpg"),
    ("Rolex Cosmograph Daytona (steel, panda dial)", 32000.0, "rolex-daytona.jpg"),
    ("Audemars Piguet Royal Oak “Jumbo” 15202", 68000.0, "ap-royal-oak.jpg"),
    ("Patek Philippe Nautilus 5711", 120000.0, "patek-nautilus.jpg"),
    ("Vacheron Constantin Overseas", 55000.0, "vc-overseas.jpg"),
    ("Patek Philippe Calatrava", 28000.0, "patek-calatrava.jpg"),
)

# What each item cost at the start of the history, as a share of today's value.
WATCH_PAID_SHARE = 0.85
CARD_PAID_SHARE = 0.7

CARD_FALLBACK = "pokemon-collection.jpg"
CARD_SCAN_URL = "https://images.pokemontcg.io/{set}/{number}_hires.png"
_USER_AGENT = "OpenNetWorth-demo-seed/1.0"

# (name, value, Pokémon TCG set id, card number) — the set/number pins the exact printing.
POKEMON_CARDS: tuple[tuple[str, float, str, str], ...] = (
    ("Charizard – Base Set (1999, Holo)", 350.0, "base1", "4"),
    ("Blastoise – Base Set (1999, Holo)", 180.0, "base1", "2"),
    ("Venusaur – Base Set (1999, Holo)", 150.0, "base1", "15"),
    ("Pikachu – Base Set (1999)", 120.0, "base1", "58"),
    ("Mewtwo – Base Set (1999, Holo)", 90.0, "base1", "10"),
    ("Gyarados – Base Set (1999, Holo)", 75.0, "base1", "6"),
    ("Chansey – Base Set (1999, Holo)", 60.0, "base1", "3"),
    ("Alakazam – Base Set (1999, Holo)", 55.0, "base1", "1"),
    ("Zapdos – Fossil (1999, Holo)", 50.0, "base3", "15"),
    ("Articuno – Fossil (1999, Holo)", 45.0, "base3", "2"),
    ("Moltres – Fossil (1999, Holo)", 45.0, "base3", "12"),
    ("Lugia – Neo Genesis (2000, Holo)", 220.0, "neo1", "9"),
    ("Ho-Oh – Neo Revelation (2001, Holo)", 60.0, "neo3", "7"),
    ("Umbreon – Neo Discovery (2001, Holo)", 80.0, "neo2", "13"),
    ("Espeon – Neo Discovery (2001, Holo)", 70.0, "neo2", "1"),
    ("Rayquaza ex – EX Dragon (2003)", 65.0, "ex3", "97"),
    ("Gengar – EX Legend Maker (2006, Holo)", 40.0, "ex12", "5"),
    ("Dragonite δ – EX Delta Species (2005, Holo)", 50.0, "ex11", "3"),
    ("Charizard – Base Set 2 (2000, Holo)", 80.0, "base4", "4"),
    ("Mew – Wizards Black Star Promo #8", 30.0, "basep", "8"),
    ("Umbreon ★ – POP Series 5 (2007)", 900.0, "pop5", "17"),
    ("Pikachu – Wizards Black Star Promo #1", 40.0, "basep", "1"),
)


def _card_scan(set_id: str, number: str) -> bytes | None:
    from backend.core.pinned_http import pinned_urlopen

    url = CARD_SCAN_URL.format(set=set_id, number=number)
    try:
        # The CDN answers 403 to urllib's default User-Agent.
        with pinned_urlopen(url, headers={"User-Agent": _USER_AGENT}, timeout=20) as resp:
            if getattr(resp, "status", 200) != 200:
                raise OSError(f"HTTP {resp.status}")
            return resp.read()
    except Exception as exc:  # noqa: BLE001 — a missing scan must never fail the build
        _log.warning(
            "demo seed: no card scan for %s/%s (%s); using the fallback photo", set_id, number, exc
        )
        return None


def _ladder(tl, paid: float, value: float) -> list[tuple[str, float]]:
    """Half-yearly valuations climbing from the purchase price to today's value, strictly
    between the purchase date and today (``create`` writes today's own mark)."""
    ceiling = min(tl.today, date.today())
    dates = []
    step = 6
    while (d := add_months(tl.start, step)) < ceiling:
        dates.append(d)
        step += 6
    span = (ceiling - tl.start).days or 1
    return [
        (d.isoformat(), round(paid + (value - paid) * (d - tl.start).days / span, 2)) for d in dates
    ]


def seed(tl, attach_photo, *, offline: bool = False) -> list[dict]:
    """``attach_photo(asset_id, filename, content=None)`` is owned_assets' own photo writer,
    passed in so the two modules don't import each other.

    Every item is bought at the start of the book's history, so net worth carries it the whole
    way — a value that appears only today is a cliff at the chart's right edge
    (test_the_net_worth_chart_has_no_cliff_at_the_right_edge). Before its first valuation an
    asset reads its purchase price (services.owned_assets_valuation)."""
    from backend.services.owned_assets import apply_owned_asset_change, create_category

    bought = min(tl.start, date.today()).isoformat()

    def create(name: str, value: float, category: str, paid_share: float) -> dict:
        paid = round(value * paid_share, 2)
        asset = apply_owned_asset_change(
            {
                "op": "create",
                "name": name,
                "category": category,
                "value": value,
                "cost": paid,
                "cost_date": bought,
                "note": "Valuation refresh",
            }
        )
        for mark_date, mark_value in _ladder(tl, paid, value):
            apply_owned_asset_change(
                {
                    "op": "add_mark",
                    "id": asset["id"],
                    "date": mark_date,
                    "value": mark_value,
                    "note": "Valuation refresh",
                }
            )
        return asset

    assets = []
    for name, value, photo in WATCHES:
        asset = create(name, value, "watch", WATCH_PAID_SHARE)
        attach_photo(asset["id"], photo)
        assets.append(asset)

    cards = create_category("Pokemon Cards")["key"]
    for name, value, set_id, number in POKEMON_CARDS:
        asset = create(name, value, cards, CARD_PAID_SHARE)
        scan = None if offline else _card_scan(set_id, number)
        if scan:
            attach_photo(asset["id"], f"{set_id}-{number}.png", scan)
        else:
            attach_photo(asset["id"], CARD_FALLBACK)
        assets.append(asset)
    return assets
