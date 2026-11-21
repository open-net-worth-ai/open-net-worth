"""One request answers a whole screen's reads: named SOURCES, queried by id, computed once.

A Dashboard load used to fire ~15 heavy GETs at once. Each rebuilt the same foundation (the whole
transaction table, the holdings context, the price maps) and all of them fought over two vCPUs and
the GIL, so every widget waited for the whole burst (~7 s on the owner's server, 2026-11-21).

`GET /api/query` takes a list of queries, Grafana-style — ``{refId, source, params}`` — and this
module answers them one after another in ONE thread inside ONE `core.request_memo`, so the shared
raw reads happen once and nothing competes. Results stream back per `refId` as each is ready.

A source is the SAME service call its own GET route makes (one definition per fact): the batch is
a cheaper way to ask, never a second computation of an answer. Adding a source = one entry in
`SOURCES` + its client spelling in `frontend/src/hooks/serverState/dataQuery.ts`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from backend.core import request_memo
from backend.core.errors import OpenNetWorthError, ValidationError, to_http_status

_log = logging.getLogger(__name__)

MAX_QUERIES = 40
_MAX_REF_ID = 200


@dataclass(frozen=True)
class Source:
    """`run(params)` computes the answer; `params` maps each accepted name to its parser."""

    run: Callable[[dict], Any]
    params: dict[str, Callable[[Any], Any]]


def _int_in(lo: int, hi: int) -> Callable[[Any], int]:
    def parse(v: Any) -> int:
        if isinstance(v, bool) or not isinstance(v, (int, str)):
            raise ValueError("expected an integer")
        n = int(v)
        if not lo <= n <= hi:
            raise ValueError(f"expected {lo}–{hi}")
        return n

    return parse


def _int_clamped(lo: int, hi: int) -> Callable[[Any], int]:
    """An integer pulled into lo–hi, never refused — for a route that clamps rather than 422s."""
    whole = _int_in(-(2**31), 2**31)

    def parse(v: Any) -> int:
        return max(lo, min(whole(v), hi))

    return parse


def _float(v: Any) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        raise ValueError("expected a number")
    return float(v)


def _month(v: Any) -> str:
    if not isinstance(v, str) or len(v) != 7 or v[4] != "-":
        raise ValueError("expected YYYY-MM")
    return v


# ── The sources: each is exactly what its GET route returns ─────────────────────────────


def _net_worth(p: dict) -> Any:
    from backend.services import ledger

    return ledger.net_worth()


def _net_worth_stats(p: dict) -> Any:
    from backend.services import networth_stats

    return networth_stats.networth_stats()


def _portfolio_analytics(p: dict) -> Any:
    from backend.services import portfolio_analytics

    return portfolio_analytics.portfolio_analytics(None, p.get("guideline_pct"))


def _commitments_summary(p: dict) -> Any:
    from backend.services import commitments_summary

    return commitments_summary.commitments_overview(**({"days": p["days"]} if "days" in p else {}))


def _commitments_coverage(p: dict) -> Any:
    from backend.services import commitments

    kwargs: dict = {"floor": p.get("floor")}
    if "months" in p:
        kwargs["months"] = p["months"]
    return commitments.coverage(**kwargs)


def _planning_summary(p: dict) -> Any:
    from backend.services import planning_summary

    return planning_summary.budget_aggregates(p.get("ym"))


def _investments_brief(p: dict) -> Any:
    from backend.services import portfolio_brief

    return portfolio_brief.compute_brief()


SOURCES: dict[str, Source] = {
    # GET /api/net-worth
    "net_worth": Source(_net_worth, {}),
    # GET /api/net-worth/stats
    "net_worth_stats": Source(_net_worth_stats, {}),
    # Not GET /api/portfolio: its live prices wait on Yahoo/CoinGecko, and a batch answers in
    # order, so one network wait would hold up every widget after it.
    # GET /api/portfolio/analytics?guideline_pct=
    "portfolio_analytics": Source(_portfolio_analytics, {"guideline_pct": _float}),
    # GET /api/commitments/summary?days=
    "commitments_summary": Source(_commitments_summary, {"days": _int_in(1, 3650)}),
    # GET /api/commitments/coverage?months=&floor= — months CLAMPED to 1–60 like the route,
    # never refused: "All" on Commitments asks for as many months as the longest commitment.
    "commitments_coverage": Source(
        _commitments_coverage, {"months": _int_clamped(1, 60), "floor": _float}
    ),
    # GET /api/planning/summary?ym=
    "planning_summary": Source(_planning_summary, {"ym": _month}),
    # GET /api/investments/brief
    "investments_brief": Source(_investments_brief, {}),
}


# ── Running a batch ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Query:
    ref_id: str
    source: str
    params: dict

    @property
    def identity(self) -> str:
        return self.source + json.dumps(self.params, sort_keys=True)


def parse_queries(raw: Any) -> list[Query]:
    """Validate a batch: every query names a known source with only its known parameters.
    Raises `ValidationError` naming the first bad query — a batch is refused whole, never run
    half-understood."""
    if not isinstance(raw, list) or not raw:
        raise ValidationError("queries must be a non-empty list")
    if len(raw) > MAX_QUERIES:
        raise ValidationError(f"at most {MAX_QUERIES} queries per request")
    out: list[Query] = []
    seen: set[str] = set()
    for i, q in enumerate(raw):
        if not isinstance(q, dict):
            raise ValidationError(f"query {i} must be an object")
        ref_id, name, params = q.get("refId"), q.get("source"), q.get("params") or {}
        if not isinstance(ref_id, str) or not ref_id or len(ref_id) > _MAX_REF_ID:
            raise ValidationError(f"query {i}: refId must be a non-empty string")
        if ref_id in seen:
            raise ValidationError(f"query {i}: refId {ref_id!r} is used twice")
        seen.add(ref_id)
        source = SOURCES.get(name) if isinstance(name, str) else None
        if source is None:
            raise ValidationError(f"query {ref_id!r}: unknown source {name!r}")
        if not isinstance(params, dict):
            raise ValidationError(f"query {ref_id!r}: params must be an object")
        parsed: dict = {}
        for key, value in params.items():
            parser = source.params.get(key)
            if parser is None:
                raise ValidationError(f"query {ref_id!r}: {name} takes no parameter {key!r}")
            if value is None:
                continue
            try:
                parsed[key] = parser(value)
            except (TypeError, ValueError) as exc:
                raise ValidationError(f"query {ref_id!r}: {key} — {exc}") from exc
        out.append(Query(ref_id, name, parsed))
    return out


@dataclass(frozen=True)
class Result:
    ref_id: str
    data: Any = None
    status: int = 200
    error: str | None = None


def _compute(query: Query) -> tuple[Any, int, str | None]:
    try:
        return SOURCES[query.source].run(query.params), 200, None
    except OpenNetWorthError as exc:
        return None, to_http_status(exc), exc.message or str(exc)
    except ValueError as exc:
        return None, 422, str(exc)
    except Exception:
        _log.exception("data query %s failed", query.source)
        return None, 500, "Internal error"


def run(queries: list[Query]) -> Iterator[Result]:
    """Answer each query in order, inside one request memo; identical queries compute once.

    A generator, so a streamed response can send each answer as soon as it exists. The scope is
    re-entered around each compute rather than held across `yield`: a streamed body's every step
    may run in a different thread-pool context."""
    scope = request_memo.RequestMemo()
    done: dict[str, tuple[Any, int, str | None]] = {}
    for query in queries:
        key = query.identity
        if key not in done:
            with request_memo.use(scope):
                done[key] = _compute(query)
        data, status, error = done[key]
        yield Result(query.ref_id, data, status, error)
    _log.debug(
        "data query: %d queries, scope hits=%d misses=%d", len(queries), scope.hits, scope.misses
    )
