"""What one host round saw of the model's stream, and its usage event (claude_sdk_live).

The CLI reports cost once, for the whole query, but the host bills per round. A round's tokens come
from the stream's ``message_start`` / ``message_delta`` usage; the cost of a round that ends on tool
calls is a price-table estimate (``cost_reported: False``), and the query's last round carries what
the CLI reports for the query minus those estimates, so the turn's sum is the CLI's own figure.
"""

from __future__ import annotations

import time
from typing import Optional

from claude_agent_sdk import ResultMessage

from backend.ai.models.pricing import cost_usd_for_model
from backend.core.errors import ExternalServiceError


class Round:
    """What one host round has seen of the model's stream."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.started_calls = 0
        self.ended_on_tools = False
        self.requests: list[dict] = []
        self.ttft_ms: Optional[float] = None
        self.t0 = time.monotonic()
        self.delta_seen = False

    def request(self) -> dict:
        return self.requests[-1] if self.requests else {}


def round_usage(
    rnd: Round,
    *,
    model: str,
    resolved_model: str,
    api_key_source: str,
    result: Optional[ResultMessage],
    estimated_so_far: float,
) -> tuple[dict, float]:
    """This round's normalized ``usage`` event, and the running total of estimated cost."""
    from backend.ai.models.claude_cli_provider import _max_prompt, _usage_event

    total = {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    total.update(output_tokens=0, ephemeral_5m=0, ephemeral_1h=0)
    peak: Optional[int] = None
    for usage in rnd.requests:
        for field in total:
            total[field] += int(usage.get(field) or 0)
        created = usage.get("cache_creation") or {}
        total["ephemeral_5m"] += int(created.get("ephemeral_5m_input_tokens") or 0)
        total["ephemeral_1h"] += int(created.get("ephemeral_1h_input_tokens") or 0)
        prompt = sum(
            int(usage.get(k) or 0)
            for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        )
        peak = _max_prompt(peak, prompt)
    envelope: dict = {
        "usage": {
            "input_tokens": total["input_tokens"],
            "cache_creation_input_tokens": total["cache_creation_input_tokens"],
            "cache_read_input_tokens": total["cache_read_input_tokens"],
            "output_tokens": total["output_tokens"],
            "cache_creation": {
                "ephemeral_5m_input_tokens": total["ephemeral_5m"],
                "ephemeral_1h_input_tokens": total["ephemeral_1h"],
            },
        },
        "model": model,
    }
    if result is not None:
        envelope["modelUsage"] = result.model_usage
        envelope["duration_ms"] = result.duration_ms
    event = _usage_event(envelope, model, rnd.ttft_ms, rnd.t0, api_key_source, peak)
    event["duration_ms"] = (time.monotonic() - rnd.t0) * 1000.0
    if result is not None and result.total_cost_usd is not None:
        event["cost_usd"] = max(0.0, float(result.total_cost_usd) - estimated_so_far)
        event["cost_reported"] = True
        return event, estimated_so_far
    estimate = _estimate(resolved_model or model, event)
    event["cost_usd"] = estimate
    event["cost_reported"] = False
    return event, estimated_so_far + estimate


def _estimate(model: str, event: dict) -> float:
    try:
        return float(
            cost_usd_for_model(
                model,
                event["input_tokens"],
                event["output_tokens"],
                event["cache_creation_tokens"],
                event["cache_read_tokens"],
                cache_creation_5m_tokens=event["cache_creation_5m_tokens"],
                cache_creation_1h_tokens=event["cache_creation_1h_tokens"],
            )
        )
    except Exception:  # noqa: BLE001 — an unpriced model estimates 0; the query's total settles it
        return 0.0


def result_failure(result: ResultMessage) -> ExternalServiceError:
    from backend.ai.models.claude_cli_provider import _cli_failure, _result_error

    return _cli_failure(_result_error({"subtype": result.subtype, "result": result.result}))
