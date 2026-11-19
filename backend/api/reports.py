"""
Report library read API (P4-RP1).

A THIN router over ``services.reports`` — it validates the report-type + period enums and
shapes the response; every figure is composed in the service from the existing read services
(net worth, returns, realized P&L, income, spending), never here. Respects the dependency
rule: imports only the service (api -> services), no store / no math directly.

A report is computed-not-stored — a pure read, no write choke point.

Routes:
    GET /api/reports                       — the catalog (available report types)
    GET /api/reports/{type}?period=&format= — a generated report

``format`` (or the ``Accept`` header) selects the body: ``html`` (default — a self-contained
printable document) or ``json`` (the structured report model). When the HTML is served as a
downloadable file (``download=1``), it mirrors the export route's CSRF guard (Bearer token OR
``X-Requested-With: XMLHttpRequest``) — a report can carry the full financial picture, so a
top-level cross-site navigation that triggers a download must be blocked.

An unknown report type → 404; a bad period → coerced to the default by the service (defensive).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from backend.api._deps import _bearer_token
from backend.services import reports as reports_service

router = APIRouter()

# Registered before the dynamic route below so the literal path wins (same reason
# /api/accounts/tracking-health sits above /api/accounts/{account_id}).


@router.get("/api/reports/rollup")
def get_reports_rollup(
    months: int = 6,
    merchant_limit: int = 10,
    period: Optional[str] = None,
    tag: Optional[list[str]] = Query(None),
    category: Optional[list[str]] = Query(None),
):
    """Trailing-window income/expense: per-month category rows and totals, the top-N + "Other"
    stacked series, the category table's current/average/total columns, top merchants and the
    savings rate. `period` (this_month, last_month, 3m, 6m, 12m, ytd) sets the window and adds
    the whole period's totals; `tag` (repeatable) keeps only transactions carrying one, `category` (repeatable ids) only
    those categories."""
    from backend.core.errors import ValidationError
    from backend.services import reports_rollup as reports_rollup_service

    try:
        return reports_rollup_service.reports_aggregates(
            months=months,
            merchant_limit=merchant_limit,
            period=period,
            tags=tag,
            categories=category,
        )
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/reports")
def list_report_types() -> dict:
    """The report catalog: available report types with title/description/period-applicability."""
    return {"reports": reports_service.list_reports()}


def _wants_json(request: Request, fmt: Optional[str]) -> bool:
    if fmt:
        return fmt.lower() == "json"
    return "application/json" in request.headers.get("accept", "").lower()


@router.get("/api/reports/{report_type}")
def get_report(
    report_type: str,
    request: Request,
    period: str = "1Y",
    format: Optional[str] = None,
    download: bool = False,
) -> Response:
    """Generate one report. JSON (the structured model) or HTML (a printable document).

    ``download=1`` returns the HTML as a file attachment and enforces the export-style CSRF
    guard (a same-site ``SameSite=Lax`` cookie rides a top-level cross-site GET, and a report
    exfiltrates financial data — so a download requires a Bearer token or an ``X-Requested-With``
    header set by the SPA's fetch, which a plain navigation cannot set).
    """
    try:
        report = reports_service.build_report(report_type, period)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc

    if _wants_json(request, format):
        return JSONResponse(report)

    if download:
        has_bearer = _bearer_token(request) is not None
        requested_with = request.headers.get("x-requested-with", "").strip().lower()
        if not has_bearer and requested_with != "xmlhttprequest":
            raise HTTPException(403, "missing X-Requested-With header")

    html_doc = reports_service.render_html(report)
    headers = {}
    if download:
        stamp = (report.get("generated_at") or "").replace(":", "-")
        suffix = f"-{report['period']}" if report.get("period") else ""
        filename = f"{report_type}{suffix}-{stamp}.html"
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return HTMLResponse(content=html_doc, headers=headers)
