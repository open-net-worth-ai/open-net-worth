"""PRD acceptance criterion 12 — with ``paper_trading`` off: every route 404s, the fill engine
and the daily job do nothing, and stored paper data stays exactly as it was."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.services.paper_trading import engine, qa_quotes, service
from backend.services.paper_trading.service import apply_paper_change
from tests.flags import flags

ROUTES = [
    ("get", "/api/paper/defaults"),
    ("get", "/api/paper/portfolios"),
    ("post", "/api/paper/portfolios"),
    ("get", "/api/paper/portfolios/ppf_x"),
    ("patch", "/api/paper/portfolios/ppf_x"),
    ("post", "/api/paper/portfolios/ppf_x/start-over"),
    ("delete", "/api/paper/portfolios/ppf_x"),
    ("post", "/api/paper/portfolios/ppf_x/orders/preview"),
    ("post", "/api/paper/portfolios/ppf_x/orders"),
    ("delete", "/api/paper/orders/pord_x"),
    ("get", "/api/paper/portfolios/ppf_x/activity"),
    ("get", "/api/paper/portfolios/ppf_x/performance"),
    ("get", "/api/paper/portfolios/ppf_x/compare"),
    ("post", "/api/paper/portfolios/ppf_x/marks"),
    ("get", "/api/paper/positions?instrument_id=inst_acme"),
    ("get", "/api/paper/assets/search?q=a"),
]


def test_flag_off_hides_every_route_idles_the_jobs_and_keeps_the_data(paper_db):
    from backend.api.paper_trading import router

    pid = apply_paper_change({"op": "create", "cash": 1000, "home_currency": "USD"})["portfolio"][
        "id"
    ]
    qa_quotes.set_state("closed")
    apply_paper_change(
        {
            "op": "place",
            "portfolio_id": pid,
            "order": {"asset_key": "inst:inst_acme", "side": "buy", "quantity": 1},
        }
    )
    before = (service.events(pid), service.portfolio_orders(pid))
    qa_quotes.set_state("open")
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    with flags(paper_trading=False):
        for method, path in ROUTES:
            assert getattr(client, method)(path).status_code == 404, path
        assert engine.tick() == {"skipped": "flag off"}
        assert engine.daily() == {"skipped": "flag off"}
    assert (service.events(pid), service.portfolio_orders(pid)) == before
    # Back on, the waiting order is still there and fills.
    assert engine.tick()["filled"] == 1
