"""Owned assets: the Apartment, a car, a watch, and one disposed item — through
``apply_owned_asset_change`` (backend/services/owned_assets.py), the ONE write choke, so
this is a book the app could actually have produced.

Property (and every other owned asset) is a thing you own valued by a dated mark, not a
ledger balance moved by adjustment rows (ADR 2026-09-07-seven-account-types-one-axis).

``_periodic_marks`` builds a useful, anchor-relative valuation history so the chart has
enough points to demonstrate the feature regardless of the day the demo is rebuilt.

**A second ceiling — REAL wall-clock, not the fictional anchor.** ``_timeline.py``'s own
rule is "no generator reads ``date.today()`` itself", so every fictional date in this book
is derived from ``tl``/the anchor alone. But ``services.owned_assets``'s ``add_mark`` and
``dispose`` ops both call ``_parse_date(..., reject_future=True)``, which rejects any date
after REAL ``date.today()`` — a genuine product invariant for a real user's mark/disposal
(you cannot record an observation of the future), and not something to carve a demo
exception into. ``docs/DEMO.md`` documents ``--as-of`` as a supported way to pin the
anchor, including to a date AHEAD of today — and when it is, clamping to ``tl.today``
alone still produces a date the real choke refuses. Every computed date in this file is
therefore clamped to ``min(tl.today, date.today())`` — the fictional anchor when it has
already happened, real "now" when the anchor is still ahead of it — via ``_real_ceiling``.
A same-day-or-past anchor (the normal case) is unaffected: ``tl.today`` already IS
``date.today()`` or earlier.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from backend.scripts.demo import asset_collections
from backend.scripts.demo._timeline import Timeline, add_months

# Real photos, so the register and each asset page look like a real book — credits and
# licences in photos/CREDITS.md.
_PHOTOS = Path(__file__).parent / "photos"


def _real_ceiling(tl: Timeline) -> date:
    """The latest date any WRITE this generator makes may legally carry. See the module
    docstring's "second ceiling" note — the write choke's reject-future check tests
    against real wall-clock ``date.today()``, which no ``--as-of`` anchor can move."""
    return min(tl.today, date.today())


def _periodic_marks(
    tl: Timeline, interval_months: int, start_value: float, annual_rate: float
) -> list[tuple[str, float]]:
    """Value observations at the requested interval, indexed backward from the anchor.
    ``annual_rate`` is a yearly
    multiplier (``1.036`` == +3.6%/yr, ``0.90`` == -10%/yr); each mark compounds the
    fraction of a year its interval actually covers, so re-tuning the interval never
    silently changes the book's overall growth/depreciation story.

    ``idxs`` runs oldest month to newest, so once a month's own date has run past
    ``ceiling`` (an ``--as-of`` anchor pinned well ahead of real today), EVERY later —
    i.e. even more future — index clamps to that same ``ceiling`` too: several
    consecutive iterations would otherwise append the identical date with different
    values, which both looks wrong in the asset's history and collides with
    ``_create_with_marks``'s own real-``today()`` mark. Collapsing them into one entry
    (keeping the latest-computed value — the one that has compounded the most) is the
    fix; skipping the append entirely would leave the OLDER of the two values on file
    for that date instead of the fresher one."""
    interval = max(1, interval_months)
    months = tl.months
    idxs = sorted(range(len(months) - 1, -1, -interval))
    period_rate = annual_rate ** (interval / 12)
    ceiling = _real_ceiling(tl)
    value = start_value
    ladder: list[tuple[str, float]] = []
    for idx in idxs:
        d_iso = min(months[idx].replace(day=28), ceiling).isoformat()
        value = round(value * period_rate, -2)
        if ladder and ladder[-1][0] == d_iso:
            ladder[-1] = (d_iso, value)
        else:
            ladder.append((d_iso, value))
    return ladder


def _create_with_marks(spec: dict, ladder: list[tuple[str, float]], *, note: str) -> dict:
    """``note`` (singular) is the MARK's own text — distinct from ``spec["notes"]``, the
    asset's free-text description. A prior version of this generator passed ``source``
    here, which neither ``create`` nor ``add_mark`` ever reads (both moved to ``note``
    before this file existed) — every seeded mark silently carried an empty/placeholder
    note instead of the intended text.

    ``create``'s own auto-mark ALWAYS stamps real ``date.today()`` (services.owned_assets
    ``_today()`` — the module docstring's "second ceiling" note), never the fictional
    anchor. When the ladder's own newest entry lands on that exact same real day too —
    the normal case, since ``tl.today == date.today()`` on a same-day/future-pinned build
    — writing it again via ``add_mark`` duplicated the row verbatim (same date, value AND
    note). A past-pinned (``--as-of``) anchor's newest entry lands on the FICTIONAL anchor
    instead, which never collides with `create`'s real-today mark, so the full ladder is
    still written in that case — dropping it unconditionally would have re-opened the
    freshness-at-anchor bug this file exists to fix."""
    from backend.services.owned_assets import apply_owned_asset_change

    asset = apply_owned_asset_change({"op": "create", **spec, "value": ladder[-1][1], "note": note})
    today_iso = date.today().isoformat()
    for i, (mark_date, mark_value) in enumerate(ladder):
        if i == len(ladder) - 1 and mark_date == today_iso:
            continue  # `create` already wrote this exact (date, value, note) above
        apply_owned_asset_change(
            {
                "op": "add_mark",
                "id": asset["id"],
                "date": mark_date,
                "value": mark_value,
                "note": note,
            }
        )
    return asset


def _attach_doc(asset_id: str, *, name: str, filename: str, text: str) -> None:
    """A real ``AttachmentStore`` row (``services.attachments.create_attachment`` — the
    module's own docstring: low-level, "used by tests and by anything that already has a
    parsed entry in hand"), then ``add_doc`` through the asset choke — exactly the shape
    the API route builds from a real upload (``api/assets_api.py``)."""
    from backend.services.attachments import create_attachment
    from backend.services.owned_assets import apply_owned_asset_change

    ref_id = create_attachment(
        {"kind": "text", "summary": f"Text file {filename!r}", "format": "txt"},
        content=text.encode("utf-8"),
        filename=filename,
        media_type="text/plain",
        source_channel="assets:docs",
    )
    apply_owned_asset_change({"op": "add_doc", "id": asset_id, "ref_id": ref_id, "name": name})


def _attach_photo(asset_id: str, filename: str, content: bytes | None = None) -> None:
    """A file from photos/, or ``content`` already in hand (a fetched card scan)."""
    from backend.services.attachments import create_attachment
    from backend.services.owned_assets import apply_owned_asset_change

    fmt = "png" if filename.endswith(".png") else "jpeg"
    ref_id = create_attachment(
        {"kind": "image", "summary": "Asset photo", "format": fmt},
        content=content if content is not None else (_PHOTOS / filename).read_bytes(),
        filename=filename,
        media_type=f"image/{fmt}",
        source_channel="assets:photo",
    )
    apply_owned_asset_change({"op": "set_photo", "id": asset_id, "ref": ref_id})


def seed(tl: Timeline, A: dict, *, offline: bool = False) -> list[dict]:
    """Every owned asset the book carries — property, a vehicle, a watch, one disposed item,
    and the collections (asset_collections.py) — spanning four builtin categories and a custom
    one, and exercising a doc, a photo on each, insurance, storage details, and a disposal."""
    from backend.services.owned_assets import apply_owned_asset_change

    assets: list[dict] = []

    # Apartment — property. LOAN_TYPES has collapsed to {"loan"} — A["mortgage"]
    # is that type, so the apartment links its backing loan account
    # (services.owned_assets_rules.validate_loan_account_id).
    apartment = _create_with_marks(
        {
            "name": "Apartment",
            "category": "property",
            "cost": 498000.0,
            "cost_date": tl.start.isoformat(),
            "notes": "Two-bed on Pearl Street. Valuation refreshed quarterly.",
            "loan_account_id": A["mortgage"],
        },
        _periodic_marks(tl, 3, 498000.0, 1.036),
        note="Valuation refresh",
    )
    _attach_photo(apartment["id"], "apartment.jpg")
    assets.append(apartment)

    # Vehicle — outright-owned, insured, and carries a supporting document.
    vehicle = _create_with_marks(
        {
            "name": "2022 Toyota RAV4 Hybrid",
            "category": "vehicle",
            "cost": 34500.0,
            "cost_date": tl.start.isoformat(),
            "notes": "Alex's daily driver. Blue Book check twice a year.",
            "insured": True,
        },
        _periodic_marks(tl, 6, 34500.0, 0.90),
        note="Valuation refresh",
    )
    _attach_doc(
        vehicle["id"],
        name="Bill of sale",
        filename="rav4-bill-of-sale.txt",
        text=(
            "BILL OF SALE\n"
            "2022 Toyota RAV4 Hybrid XLE\n"
            f"Purchase date: {tl.start.isoformat()}\n"
            "Purchase price: $34,500.00\n"
            "Buyer: Alex Morgan\n"
        ),
    )
    _attach_photo(vehicle["id"], "rav4.jpg")
    assets.append(vehicle)

    # Watch
    watch = _create_with_marks(
        {
            "name": "Tudor Black Bay 58",
            "category": "watch",
            "cost": 5200.0,
            "cost_date": tl.start.isoformat(),
            "notes": "A 30th-birthday gift. Checked against resale listings twice a year.",
        },
        _periodic_marks(tl, 12, 5200.0, 1.015),
        note="Valuation refresh",
    )
    _attach_photo(watch["id"], "watch.jpg")
    assets.append(watch)

    # A small disposed item — the only place in the book that exercises `dispose`
    # (archived state, proceeds landing as a real ledger transaction).
    bike = apply_owned_asset_change(
        {
            "op": "create",
            "name": "Mountain E-Bike",
            "category": "other",
            "value": 1600.0,
            "cost": 1600.0,
            "cost_date": tl.start.isoformat(),
            "notes": "Traded in for cash once Alex started cycling less.",
        }
    )
    _attach_photo(bike["id"], "ebike.jpg")
    sold_on = min(tl.clamp(add_months(tl.today, -14)) or tl.start, _real_ceiling(tl))
    disposed = apply_owned_asset_change(
        {
            "op": "dispose",
            "id": bike["id"],
            "proceeds": 650.0,
            "account_id": A["checking"],
            "date": sold_on.isoformat(),
        }
    )
    assets.append(disposed)

    assets += asset_collections.seed(tl, _attach_photo, offline=offline)
    return assets
