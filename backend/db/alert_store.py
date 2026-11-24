"""AlertStore — standalone, ticker-keyed price alerts (ADR 0014).

A flat list, one row per condition, independent of watchlist membership. Each row:

    {id, ticker, instrument_id?, op, value, note, label, name, kind, channels, asset_class,
     on_trigger, cooldown_minutes, enabled, last_fired_at, created_at}

Decoupled from watchlist items so removing a ticker from a watchlist never destroys
its alerts, and setting an alert never forces the ticker into the "My holdings" list.
Rising-edge ("armed") state is NOT stored here — it stays runtime-only in the
monitoring price cache keyed by ``alert_cache_key()`` (ticker, "{op}{value}" for a price
alert, "{op}{value}%" for a move alert), so a checkpoint restore of the ``alerts`` table can
never silently re-arm or silence an alert. ``value`` is coerced to float on every write so
that cache key string stays stable across the migration.

``name`` is the alert's OWN short display name (e.g. "Stop-loss zone"), distinct from
``label`` (the ticker's display name). ``kind`` selects what ``value`` means: ``"price"``
(unchanged — a price level) or ``"move"`` (a percent threshold on the one-day move vs. the
previous close). ``channels`` is the alert's OWN external fan-out (channel ids, e.g.
``["telegram", "email"]``; ``[]`` = in-app only) — saved on the alert, never derived from any
other setting (ADR 2026-09-30-notification-channels-per-kind-and-per-alert). Which ids EXIST
is a services concern (``services.notify_routes.normalize_channels``); this store only keeps
the list well-formed.

PLAN 4D TASK 2: DB-backed (Tier-1, book-scoped, table ``alerts``) via
``BookPayloadTableStore`` — spec:139-140 lists alerts explicitly as Tier-1.
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.core.errors import ValidationError
from backend.core.ids import new_id

# Import order is LOAD-BEARING (backend.db.tables <-> backend.db.payload_store is a genuine
# two-way reference — see tables.py's own docstring on its late `payload_table`/
# `book_payload_table` import): whichever of the two a fresh process touches FIRST must
# resolve `tables` before `payload_store`'s own `from backend.db.tables import ...` runs, or
# the circular import deadlocks on a not-yet-defined name. `alert_store` is the literal first
# submodule `backend/db/__init__.py` imports (alphabetically first among its re-exports), so
# THIS file is the one that must anchor the safe order; every other store in this package is
# imported later, once both modules are already fully resolved, and follows the ordinary
# ruff-sorted (payload_store-before-tables) convention instead. noqa: I001 — isort would
# otherwise alphabetize this back to the order that deadlocks.
from backend.db.tables import alerts
from backend.db.payload_store import BookPayloadTableStore

ALERT_OPS = (">=", "<=", ">", "<")
ALERT_ON_TRIGGER = ("cooldown", "disable")
ALERT_KINDS = ("price", "move")
DEFAULT_COOLDOWN_MINUTES = 1440
# Generous but not unbounded — guards against a typo/garbage value wedging an alert silent
# for an implausible span. The UI's own presets top out at 24h (1440).
_MAX_COOLDOWN_MINUTES = 10080  # 7 days
_NAME_MAX_LEN = 64
# A move alert's value is a PERCENT threshold on the prior close, not a price — bounded to a
# plausible single-day range so a typo (e.g. a price level typed into a move alert) fails
# loud at the choke instead of silently never firing (>100%) or firing on noise (<=0%).
_MAX_MOVE_PCT = 100.0


def norm_ticker(ticker: str) -> str:
    t = (ticker or "").strip().upper()
    if not t:
        raise ValidationError("alert ticker must be non-empty")
    return t


def norm_value(value: object) -> float:
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"alert value must be a number, got {value!r}") from exc
    if v != v or v in (float("inf"), float("-inf")):  # NaN / inf
        raise ValidationError("alert value must be finite")
    return v


def norm_op(op: str) -> str:
    if op not in ALERT_OPS:
        raise ValidationError(f"alert op must be one of {ALERT_OPS}, got {op!r}")
    return op


def norm_kind(kind: str) -> str:
    if kind not in ALERT_KINDS:
        raise ValidationError(f"alert kind must be one of {ALERT_KINDS}, got {kind!r}")
    return kind


def norm_channels(channels: object) -> list[str]:
    if not isinstance(channels, list) or not all(isinstance(c, str) for c in channels):
        raise ValidationError(f"alert channels must be a list of channel ids, got {channels!r}")
    out: list[str] = []
    for c in (c.strip() for c in channels):
        if c and c != "in_app" and c not in out:
            out.append(c)
    return out


def norm_name(name: object) -> str:
    n = str(name or "").strip()
    if len(n) > _NAME_MAX_LEN:
        raise ValidationError(f"alert name must be at most {_NAME_MAX_LEN} characters, got {n!r}")
    return n


def _norm_value_for_kind(value: object, kind: str) -> float:
    """``norm_value`` plus the ``"move"`` kind's own bound: a percent threshold on the prior
    close must be > 0 and <= 100. A ``"price"`` alert keeps the unbounded semantics."""
    v = norm_value(value)
    if kind == "move" and not (0 < v <= _MAX_MOVE_PCT):
        raise ValidationError(f"move alert value must be > 0 and <= {_MAX_MOVE_PCT}, got {v!r}")
    return v


def alert_cache_key(condition: dict) -> str:
    """Rising-edge armed-state key for one alert condition (data-only, no I/O).

    Unchanged for ``kind="price"`` (``f"{op}{value}"``) — an alert armed before this
    redesign keeps its armed state and does not spuriously re-fire. A move alert gets a
    distinct ``"%"``-suffixed key so a ``>=5`` price alert and a ``>=5%`` move alert on the
    same ticker never share armed state."""
    op = condition.get("op", "")
    value = condition.get("value")
    suffix = "%" if condition.get("kind") == "move" else ""
    return f"{op}{value}{suffix}"


def _resolve_instrument_id_by_ticker(ticker: str) -> str:
    """Best-effort READ-ONLY ticker -> instrument_id lookup — no network call, no instrument
    creation. Mirrors the local-lookup half of ``services.instruments.resolve_by_ticker``
    (exact ``price_source_id`` match first, then a bare symbol ONLY when it is unambiguous)
    without that function's find-or-create tail, which a data-access write path must never
    trigger. Empty string when nothing resolves, including a same-symbol collision across
    asset classes (e.g. an equity ticker and a coin both called "BTC") — guessing there would
    silently point identity at the wrong asset."""
    from backend.db.instrument_store import InstrumentStore

    store = InstrumentStore()
    inst = store.find_by_price_source_id(ticker)
    if inst:
        return inst.get("id") or ""
    matches = store.find_all_by_symbol(ticker)
    return matches[0].get("id", "") if len(matches) == 1 else ""


def norm_on_trigger(on_trigger: str) -> str:
    if on_trigger not in ALERT_ON_TRIGGER:
        raise ValidationError(
            f"alert on_trigger must be one of {ALERT_ON_TRIGGER}, got {on_trigger!r}"
        )
    return on_trigger


def norm_cooldown_minutes(value: object) -> int:
    """Reject bool explicitly — bool is an int subclass in Python and would otherwise pass a
    bare ``isinstance(value, int)`` check while meaning nothing here."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"alert cooldown_minutes must be a whole number, got {value!r}")
    if not (0 <= value <= _MAX_COOLDOWN_MINUTES):
        raise ValidationError(
            f"alert cooldown_minutes must be between 0 and {_MAX_COOLDOWN_MINUTES}, got {value!r}"
        )
    return value


class AlertStore(BookPayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(alerts, id_field="id", engine=engine)

    # ── Writes ────────────────────────────────────────────────────────────────

    def create(self, alert: dict) -> dict:
        """Validate + insert one alert. Stamps id and created_at; uppercases ticker;
        coerces value to float (keeps the monitor cache key stable).

        ``on_trigger``/``cooldown_minutes`` are the user's choice of what happens after this
        alert fires — go quiet for a while and re-arm, or turn off until manually re-enabled
        (owner request, 2026-08-25: an explicit alert could re-fire on every poll tick while a
        price bounced across its threshold during trading hours). ``enabled``/``last_fired_at``
        are runtime bookkeeping the monitor writes back through this same choke point when an
        alert fires — never user-set at create time.

        ``instrument_id`` is backfilled from ``ticker`` when the caller omits it — neither the
        REST route nor the AI tool (``set_price_alerts``) has ever supplied one, which is why
        this field was "mostly unpopulated on write" (identity-migration finding, 2026-08-30):
        nothing upstream resolved it and this store never tried either. A watched-but-not-held
        alert (ADR 0014 — no position row exists for it) has no other source of a stable id, so
        without this backfill ``price_monitor._run_alert_lane`` fell back to a raw ticker string
        for that whole class of alert, every time. A caller-supplied id still wins."""
        ticker = norm_ticker(alert.get("ticker", ""))
        instrument_id = alert.get("instrument_id", "") or _resolve_instrument_id_by_ticker(ticker)
        # Presence-only defaulting (not a falsy/None check): an OMITTED field defaults, but
        # an EXPLICIT None is a caller bug and must still hit norm_*'s validation, not
        # silently fall back — same convention as on_trigger/cooldown_minutes below.
        kind = norm_kind(alert["kind"] if "kind" in alert else "price")
        row = {
            "id": new_id("alrt"),
            "ticker": ticker,
            "instrument_id": instrument_id,
            "op": norm_op(alert.get("op", "")),
            "value": _norm_value_for_kind(alert.get("value"), kind),
            "kind": kind,
            "channels": norm_channels(alert["channels"] if "channels" in alert else []),
            "note": alert.get("note", "") or "",
            "label": alert.get("label", "") or "",
            "name": norm_name(alert.get("name", "")),
            "asset_class": alert.get("asset_class", "") or "equity",
            "on_trigger": norm_on_trigger(
                alert["on_trigger"] if "on_trigger" in alert else "cooldown"
            ),
            "cooldown_minutes": norm_cooldown_minutes(
                alert["cooldown_minutes"]
                if "cooldown_minutes" in alert
                else DEFAULT_COOLDOWN_MINUTES
            ),
            "enabled": True,
            "last_fired_at": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        return self.insert(row)

    def update(self, alert_id: str, changes: dict) -> dict | None:
        """Patch a whitelist of fields on one alert, re-validating op/value/on_trigger/cooldown.

        ``enabled``/``last_fired_at`` are in the whitelist so the monitor can write them back
        (re-arm bookkeeping and disable-after-fire) through this same choke point — not because
        a user PATCH is expected to set them, though the frontend does use ``enabled: true`` as
        the explicit "turn this alert back on" action.

        ``value``'s bounds depend on ``kind`` (a move alert's value is a percent, capped),
        so changing EITHER re-validates ``value`` against the *effective* (possibly just-changed)
        kind — a value that was fine as a price and stays unchanged is invalid the moment the
        alert flips to ``"move"``."""
        allowed = {
            "op",
            "value",
            "note",
            "label",
            "name",
            "asset_class",
            "kind",
            "channels",
            "on_trigger",
            "cooldown_minutes",
            "enabled",
            "last_fired_at",
        }
        patch = {k: v for k, v in changes.items() if k in allowed and v is not None}
        if "op" in patch:
            patch["op"] = norm_op(patch["op"])
        if "kind" in patch:
            patch["kind"] = norm_kind(patch["kind"])
        if "channels" in patch:
            patch["channels"] = norm_channels(patch["channels"])
        if "name" in patch:
            patch["name"] = norm_name(patch["name"])
        if "value" in patch or "kind" in patch:
            existing = self.find_by_id(alert_id)
            if existing is not None:
                effective_kind = patch.get("kind", existing.get("kind", "price"))
                effective_value = patch.get("value", existing.get("value"))
                patch["value"] = _norm_value_for_kind(effective_value, effective_kind)
        if "on_trigger" in patch:
            patch["on_trigger"] = norm_on_trigger(patch["on_trigger"])
        if "cooldown_minutes" in patch:
            patch["cooldown_minutes"] = norm_cooldown_minutes(patch["cooldown_minutes"])
        if "enabled" in patch:
            patch["enabled"] = bool(patch["enabled"])
        if not patch:
            return self.find_by_id(alert_id)
        return self.update_by_id(alert_id, patch)

    # ── Domain queries ────────────────────────────────────────────────────────

    def for_ticker(self, ticker: str) -> list[dict]:
        return self.query(ticker=norm_ticker(ticker))

    def by_ticker(self) -> dict[str, list[dict]]:
        """Group every alert by UPPER ticker — the shape the monitor alert lane wants
        (mirrors monitoring._collect_crash_items keyed-by-ticker)."""
        grouped: dict[str, list[dict]] = {}
        for row in self.all():
            grouped.setdefault(row.get("ticker", "").upper(), []).append(row)
        return grouped
