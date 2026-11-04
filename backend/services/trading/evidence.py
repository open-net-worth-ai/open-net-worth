"""Identity-linked trading evidence; absent decision/data relationships remain absent."""

from backend.core.errors import NotFoundError, ValidationError
from backend.db.trading_store import TradingStore
from backend.services.trading.common import instant
from backend.services.trading.market import available_observations, observation_record


def _owned(kind, record_id, run_id):
    row = TradingStore(kind).required(record_id)
    if row.get("run_id") != run_id:
        raise NotFoundError("Trading evidence not found")
    return row


def get_evidence(run_id, kind, record_id):
    if kind not in {"order", "fill", "event"}:
        raise ValidationError("Evidence kind must be order, fill or event")
    run = TradingStore("runs").required(run_id)
    selected = _owned(f"{kind}s", record_id, run_id)
    order = selected if kind == "order" else None
    if selected.get("order_id"):
        order = _owned("orders", selected["order_id"], run_id)
    if kind == "event" and selected["payload"].get("order_id"):
        order = _owned("orders", selected["payload"]["order_id"], run_id)
    events = TradingStore("events").list_scope(run_id)
    decision_id = order.get("decision_id") if order else None
    decision = next((row for row in events if row["id"] == decision_id), None)
    if kind == "event" and selected["kind"] == "agent_decision":
        decision = selected
    orders = (
        [
            row
            for row in TradingStore("orders").list_scope(run_id)
            if row.get("decision_id") == decision["id"]
        ]
        if decision
        else [order]
        if order
        else []
    )
    order = order or (orders[0] if orders else None)
    order_ids = {row["id"] for row in orders}
    linked = _linked_events(events, order_ids, decision, selected)
    fills = [
        row for row in TradingStore("fills").list_scope(run_id) if row["order_id"] in order_ids
    ]
    data = _decision_data(run, decision)
    missing = []
    if not decision:
        missing.append("Decision link not recorded")
    if not data["recorded"]:
        missing.append("Data available at this decision was not recorded")
    if data.get("missing_observation_ids"):
        missing.append(
            "Some recorded data references are no longer present in the retained market series"
        )
    files = [artifact for row in linked for artifact in row["payload"].get("artifacts", [])]
    return {
        "selection": {"kind": kind, "id": record_id, "record": selected},
        "order": order,
        "orders": orders,
        "fills": fills,
        "decision": decision,
        "events": linked,
        "observations": data.pop("observations"),
        "data_context": data,
        "files": files,
        "missing_links": missing,
    }


def _linked_events(events, order_ids, decision, selected):
    turn_id = decision["payload"].get("turn_id") if decision else None
    return [
        row
        for row in events
        if row["id"] == selected["id"]
        or (decision and row["id"] == decision["id"])
        or row["payload"].get("order_id") in order_ids
        or (turn_id and row["payload"].get("turn_id") == turn_id)
    ]


def _decision_data(run, decision):
    context = decision["payload"].get("data_context") if decision else None
    if not context or not context.get("cutoff"):
        return {
            "recorded": False,
            "label": "Data context not recorded",
            "cutoff": None,
            "data_version": run["data_version"],
            "observations": [],
        }
    cutoff = instant(context["cutoff"])
    rows = [
        observation_record(row, run["data_version"])
        for row in available_observations(run)
        if instant(row["timestamp"]) <= cutoff
        and instant(row.get("available_at") or row["timestamp"]) <= cutoff
    ]
    recorded_ids = context.get("observation_ids")
    if recorded_ids is not None:
        rows = [row for row in rows if row["id"] in recorded_ids]
    retained = {row["id"] for row in rows}
    return {
        **context,
        "recorded": True,
        "label": "Data available at this decision",
        "observations": rows[-500:],
        "total": len(rows),
        "missing_observation_ids": [
            record_id for record_id in recorded_ids or [] if record_id not in retained
        ],
        "usage": "Recorded availability context; exact consumption is shown by tool inputs/results.",
    }
