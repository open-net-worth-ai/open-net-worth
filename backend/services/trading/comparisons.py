"""Only comparable scenarios can be ranked together."""

from backend.core.errors import ConflictError
from backend.core.ids import new_id
from backend.db.trading_store import TradingStore, trading_transaction
from backend.models.trading import ComparisonCreate
from backend.services.trading.common import collection, now, validated
from backend.services.trading.performance import calculate_performance


def create_comparison(data):
    clean = validated(ComparisonCreate, data)
    if len(set(clean["run_ids"])) != len(clean["run_ids"]):
        raise ConflictError("A comparison needs distinct participant runs")
    with trading_transaction() as conn:
        runs = [TradingStore("runs").required(run_id, conn=conn) for run_id in clean["run_ids"]]
        if len({run["scenario_hash"] for run in runs}) != 1:
            raise ConflictError("Runs have different capital, data, execution mode or period")
        row = {
            **clean,
            "id": new_id("tcmp"),
            "scope_id": "",
            "created_at": now(),
            "scenario_hash": runs[0]["scenario_hash"],
        }
        TradingStore("comparisons").insert(row, conn=conn)
    return get_comparison(row["id"])


def get_comparison(comparison_id):
    row = TradingStore("comparisons").required(comparison_id)
    results = []
    historical_clocks = set()
    for run_id in row["run_ids"]:
        run = TradingStore("runs").required(run_id)
        if run["mode"] == "backtest":
            historical_clocks.add(run["current_time"])
        results.append(
            {
                "run_id": run_id,
                "workspace_id": run["workspace_id"],
                "agent_label": run["workspace_snapshot"]["agent_label"],
                "agent_kind": run["workspace_snapshot"]["agent_kind"],
                "agent_version": run["agent_version"],
                "state": run["state"],
                "mode": run["mode"],
                "start_at": run["start_at"],
                "end_at": run["end_at"],
                "current_time": run["current_time"],
                **calculate_performance(run),
            }
        )
    complete = all(result["valuation_complete"] for result in results)
    reasons = [] if complete else ["One or more valuations are incomplete"]
    if len(historical_clocks) > 1:
        complete = False
        reasons.append("Historical runs are at different environment times")
    return {
        **row,
        "scenario": comparison_scenario(TradingStore("runs").required(row["run_ids"][0])),
        "comparable": complete,
        "reasons": reasons,
        "results": sorted(results, key=lambda result: -(result["return_pct"] or 0))
        if complete
        else results,
        "limitations": [
            "Historical model-knowledge contamination is not eliminated",
            "External participant information access requires independent verification",
        ],
    }


def comparison_scenario(run):
    mandate = run["workspace_snapshot"]["mandate"]
    instruments = mandate.get("instruments") or [row["instrument"] for row in run["observations"]]
    universe = {
        tuple(item[k] for k in ("venue", "asset_class", "symbol", "currency")): item
        for item in instruments
    }
    return {
        "mode": run["mode"],
        "start_at": run["start_at"],
        "end_at": run["end_at"],
        "period": {"start_at": run["start_at"], "end_at": run["end_at"]},
        "capital_by_currency": run["initial_capital"],
        "base_currency": run["base_currency"],
        "data_version": run["data_version"],
        "market_data_source": mandate["market_data_source"],
        "instruments": list(universe.values())[:200],
        "instrument_count": len(universe),
        "fee_bps": mandate["fee_bps"],
        "slippage_bps": mandate["slippage_bps"],
        "max_participation": mandate["max_participation"],
    }


def list_comparisons(*, limit=100, offset=0):
    return collection(
        [get_comparison(row["id"]) for row in TradingStore("comparisons").all()],
        limit=limit,
        offset=offset,
    )
