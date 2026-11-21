"""Durable event observation, filtering, cooldown, and queue draining."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import stat
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Sequence

from backend.core import platform_fs
from backend.core.file_lock import lock_fd, unlock_fd
from backend.core.ids import new_id
from backend.core.paths import DATA_DIR
from backend.db.automation_run_store import AutomationRunStore
from backend.db.automation_runtime_store import AutomationRuntimeStore
from backend.db.automation_store import AutomationStore
from backend.services.automations.txn_conditions import conditions_match

from .runs import run_automation

MAX_EVENT_QUEUE = 500
MAX_SEEN_EVENT_KEYS = 2_000
_event_lock = threading.RLock()
_event_lock_state = threading.local()
logger = logging.getLogger(__name__)
_TRIGGER_RUNTIME_KEYS = frozenset(
    {
        "event_queue",
        "event_seen",
        "event_overflow",
        "event_inflight",
        "event_last_fired_at",
        "balance_observations",
        "balance_occurrences",
        "balance_deliveries_seen",
        "price_observations",
        "price_occurrences",
    }
)


@contextmanager
def trigger_state_change_guard():
    """Serialize trigger edits with event state observation across processes."""
    with _event_lock:
        depth = int(getattr(_event_lock_state, "depth", 0))
        if depth:
            _event_lock_state.depth = depth + 1
            try:
                yield
            finally:
                _event_lock_state.depth = depth
            return
        # PLAN 4C TASK 1: AutomationRuntimeStore is DB-backed now (no AUTOMATION_RUNTIME_JSON
        # file constant to derive a sibling path from) — this lock is a pure filesystem
        # primitive with no relationship to the store's OWN persistence beyond having always
        # lived alongside it in DATA_DIR, so it now anchors on DATA_DIR directly.
        lock_path = DATA_DIR / ".automation-trigger.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        flags = platform_fs.open_flags(os.O_RDWR, os.O_CREAT)
        descriptor = os.open(lock_path, flags, 0o600)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or not platform_fs.is_current_owner(metadata):
                raise OSError("Automation trigger lock is not a private regular file")
            platform_fs.fchmod(descriptor, 0o600)
            lock_fd(descriptor, exclusive=True)
            _event_lock_state.depth = 1
            try:
                yield
            finally:
                _event_lock_state.depth = 0
                unlock_fd(descriptor)
        finally:
            os.close(descriptor)


def clear_trigger_runtime_state(automation_id: str) -> None:
    """Drop state derived from the old trigger while retaining run/conversation ownership."""
    store = AutomationRuntimeStore()
    automation = AutomationStore().find_by_id(automation_id)
    if automation is None:
        store.clear_fields(automation_id, _TRIGGER_RUNTIME_KEYS)
        return
    store.ensure_trigger_revision(
        automation_id,
        _trigger_revision(automation),
        _TRIGGER_RUNTIME_KEYS,
    )


def _trigger_revision(automation: dict) -> str:
    configured = automation.get("trigger_revision")
    if isinstance(configured, str) and configured:
        return configured
    encoded = json.dumps(
        {
            "id": automation.get("id"),
            "updated_at": automation.get("updated_at"),
            "trigger": automation.get("trigger") or {},
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()[:20]


def _runtime_state_for_trigger(automation: dict) -> dict | None:
    revision = _trigger_revision(automation)
    current = AutomationStore().find_by_id(automation["id"])
    if current is None or _trigger_revision(current) != revision:
        return None
    return AutomationRuntimeStore().ensure_trigger_revision(
        automation["id"], revision, _TRIGGER_RUNTIME_KEYS
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _active_event_automations(event: str | None = None) -> list[dict]:
    rows = []
    for automation in AutomationStore().all():
        trigger = automation.get("trigger") or {}
        if automation.get("status") != "active" or trigger.get("kind") != "event":
            continue
        if (automation.get("execution") or {}).get("kind") == "transaction_transform":
            continue  # a Rule runs inside the write, never as a queued Lucius run
        if event is None or trigger.get("event") == event:
            rows.append(automation)
    return rows


def _event_key(event: str, payload: dict) -> str:
    key = payload.get("event_key") or payload.get("id")
    if key:
        return str(key)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return f"{event}:{hashlib.sha256(encoded.encode()).hexdigest()[:24]}"


def _transaction_side(payload: dict, account: object = None) -> str | None:
    if account:
        if payload.get("from_account_id") == account:
            return "from"
        if payload.get("to_account_id") == account:
            return "to"
        return None
    if payload.get("from_account_id") or payload.get("from_amount") is not None:
        return "from"
    if payload.get("to_account_id") or payload.get("to_amount") is not None:
        return "to"
    return None


def _transaction_currency(payload: dict, side: str | None) -> str:
    if side:
        return str(payload.get(f"{side}_currency") or payload.get("currency") or "").upper()
    return str(payload.get("currency") or "").upper()


def _amount(payload: dict, currency: str | None = None, *, side: str | None = None) -> float | None:
    wanted = str(currency or "").upper()
    candidates: list[tuple[object, str]] = []
    # A trigger on one account compares in that account's currency (its own leg); on any
    # account, in the base currency — the only unit every account shares.
    if not wanted and not side:
        candidates.append((payload.get("base_amount"), ""))
    if side:
        candidates.append((payload.get(f"{side}_amount"), _transaction_currency(payload, side)))
        candidates.append((payload.get("amount"), _transaction_currency(payload, side)))
    else:
        candidates.append((payload.get("amount"), _transaction_currency(payload, None)))
        if wanted:
            candidates.append(
                (payload.get("base_amount"), str(payload.get("base_currency") or "").upper())
            )
        candidates.append((payload.get("from_amount"), _transaction_currency(payload, "from")))
        candidates.append((payload.get("to_amount"), _transaction_currency(payload, "to")))
    for value, candidate_currency in candidates:
        if value is None or (wanted and candidate_currency != wanted):
            continue
        try:
            return abs(float(value))
        except (TypeError, ValueError):
            return None
    return None


def _contains_any(value: object, haystack: str) -> bool:
    if not value:
        return True
    if not isinstance(value, list):
        return False
    needles = [str(item).casefold() for item in value if str(item).strip()]
    return bool(needles) and any(needle in haystack for needle in needles)


def _regex_matches(pattern: object, value: str) -> bool:
    if not pattern:
        return True
    try:
        return re.search(str(pattern), value, re.IGNORECASE) is not None
    except re.error:
        return False


def _direction(payload: dict, account: object = None) -> str | None:
    side = _transaction_side(payload, account)
    if side == "from":
        return "debit"
    if side == "to":
        return "credit"
    return None


def _transaction_operation_matches(when: dict, payload: dict) -> bool:
    configured = when.get("operation", "create")
    operations = configured if isinstance(configured, list) else [configured]
    return str(payload.get("operation") or "create") in operations


def _is_self_originated(automation: dict, payload: dict) -> bool:
    return bool(
        payload.get("source_automation_run_id")
        and payload.get("source_automation_id") == automation.get("id")
    )


def _matches(event: str, when: dict, payload: dict) -> bool:
    if event == "txn":
        if not _transaction_operation_matches(when, payload):
            return False
        account = when.get("account") or when.get("account_id")
        side = _transaction_side(payload, account)
        if account and side is None:
            return False
        direction = when.get("direction")
        actual_direction = _direction(payload, account)
        if direction in {"out", "debit"} and actual_direction != "debit":
            return False
        if direction in {"in", "credit"} and actual_direction != "credit":
            return False
        over = when.get("over")
        minimum = when.get("min_amount")
        maximum = when.get("max_amount")
        amount = _amount(
            payload,
            str(when.get("currency") or "") or None,
            side=side if account else None,
        )
        try:
            if over not in (None, "") and (amount is None or amount <= float(over)):
                return False
            if minimum not in (None, "") and (amount is None or amount < float(minimum)):
                return False
            if maximum not in (None, "") and (amount is None or amount > float(maximum)):
                return False
        except (TypeError, ValueError):
            return False
        contains = str(when.get("contains") or "").strip().casefold()
        if contains:
            haystack = " ".join(
                str(payload.get(key) or "") for key in ("description", "memo", "payee")
            ).casefold()
            if contains not in haystack:
                return False
        description = str(payload.get("description") or payload.get("note") or "")
        lowered_description = description.casefold()
        description_contains = str(when.get("description_contains") or "").casefold()
        if description_contains and description_contains not in lowered_description:
            return False
        if not _contains_any(when.get("keywords"), lowered_description):
            return False
        if not _regex_matches(when.get("description_regex"), description):
            return False
        memo = str(payload.get("memo") or "")
        lowered_memo = memo.casefold()
        memo_contains = str(when.get("memo_contains") or "").casefold()
        if memo_contains and memo_contains not in lowered_memo:
            return False
        if not _contains_any(when.get("memo_keywords"), lowered_memo):
            return False
        if not _regex_matches(when.get("memo_regex"), memo):
            return False
        if not conditions_match(
            when.get("conditions"),
            payload,
            amount_on=lambda on: _amount(
                payload, None, side=_transaction_side(payload, on) if on else None
            ),
            match=when.get("match") or "all",
        ):
            return False
        if (
            when.get("kind")
            and str(payload.get("kind") or "").casefold() != str(when["kind"]).casefold()
        ):
            return False
        currency = str(when.get("currency") or "").upper()
        if currency and account and _transaction_currency(payload, side) != currency:
            return False
        if (
            currency
            and not account
            and currency
            not in {
                _transaction_currency(payload, None),
                _transaction_currency(payload, "from"),
                _transaction_currency(payload, "to"),
            }
        ):
            return False
        return True
    if event == "price":
        return str(payload.get("symbol") or "").upper() == str(when.get("symbol") or "").upper()
    if event == "balance":
        account = when.get("account") or when.get("account_id")
        return not account or payload.get("account_id") == account
    return False


def _enqueue(
    automation: dict,
    event: str,
    payloads: Sequence[dict],
    *,
    state_updates: dict | None = None,
) -> int:
    runtime_store = AutomationRuntimeStore()
    revision = _trigger_revision(automation)
    current = AutomationStore().find_by_id(automation["id"])
    if current is None or _trigger_revision(current) != revision:
        return 0
    runtime_store.ensure_trigger_revision(automation["id"], revision, _TRIGGER_RUNTIME_KEYS)
    queued_at = _now().isoformat()
    items = []
    for source in payloads:
        payload = dict(source)
        key = _event_key(event, payload)
        payload.setdefault("event_key", key)
        items.append(
            {
                "event_key": key,
                "event": event,
                "payload": payload,
                "queued_at": queued_at,
                "trigger_revision": revision,
            }
        )
    written, _dropped = runtime_store.enqueue_events(
        automation["id"],
        items,
        trigger_revision=revision,
        max_queue=MAX_EVENT_QUEUE,
        max_seen=MAX_SEEN_EVENT_KEYS,
        state_updates=state_updates,
    )
    return written


def publish_automation_events(
    event: str, payloads: Sequence[dict], *, _staged: bool = False
) -> int:
    """Filter and durably enqueue source events for active matching Automations."""
    delivery_id = None
    if not _staged:
        delivery_id = AutomationRuntimeStore().stage_event_delivery(
            "publish", {"event": event, "payloads": [dict(row) for row in payloads]}
        )
    try:
        written = _publish_automation_events(event, payloads)
    except Exception:
        # The staged observation stays in the outbox and is replayed by the scheduler.
        if _staged:
            raise
        return 0
    if delivery_id:
        AutomationRuntimeStore().acknowledge_event_delivery(delivery_id)
    return written


def _publish_automation_events(event: str, payloads: Sequence[dict]) -> int:
    with trigger_state_change_guard():
        written = 0
        independent: list[dict] = []
        sequence_groups: dict[str, list[dict]] = {}
        for automation in _active_event_automations(event):
            sequence = (automation.get("trigger") or {}).get("sequence")
            group = str((sequence or {}).get("group") or "")
            if group:
                sequence_groups.setdefault(group, []).append(automation)
            else:
                independent.append(automation)

        for automation in independent:
            when = (automation.get("trigger") or {}).get("when") or {}
            matched = [
                dict(payload)
                for payload in payloads
                if not _is_self_originated(automation, payload) and _matches(event, when, payload)
            ]
            if matched:
                written += _enqueue(automation, event, matched)
        for group in sorted(sequence_groups):
            blocked: set[str] = set()
            ordered = sorted(
                sequence_groups[group],
                key=lambda row: (
                    int(((row.get("trigger") or {}).get("sequence") or {}).get("priority", 100)),
                    row.get("created_at") or "",
                    row.get("id") or "",
                ),
            )
            for automation in ordered:
                when = (automation.get("trigger") or {}).get("when") or {}
                matched = [
                    dict(payload)
                    for payload in payloads
                    if _event_key(event, payload) not in blocked
                    and not _is_self_originated(automation, payload)
                    and _matches(event, when, payload)
                ]
                if matched:
                    written += _enqueue(automation, event, matched)
                    sequence = (automation.get("trigger") or {}).get("sequence") or {}
                    if sequence.get("stop"):
                        blocked.update(_event_key(event, payload) for payload in matched)
        return written


def evaluate_balance(automation: dict, state: dict, balance: float) -> dict | None:
    """Record one balance observation and return only a configured directional crossing."""
    when = (automation.get("trigger") or {}).get("when") or {}
    account_id = str(when.get("account") or when.get("account_id") or "")
    threshold = float(when.get("amount", when.get("threshold", 0)))
    direction = when.get("direction")
    observations = dict(state.get("balance_observations") or {})
    previous = observations.get(account_id)
    observations[account_id] = balance
    state["balance_observations"] = observations
    if previous is None:
        return None
    crossed = (direction == "below" and float(previous) >= threshold and balance < threshold) or (
        direction == "above" and float(previous) <= threshold and balance > threshold
    )
    if not crossed:
        return None
    occurrences = dict(state.get("balance_occurrences") or {})
    occurrence = int(occurrences.get(account_id) or 0) + 1
    occurrences[account_id] = occurrence
    state["balance_occurrences"] = occurrences
    return {
        "event_key": f"balance:{account_id}:{direction}:{threshold}:{occurrence}",
        "account_id": account_id,
        "direction": direction,
        "threshold": threshold,
        "before": float(previous),
        "after": balance,
    }


def capture_balance_snapshot() -> dict[str, float] | None:
    """Capture pre-commit balances only when an active crossing trigger needs them."""
    if not _active_event_automations("balance"):
        return None
    from backend.services.ledger import balances

    return balances(include_archived=True)


def observe_balance_events(
    *,
    before: dict[str, float] | None = None,
    current: dict[str, float] | None = None,
    _staged: bool = False,
    _delivery_id: str | None = None,
) -> int:
    from backend.services.ledger import balances

    automations = _active_event_automations("balance")
    if not automations:
        return 0
    current = dict(current) if current is not None else balances(include_archived=True)
    delivery_id = None
    if not _staged:
        delivery_id = AutomationRuntimeStore().stage_event_delivery(
            "balance", {"before": before, "current": current}
        )
    else:
        delivery_id = _delivery_id
    written = 0
    with trigger_state_change_guard():
        runtime_store = AutomationRuntimeStore()
        for automation in automations:
            state = _runtime_state_for_trigger(automation)
            if state is None:
                continue
            deliveries_seen = list(state.get("balance_deliveries_seen") or [])
            if delivery_id and delivery_id in deliveries_seen:
                continue
            when = (automation.get("trigger") or {}).get("when") or {}
            account_id = str(when.get("account") or when.get("account_id") or "")
            if account_id not in current:
                continue
            if before is not None and account_id in before:
                observations = dict(state.get("balance_observations") or {})
                observations[account_id] = float(before[account_id])
                state["balance_observations"] = observations
            payload = evaluate_balance(automation, state, float(current[account_id]))
            if delivery_id:
                deliveries_seen.append(delivery_id)
            updates = {
                "balance_observations": state["balance_observations"],
                "balance_deliveries_seen": deliveries_seen[-MAX_SEEN_EVENT_KEYS:],
            }
            if payload:
                updates["balance_occurrences"] = state["balance_occurrences"]
                written += _enqueue(
                    automation,
                    "balance",
                    [payload],
                    state_updates=updates,
                )
            else:
                runtime_store.update_event_state(automation["id"], updates)
    if delivery_id:
        AutomationRuntimeStore().acknowledge_event_delivery(delivery_id)
    return written


def automation_price_symbols() -> list[str]:
    return sorted(
        {
            _configured_price_symbol(row)
            for row in _active_event_automations("price")
            if ((row.get("trigger") or {}).get("when") or {}).get("symbol")
        }
    )


def _configured_price_symbol(automation: dict) -> str:
    configured = str(((automation.get("trigger") or {}).get("when") or {}).get("symbol") or "")
    from backend.db.instrument_store import InstrumentStore

    instrument = InstrumentStore().find_by_id(configured)
    return str((instrument or {}).get("symbol") or configured).upper()


_PERCENT_DIRECTIONS = frozenset({"move", "rise", "fall"})


def _price_crossing(
    direction: str, value: float, price: float, row: dict, prior: dict | None
) -> tuple[bool, bool] | None:
    """(active, rising edge) for one observation, or None when it cannot be evaluated — a
    percent trigger with no usable previous close is skipped, never read as a price level."""
    if direction in _PERCENT_DIRECTIONS:
        anchor = row.get("previous_close")
        if anchor in (None, 0):
            return None
        move = (price - float(anchor)) / float(anchor) * 100
        if direction == "rise":
            active = move > value
        elif direction == "fall":
            active = -move > value
        else:
            active = abs(move) > value
        same_day = bool(prior and prior.get("day") == row.get("day"))
        return active, active and not bool(same_day and prior.get("active"))
    if direction == "below":
        active = price < value
        return active, prior is not None and float(prior["price"]) >= value and active
    active = price > value
    return active, prior is not None and float(prior["price"]) <= value and active


def observe_price_events(
    observations: Sequence[dict] | None = None, *, _staged: bool = False
) -> int:
    """Observe fetched prices and enqueue threshold rising edges or daily percentage moves."""
    if not observations:
        return 0
    delivery_id = None
    if not _staged:
        delivery_id = AutomationRuntimeStore().stage_event_delivery(
            "price", [dict(row) for row in observations]
        )
    by_symbol = {str(row.get("symbol") or "").upper(): dict(row) for row in observations}
    written = 0
    with trigger_state_change_guard():
        runtime_store = AutomationRuntimeStore()
        for automation in _active_event_automations("price"):
            when = (automation.get("trigger") or {}).get("when") or {}
            symbol = _configured_price_symbol(automation)
            row = by_symbol.get(symbol)
            if row is None or row.get("price") is None:
                continue
            price = float(row["price"])
            value = float(when.get("value") or 0)
            direction = when.get("direction")
            state = _runtime_state_for_trigger(automation)
            if state is None:
                continue
            prior = (state.get("price_observations") or {}).get(symbol)
            crossing = _price_crossing(direction, value, price, row, prior)
            if crossing is None:
                continue
            active, edge = crossing
            observations_state = dict(state.get("price_observations") or {})
            observations_state[symbol] = {
                "price": price,
                "active": active,
                "day": row.get("day") or _now().date().isoformat(),
            }
            state["price_observations"] = observations_state
            if not edge:
                runtime_store.update_event_state(
                    automation["id"], {"price_observations": observations_state}
                )
                continue
            occurrence_key = f"{symbol}:{direction}:{value}"
            occurrences = dict(state.get("price_occurrences") or {})
            occurrence = int(occurrences.get(occurrence_key) or 0) + 1
            occurrences[occurrence_key] = occurrence
            payload = {
                **row,
                "symbol": symbol,
                "direction": direction,
                "threshold": value,
                "event_key": f"price:{symbol}:{direction}:{value}:{occurrence}",
            }
            written += _enqueue(
                automation,
                "price",
                [payload],
                state_updates={
                    "price_observations": observations_state,
                    "price_occurrences": occurrences,
                },
            )
    if delivery_id:
        AutomationRuntimeStore().acknowledge_event_delivery(delivery_id)
    return written


def replay_automation_event_deliveries() -> int:
    """Replay staged source observations; stable event keys make every retry idempotent."""
    store = AutomationRuntimeStore()
    replayed = 0
    for delivery in store.pending_event_deliveries():
        delivery_id = str(delivery.get("id") or "")
        kind = delivery.get("kind")
        payload = delivery.get("payload")
        try:
            if kind == "publish" and isinstance(payload, dict):
                replayed += publish_automation_events(
                    str(payload.get("event") or ""),
                    [dict(row) for row in payload.get("payloads") or []],
                    _staged=True,
                )
            elif kind == "sync":
                pass  # the retired "connector sync fails" trigger — drop a leftover delivery
            elif kind == "price" and isinstance(payload, list):
                replayed += observe_price_events(
                    [dict(row) for row in payload if isinstance(row, dict)], _staged=True
                )
            elif kind == "balance" and isinstance(payload, dict):
                raw_before = payload.get("before")
                raw_current = payload.get("current")
                replayed += observe_balance_events(
                    before=dict(raw_before) if isinstance(raw_before, dict) else None,
                    current=dict(raw_current) if isinstance(raw_current, dict) else None,
                    _staged=True,
                    _delivery_id=delivery_id,
                )
            else:
                raise ValueError(f"unknown Automation event delivery kind {kind!r}")
        except Exception:
            logger.exception("Automation event outbox replay failed: %s", delivery_id)
            continue
        store.acknowledge_event_delivery(delivery_id)
    return replayed


def _window_open(last_fired_at: str | None, limit: str, now: datetime) -> bool:
    if limit == "each" or not last_fired_at:
        return True
    try:
        previous = datetime.fromisoformat(last_fired_at)
    except (TypeError, ValueError):
        return True
    cooldown = timedelta(hours=1) if limit == "hour" else timedelta(days=1)
    return now >= previous + cooldown


def _reconcile_event_batches() -> dict[str, int]:
    runtime_store = AutomationRuntimeStore()
    run_store = AutomationRunStore()
    existing: set[tuple[str, str]] = set()
    for row in runtime_store.all():
        inflight = (row.get("state") or {}).get("event_inflight")
        if not isinstance(inflight, dict) or not inflight.get("run_id"):
            continue
        automation_id = str(row.get("automation_id") or "")
        run_id = str(inflight["run_id"])
        run = run_store.find_by_id(run_id)
        if run is not None and run.get("automation_id") == automation_id:
            existing.add((automation_id, run_id))
    return runtime_store.reconcile_event_batches(existing)


async def drain_automation_events(now: datetime | None = None) -> list[dict]:
    """Start eligible queued runs and acknowledge only after the run row exists."""
    now = now or _now()
    with trigger_state_change_guard():
        _reconcile_event_batches()
    results = []
    for automation in _active_event_automations():
        runtime_store = AutomationRuntimeStore()
        revision = _trigger_revision(automation)
        with trigger_state_change_guard():
            state = _runtime_state_for_trigger(automation)
        if state is None:
            continue
        queue = list(state.get("event_queue") or [])
        if not queue:
            continue
        limit = (automation.get("trigger") or {}).get("limit") or "each"
        if not _window_open(state.get("event_last_fired_at"), limit, now):
            continue
        selected = queue[:1] if limit == "each" else queue
        event_ids = [item["event_key"] for item in selected]
        payloads = [dict(item["payload"]) for item in selected]
        payload = {
            "event": (automation.get("trigger") or {}).get("event"),
            "event_ids": event_ids,
            "events": payloads,
        }
        with trigger_state_change_guard():
            current = AutomationStore().find_by_id(automation["id"])
            if (
                current is None
                or current.get("status") != "active"
                or _trigger_revision(current) != revision
            ):
                if current is not None:
                    _runtime_state_for_trigger(current)
                continue
            run_id = new_id("run")
            if not runtime_store.claim_event_batch(
                automation["id"], run_id, event_ids, now.isoformat(), revision
            ):
                continue
            try:
                run = await run_automation(
                    automation["id"],
                    trigger={"kind": "event", "event": payload["event"]},
                    payload=payload,
                    run_id=run_id,
                )
            except Exception as exc:
                if AutomationRunStore().find_by_id(run_id) is None:
                    runtime_store.restore_event_batch(automation["id"], run_id)
                else:
                    runtime_store.complete_event_batch(automation["id"], run_id)
                results.append({"id": automation["id"], "event_ids": event_ids, "error": str(exc)})
                continue
            runtime_store.complete_event_batch(automation["id"], run_id)
        results.append({"id": automation["id"], "run_id": run["id"], "event_ids": event_ids})
    return results
