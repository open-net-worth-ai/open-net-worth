"""
API tests for the Assets router (backend/api/assets_api.py — ADR
2026-09-06-assets-tab-faithful-port).

Offline + isolated: a minimal FastAPI app mounts only the assets router.
OwnedAssetsStore is DB-backed — db_isolated gives each test its own fresh, migrated
database. Attachment storage is redirected to tmp_path (mirrors tests/test_upload_api.py).
No LLM, no network.
"""

from __future__ import annotations

import io

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.assets_api import router
from backend.db.account_store import AccountStore
from backend.services.attachment_parse import SUPPORTED_UPLOADS

_PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-but-fine-for-this-route"


@pytest.fixture(autouse=True)
def _isolated_dirs(tmp_path, monkeypatch, db_isolated):
    import backend.db.attachment_store as att_mod
    from backend.services import attachments as attachments_svc

    att_dir = tmp_path / "attachments"
    att_dir.mkdir()
    monkeypatch.setattr(att_mod, "ATTACHMENTS_DIR", att_dir)
    attachments_svc._cache_drop()
    yield
    attachments_svc._cache_drop()


@pytest.fixture
def client():
    from fastapi.exceptions import RequestValidationError

    from backend.main import log_validation_error

    app = FastAPI()
    app.include_router(router)
    # A NaN/Infinity payload against a `Field(allow_inf_nan=False)` model is correctly
    # REJECTED by pydantic, but FastAPI's stock RequestValidationError handler echoes the
    # raw offending float back in the error body — which Starlette's JSONResponse then
    # refuses to encode (`json.dumps(..., allow_nan=False)`), turning the 422 into an
    # unhandled 500. `backend.main` registers the fix (`log_validation_error` sanitizes
    # the echoed value); mount the SAME handler here so this router-only test app sees
    # the same behavior a request against the real app would (H1 regression).
    app.add_exception_handler(RequestValidationError, log_validation_error)
    return TestClient(app)


def _create(client, **overrides):
    body = {"name": "Rolex Submariner", "category": "watch", "value": 14800}
    body.update(overrides)
    r = client.post("/api/assets", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ── GET /api/assets ──────────────────────────────────────────────────────────────────────


def test_get_assets_page_shape_when_empty(client):
    r = client.get("/api/assets")
    assert r.status_code == 200
    body = r.json()
    assert body["assets"] == []
    assert {c["key"] for c in body["categories"]} >= {"property", "vehicle", "other"}
    assert body["loans"] == []
    assert body["cash_accounts"] == []
    assert body["base_currency"] == "SGD"
    assert "own" in body["summary"] and "classes" in body["summary"]


def test_create_then_list_excludes_archived_by_default(client):
    asset = _create(client)
    r = client.get("/api/assets")
    assert [a["id"] for a in r.json()["assets"]] == [asset["id"]]

    cash = AccountStore().create({"name": "DBS", "type": "cash", "kind": "asset"})
    client.post(
        f"/api/assets/{asset['id']}/dispose", json={"proceeds": 0, "account_id": cash["id"]}
    )
    assert client.get("/api/assets").json()["assets"] == []
    assert len(client.get("/api/assets?include_archived=1").json()["assets"]) == 1


# ── create / update / delete ─────────────────────────────────────────────────────────────


def test_create_asset_full_lifecycle(client):
    asset = _create(client, cost=9000, cost_date="2019-08-01")
    assert asset["value"] == 14800
    assert any(m["value"] == 9000 for m in asset["marks"])

    r = client.patch(f"/api/assets/{asset['id']}", json={"changes": {"notes": "serviced"}})
    assert r.status_code == 200
    assert r.json()["notes"] == "serviced"

    r = client.delete(f"/api/assets/{asset['id']}")
    assert r.status_code == 200
    assert client.get("/api/assets?include_archived=1").json()["assets"] == []


def test_create_asset_typed_details_and_no_review_contract(client):
    """Catches the HTTP surface retaining cadence or dropping typed detail fields."""
    asset = _create(
        client,
        insured=True,
        maturity_date="2031-06-17",
        cost_date="2020-04-19",
    )
    assert asset["insured"] is True
    assert asset["maturity_date"] == "2031-06-17"
    assert asset["cost_date"] == "2020-04-19"
    assert {"cadence_months", "automation_id", "months_old", "automation"}.isdisjoint(asset)


def test_create_price_without_date_has_no_purchase_point(client):
    """A price with no purchase date writes no today-dated purchase point: the only point in
    the series is the valuation, and it is the value."""
    asset = _create(client, value=15000, cost=10000)
    assert asset["value"] == 15000
    assert [m["kind"] for m in asset["marks"]] == ["valuation"]


def test_create_unknown_category_is_422(client):
    r = client.post("/api/assets", json={"name": "x", "category": "nope", "value": 1})
    assert r.status_code == 422


def test_create_asset_rejects_retired_review_fields(client):
    body = {
        "name": "Watch",
        "category": "watch",
        "value": 100,
        "cadence_months": 12,
    }
    assert client.post("/api/assets", json=body).status_code == 422


def test_update_asset_rejects_retired_review_fields(client):
    asset = _create(client)
    response = client.patch(f"/api/assets/{asset['id']}", json={"changes": {"automate": True}})
    assert response.status_code == 422


# ── GET /api/assets/{asset_id} ───────────────────────────────────────────────────────────


def test_get_one_asset_route(client):
    asset = _create(client)
    r = client.get(f"/api/assets/{asset['id']}")
    assert r.status_code == 200
    assert r.json()["id"] == asset["id"]
    assert r.json()["value"] == 14800


def test_get_one_asset_missing_is_404(client):
    r = client.get("/api/assets/oa_nope")
    assert r.status_code == 404


def test_update_missing_asset_is_404(client):
    r = client.patch("/api/assets/oa_nope", json={"changes": {"name": "x"}})
    assert r.status_code == 404


def test_delete_missing_asset_is_404(client):
    r = client.delete("/api/assets/oa_nope")
    assert r.status_code == 404


# ── marks ────────────────────────────────────────────────────────────────────────────────


def test_add_mark_route(client):
    asset = _create(client)
    r = client.post(f"/api/assets/{asset['id']}/marks", json={"value": 15900, "note": "Chrono24"})
    assert r.status_code == 200
    body = r.json()
    assert body["value"] == 15900
    assert len(body["marks"]) == 2


def test_add_mark_missing_asset_is_404(client):
    r = client.post("/api/assets/oa_nope/marks", json={"value": 1})
    assert r.status_code == 404


def test_add_mark_non_positive_value_is_422(client):
    asset = _create(client)
    r = client.post(f"/api/assets/{asset['id']}/marks", json={"value": 0})
    assert r.status_code == 422


def test_patch_mark_route(client):
    asset = _create(client)
    mark_id = asset["marks"][0]["id"]
    r = client.patch(
        f"/api/assets/{asset['id']}/marks/{mark_id}", json={"value": 15100, "note": "typo"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["value"] == 15100
    assert (body["marks"][0]["id"], body["marks"][0]["note"]) == (mark_id, "typo")
    assert body["marks"][0]["edited_at"]

    r = client.patch(f"/api/assets/{asset['id']}/marks/{mark_id}", json={})
    assert r.status_code == 422  # a correction changes at least one field
    r = client.patch(f"/api/assets/{asset['id']}/marks/mk_gone", json={"value": 1})
    assert r.status_code == 404
    assert r.json() == {"detail": "That value no longer exists.", "code": "not_found"}


def test_patch_mark_rejects_kind_key(client):
    asset = _create(client)
    mark_id = asset["marks"][0]["id"]
    for body in ({"kind": "purchase"}, {"value": 1, "kind": "valuation"}):
        r = client.patch(f"/api/assets/{asset['id']}/marks/{mark_id}", json=body)
        assert r.status_code == 422, body
    assert client.get(f"/api/assets/{asset['id']}").json()["marks"][0]["edited_at"] is None


def test_delete_mark_route(client):
    asset = _create(client)
    added = client.post(f"/api/assets/{asset['id']}/marks", json={"value": 15900}).json()
    newest = next(m["id"] for m in added["marks"] if m["id"] != asset["marks"][0]["id"])
    r = client.delete(f"/api/assets/{asset['id']}/marks/{newest}")
    assert r.status_code == 200
    assert (r.json()["value"], len(r.json()["marks"])) == (14800, 1)

    r = client.delete(f"/api/assets/{asset['id']}/marks/{asset['marks'][0]['id']}")
    assert r.status_code == 409
    assert r.json() == {
        "detail": "An asset needs at least one value. Record a new value first, or delete the asset.",
        "code": "conflict",
        "reason": "last_value",
    }


def test_delete_purchase_point_is_409(client):
    asset = _create(client, cost=9000, cost_date="2019-08-01")
    r = client.delete(f"/api/assets/{asset['id']}/marks/purchase")
    assert r.status_code == 409
    assert r.json() == {
        "detail": (
            "The purchase point comes from the purchase price and date — change or clear "
            "them in Purchase."
        ),
        "code": "conflict",
        "reason": "purchase_point",
    }


# ── dispose ──────────────────────────────────────────────────────────────────────────────


def test_dispose_route(client):
    asset = _create(client)
    cash = AccountStore().create({"name": "DBS", "type": "cash", "kind": "asset"})
    r = client.post(
        f"/api/assets/{asset['id']}/dispose",
        json={"proceeds": 12000, "account_id": cash["id"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["archived"] is True
    assert body["disposal_proceeds"] == 12000


def test_dispose_non_cash_account_is_422(client):
    asset = _create(client)
    brokerage = AccountStore().create({"name": "IBKR", "type": "investment", "kind": "asset"})
    r = client.post(
        f"/api/assets/{asset['id']}/dispose",
        json={"proceeds": 100, "account_id": brokerage["id"]},
    )
    assert r.status_code == 422


# ── photo / docs ─────────────────────────────────────────────────────────────────────────


def test_photo_upload_route(client):
    asset = _create(client)
    r = client.post(
        f"/api/assets/{asset['id']}/photo",
        files={"file": ("photo.png", io.BytesIO(_PNG_BYTES), "image/png")},
    )
    assert r.status_code == 200
    assert r.json()["photo_ref"]


def test_photo_remove_route_clears_it_and_deletes_the_file(client):
    """Owner 2026-09-28: tapping the photo now opens it; its viewer offers "Remove photo" —
    the same set_photo write with no ref, which also deletes the file it held."""
    from backend.services.attachments import existing_attachment_ids

    asset = _create(client)
    ref = client.post(
        f"/api/assets/{asset['id']}/photo",
        files={"file": ("photo.png", io.BytesIO(_PNG_BYTES), "image/png")},
    ).json()["photo_ref"]
    r = client.delete(f"/api/assets/{asset['id']}/photo")
    assert r.status_code == 200
    assert r.json()["photo_ref"] is None
    assert existing_attachment_ids({ref}) == set()
    assert client.delete("/api/assets/oa_nope00000000/photo").status_code == 404


def test_photo_upload_missing_asset_is_404(client):
    r = client.post(
        "/api/assets/oa_nope/photo",
        files={"file": ("photo.png", io.BytesIO(_PNG_BYTES), "image/png")},
    )
    assert r.status_code == 404


def test_doc_upload_and_patch_and_remove(client):
    asset = _create(client)
    r = client.post(
        f"/api/assets/{asset['id']}/docs",
        files={"file": ("statement.txt", io.BytesIO(b"Annual statement 2026"), "text/plain")},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["docs"]) == 1
    ref_id = body["docs"][0]["ref_id"]
    assert body["docs"][0]["name"] == "statement.txt"

    r = client.patch(
        f"/api/assets/{asset['id']}/docs/{ref_id}", json={"changes": {"lucius_readable": False}}
    )
    assert r.status_code == 200
    assert r.json()["docs"][0]["lucius_readable"] is False

    r = client.delete(f"/api/assets/{asset['id']}/docs/{ref_id}")
    assert r.status_code == 200
    assert r.json()["docs"] == []


def test_doc_patch_missing_ref_is_404(client):
    asset = _create(client)
    r = client.patch(f"/api/assets/{asset['id']}/docs/att_nope", json={"changes": {"name": "x"}})
    assert r.status_code == 404


def test_oversize_photo_upload_is_413(client):
    from backend.api.assets_api import _MAX_UPLOAD_BYTES

    asset = _create(client)
    oversize = io.BytesIO(b"x" * (_MAX_UPLOAD_BYTES + 1))
    r = client.post(
        f"/api/assets/{asset['id']}/photo",
        files={"file": ("big.png", oversize, "image/png")},
    )
    assert r.status_code == 413


# ── categories ───────────────────────────────────────────────────────────────────────────


def test_create_category_route(client):
    r = client.post("/api/assets/categories", json={"label": "Vinyl & hi-fi"})
    assert r.status_code == 200
    cat = r.json()
    assert cat["custom"] is True
    keys = [c["key"] for c in client.get("/api/assets").json()["categories"]]
    assert keys[-1] == "other"
    assert cat["key"] in keys


def test_create_category_empty_label_is_422(client):
    r = client.post("/api/assets/categories", json={"label": "   "})
    assert r.status_code == 422


def test_create_category_label_over_limit_is_422(client):
    r = client.post("/api/assets/categories", json={"label": "x" * 61})
    assert r.status_code == 422


# ── H1: NaN / Infinity are rejected on the wire, not just at the choke ─────────────────────


def _raw_json(client, path, body: str):
    return client.post(path, content=body, headers={"Content-Type": "application/json"})


def test_create_asset_value_nan_is_422(client):
    r = _raw_json(client, "/api/assets", '{"name": "Bad", "category": "other", "value": NaN}')
    assert r.status_code == 422


def test_create_asset_cost_infinity_is_422(client):
    r = _raw_json(
        client,
        "/api/assets",
        '{"name": "Bad", "category": "other", "value": 100, "cost": Infinity}',
    )
    assert r.status_code == 422


def test_create_asset_value_1e400_is_422(client):
    r = _raw_json(client, "/api/assets", '{"name": "Bad", "category": "other", "value": 1e400}')
    assert r.status_code == 422


def test_add_mark_value_infinite_is_422(client):
    asset = _create(client)
    r = _raw_json(client, f"/api/assets/{asset['id']}/marks", '{"value": Infinity}')
    assert r.status_code == 422


def test_dispose_proceeds_nan_is_422(client):
    asset = _create(client)
    cash = AccountStore().create({"name": "DBS", "type": "cash", "kind": "asset"})
    r = _raw_json(
        client,
        f"/api/assets/{asset['id']}/dispose",
        f'{{"proceeds": NaN, "account_id": "{cash["id"]}"}}',
    )
    assert r.status_code == 422


# ── M3: length bounds on the wire ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "field,limit",
    [("name", 120), ("description", 300), ("notes", 4000), ("share_note", 200)],
)
def test_create_asset_field_over_limit_is_422(client, field, limit):
    body = {"name": "Fine", "category": "other", "value": 100}
    body[field] = "x" * (limit + 1)
    r = client.post("/api/assets", json=body)
    assert r.status_code == 422


def test_create_asset_fact_field_over_limit_is_422(client):
    body = {
        "name": "Fine",
        "category": "other",
        "value": 100,
        "facts": [["Label", "x" * 121]],
    }
    r = client.post("/api/assets", json=body)
    assert r.status_code == 422


def test_create_asset_too_many_facts_is_422(client):
    body = {
        "name": "Fine",
        "category": "other",
        "value": 100,
        "facts": [[f"k{i}", "v"] for i in range(41)],
    }
    r = client.post("/api/assets", json=body)
    assert r.status_code == 422


def test_add_mark_note_over_limit_is_422(client):
    asset = _create(client)
    r = client.post(f"/api/assets/{asset['id']}/marks", json={"value": 100, "note": "x" * 301})
    assert r.status_code == 422


# ── M2: dates on the wire ────────────────────────────────────────────────────────────────


def test_create_asset_cost_date_non_iso_is_422(client):
    body = {"name": "Fine", "category": "other", "value": 100, "cost": 50, "cost_date": "yesterday"}
    r = client.post("/api/assets", json=body)
    assert r.status_code == 422


def test_create_asset_cost_date_invalid_calendar_date_is_422(client):
    body = {
        "name": "Fine",
        "category": "other",
        "value": 100,
        "cost": 50,
        "cost_date": "2019-13-01",
    }
    r = client.post("/api/assets", json=body)
    assert r.status_code == 422


def test_create_asset_cost_date_year_month_is_422(client):
    body = {"name": "Watch", "category": "watch", "value": 100, "cost_date": "2019-03"}
    assert client.post("/api/assets", json=body).status_code == 422


def test_add_mark_future_date_is_422(client):
    from datetime import date, timedelta

    asset = _create(client)
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    r = client.post(f"/api/assets/{asset['id']}/marks", json={"value": 100, "date": tomorrow})
    assert r.status_code == 422


# ── M1: currency guard on the wire ──────────────────────────────────────────────────────────


def test_dispose_foreign_currency_account_is_422(client):
    asset = _create(client)
    usd = AccountStore().create(
        {"name": "Chase USD", "type": "cash", "kind": "asset", "currency": "USD"}
    )
    r = client.post(
        f"/api/assets/{asset['id']}/dispose",
        json={"proceeds": 100, "account_id": usd["id"]},
    )
    assert r.status_code == 422
    assert usd["id"] not in [a["id"] for a in client.get("/api/assets").json()["cash_accounts"]]


# ── L1: uploads never orphan an attachment on a missing asset ──────────────────────────────


def test_photo_upload_missing_asset_creates_no_attachment(client):
    from backend.services.attachments import list_attachments

    r = client.post(
        "/api/assets/oa_nope/photo",
        files={"file": ("photo.png", io.BytesIO(_PNG_BYTES), "image/png")},
    )
    assert r.status_code == 404
    assert list_attachments() == []


def test_doc_upload_missing_asset_creates_no_attachment(client):
    from backend.services.attachments import list_attachments

    r = client.post(
        "/api/assets/oa_nope/docs",
        files={"file": ("statement.txt", io.BytesIO(b"hello"), "text/plain")},
    )
    assert r.status_code == 404
    assert list_attachments() == []


# ── L2: /photo requires an actual image ─────────────────────────────────────────────────────


def test_photo_upload_non_image_is_415(client):
    from backend.services.attachments import list_attachments

    asset = _create(client)
    r = client.post(
        f"/api/assets/{asset['id']}/photo",
        files={"file": ("statement.csv", io.BytesIO(b"a,b,c\n1,2,3"), "text/csv")},
    )
    assert r.status_code == 415
    # Rejected — and cleaned up, not left as an orphaned attachment row.
    assert list_attachments() == []


# ── The error contract: {detail, code, field?, reason?, holder?} on every /api/assets route ──


def test_domain_error_body_carries_detail_code_field(client):
    asset = _create(client, cost=9000, cost_date="2019-08-01")

    r = client.patch(f"/api/assets/{asset['id']}", json={"changes": {"share_pct": 150}})
    assert r.status_code == 422
    assert r.json() == {
        "detail": "Your share must be between 0 and 100%.",
        "code": "validation",
        "field": "share_pct",
    }

    r = client.get("/api/assets/oa_nope")
    assert r.status_code == 404
    assert r.json() == {"detail": "That asset ('oa_nope') no longer exists.", "code": "not_found"}

    r = client.post(f"/api/assets/{asset['id']}/marks", json={"value": 100, "date": "2019-07-31"})
    assert r.status_code == 409
    assert r.json() == {
        "detail": "A value can't be dated before the purchase (1 Aug 2019).",
        "code": "conflict",
        "reason": "before_purchase",
        "field": "date",
    }


def test_domain_error_body_never_leaks_other_context(client, monkeypatch):
    from backend.core.errors import ConflictError
    from backend.services import owned_assets as owned_assets_service

    def _refuse(change):
        raise ConflictError(
            "That clashes.", reason="x", field="name", secret="leak", existing_id="txn_1"
        )

    monkeypatch.setattr(owned_assets_service, "apply_owned_asset_change", _refuse)
    r = client.post("/api/assets", json={"name": "A", "category": "watch", "value": 1})
    assert r.status_code == 409
    assert r.json() == {
        "detail": "That clashes.",
        "code": "conflict",
        "reason": "x",
        "field": "name",
    }


def test_patch_non_numeric_cost_is_422_not_500(client):
    asset = _create(client)
    r = client.patch(f"/api/assets/{asset['id']}", json={"changes": {"cost": "abc"}})
    assert r.status_code == 422
    assert r.json()["field"] == "cost"
    assert r.json()["code"] == "validation"


def test_patch_unknown_key_is_422_naming_it(client):
    asset = _create(client)
    r = client.patch(f"/api/assets/{asset['id']}", json={"changes": {"value": 1}})
    assert r.status_code == 422
    assert r.json() == {
        "detail": "The value can't be edited here — record a new value instead.",
        "code": "validation",
        "field": "value",
    }


def test_conflict_body_carries_reason_and_holder(client):
    loan = AccountStore().create(
        {"name": "Lark Mortgage", "type": "loan", "kind": "liability", "currency": "SGD"}
    )
    home = _create(client, name="88 Marina Rise", category="property", loan_account_id=loan["id"])
    body = {"name": "Holiday flat", "category": "property", "value": 500_000}
    r = client.post("/api/assets", json={**body, "loan_account_id": loan["id"]})
    assert r.status_code == 409
    assert r.json() == {
        "detail": (
            "Lark Mortgage already secures “88 Marina Rise”. A loan can secure one asset — "
            "unlink it there first."
        ),
        "code": "conflict",
        "reason": "loan_in_use",
        "field": "loan_account_id",
        "holder": {"id": home["id"], "name": "88 Marina Rise"},
    }


def test_get_assets_page_has_composition_share_value_and_holder(client):
    loan = AccountStore().create(
        {"name": "Lark Mortgage", "type": "loan", "kind": "liability", "currency": "SGD"}
    )
    home = _create(
        client,
        name="88 Marina Rise",
        category="property",
        value=900_000,
        share_pct=50,
        loan_account_id=loan["id"],
    )
    body = client.get("/api/assets").json()
    asset = body["assets"][0]
    assert (asset["value"], asset["share_value"]) == (900_000, 450_000)
    assert body["loans"][0]["held_by"] == {"id": home["id"], "name": "88 Marina Rise"}
    assert body["summary"]["composition"]["rows"] == [
        {"key": "property", "kind": "category", "value": 450_000.0, "pct": 100}
    ]


# ── Uploads: readable refusals, and no file left behind ──────────────────────────────────

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _upload_doc(client, asset_id, name, content, media_type):
    return client.post(
        f"/api/assets/{asset_id}/docs", files={"file": (name, io.BytesIO(content), media_type)}
    )


def test_remove_doc_route_deletes_the_file(client):
    from backend.services.attachments import list_attachments

    asset = _create(client)
    body = _upload_doc(client, asset["id"], "statement.txt", b"Annual statement", "text/plain")
    ref_id = body.json()["docs"][0]["ref_id"]
    assert [a["id"] for a in list_attachments()] == [ref_id]

    r = client.delete(f"/api/assets/{asset['id']}/docs/{ref_id}")
    assert r.status_code == 200
    assert r.json()["docs"] == []
    assert list_attachments() == []


def test_doc_upload_password_protected_is_readable(client, monkeypatch):
    """The parser's internal `password_required: …` text used to reach the page verbatim."""
    from backend.services import office_crypt
    from backend.services.attachments import list_attachments

    class _Encrypted:
        def __init__(self, _file):
            pass

        def is_encrypted(self):
            return True

    monkeypatch.setattr(office_crypt.msoffcrypto, "OfficeFile", _Encrypted)
    asset = _create(client)
    r = _upload_doc(client, asset["id"], "statement.xlsx", b"PK-not-really", _XLSX)
    assert r.status_code == 422
    assert r.json() == {
        "detail": "That file is password-protected — remove the password and upload it again.",
        "code": "password_protected",
        "field": "file",
    }
    assert list_attachments() == []


def test_an_unreadable_doc_upload_is_a_plain_sentence(client):
    """Security review S5: a file the parser can't read was refused with the reader's own
    text (openpyxl, xlrd, PyMuPDF), and a password-protected PDF was a 500."""
    from backend.services.attachments import list_attachments
    from tests._pdf import blank_pdf

    asset = _create(client)
    locked = _upload_doc(
        client, asset["id"], "deed.pdf", blank_pdf(user_password="secret"), "application/pdf"
    )
    assert (locked.status_code, locked.json()) == (
        422,
        {
            "detail": "That file is password-protected — remove the password and upload it again.",
            "code": "password_protected",
            "field": "file",
        },
    )
    for name, content, media_type in (
        ("deed.pdf", b"%PDF-1.4 not really", "application/pdf"),
        ("rates.xlsx", b"PK-not-a-zip", _XLSX),
    ):
        r = _upload_doc(client, asset["id"], name, content, media_type)
        assert (r.status_code, r.json()) == (
            422,
            {
                "detail": "That file couldn't be read — it may be damaged. Try another copy.",
                "code": "validation",
                "field": "file",
            },
        ), name
    assert list_attachments() == []


def test_doc_upload_cleans_up_when_add_doc_fails(client, monkeypatch):
    """The file is saved before the asset write; when that write is refused — here, the asset
    deleted in between — the saved file is deleted again instead of orphaned."""
    from backend.core.errors import NotFoundError
    from backend.services import owned_assets as owned_assets_service
    from backend.services.attachments import list_attachments

    asset = _create(client)

    def _vanished(change):
        raise NotFoundError(f"That asset ('{change['id']}') no longer exists.")

    monkeypatch.setattr(owned_assets_service, "apply_owned_asset_change", _vanished)
    r = _upload_doc(client, asset["id"], "statement.txt", b"hello", "text/plain")
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"
    assert list_attachments() == []


def test_upload_refusals_are_readable(client):
    from backend.api.assets_api import _MAX_UPLOAD_BYTES

    asset = _create(client)
    big = _upload_doc(
        client, asset["id"], "big.pdf", b"x" * (_MAX_UPLOAD_BYTES + 1), "application/pdf"
    )
    assert (big.status_code, big.json()) == (
        413,
        {"detail": "That file is over 25 MB.", "code": "too_large", "field": "file"},
    )
    word = _upload_doc(client, asset["id"], "letter.docx", b"PK", "application/msword")
    assert word.status_code == 415
    assert word.json() == {
        "detail": f"That file type can't be read. Supported: {SUPPORTED_UPLOADS}.",
        "code": "unsupported_type",
        "field": "file",
    }
    photo = client.post(
        f"/api/assets/{asset['id']}/photo",
        files={"file": ("rates.csv", io.BytesIO(b"a,b\n1,2"), "text/csv")},
    )
    assert (photo.status_code, photo.json()) == (
        415,
        {"detail": "The photo must be an image.", "code": "unsupported_type", "field": "file"},
    )


def test_a_failure_after_the_upload_is_linked_keeps_the_file(client, monkeypatch):
    """Security review S3: the cleanup ran on ANY exception — including one raised after the
    asset write had committed — leaving the asset pointing at a deleted file. A failed upload
    now discards its file only when no asset references it."""
    from backend.services import owned_assets as owned_assets_service
    from backend.services.attachments import list_attachments

    asset = _create(client)
    project = owned_assets_service.project

    def _broken_once_linked(row, **kw):
        if row.get("docs"):  # only the projection of the write's result
            raise RuntimeError("projection failed after the write")
        return project(row, **kw)

    monkeypatch.setattr(owned_assets_service, "project", _broken_once_linked)
    with pytest.raises(RuntimeError):
        _upload_doc(client, asset["id"], "statement.txt", b"hello", "text/plain")
    monkeypatch.undo()

    [kept] = list_attachments()
    stored = client.get(f"/api/assets/{asset['id']}").json()
    assert [d["ref_id"] for d in stored["docs"]] == [kept["id"]]
