"""
Unit tests for backend.services.classification — deterministic, no network.

Covers:
- _classify_heuristic: asset-class fold for bond/crypto/commodity/cash/property/equity
- _region_from_currency: known and unknown currencies
- intl-fund detection: WORLD/MSCI/ACWI/EM name/symbol hints
- classify_instrument: manual precedence (constituents OR source=="manual")
- enrich_instrument_classification: idempotency; never downgrades "source"/"manual"
- InstrumentStore factory: region/classification_source/classification_updated_at coercion
"""

from __future__ import annotations

import pytest  # noqa: F401 (used by fixtures)

from backend.db.instrument_store import InstrumentStore
from backend.services import classification as svc

# ── _region_from_currency ────────────────────────────────────────────────────


def test_region_usd():
    assert svc._region_from_currency("USD") == "United States"


def test_region_eur():
    assert svc._region_from_currency("EUR") == "Europe"


def test_region_jpy():
    assert svc._region_from_currency("JPY") == "Japan"


def test_region_sgd():
    assert svc._region_from_currency("SGD") == "Singapore"


def test_region_hkd():
    assert svc._region_from_currency("HKD") == "Greater China"


def test_region_unknown_returns_empty():
    assert svc._region_from_currency("XYZ") == ""
    assert svc._region_from_currency("") == ""


def test_region_case_insensitive():
    assert svc._region_from_currency("usd") == "United States"
    assert svc._region_from_currency("Eur") == "Europe"


# ── _classify_heuristic ──────────────────────────────────────────────────────


def _inst(asset_class: str, **kwargs) -> dict:
    """Build a minimal instrument dict for heuristic testing."""
    return {
        "asset_class": asset_class,
        "asset_subclass": kwargs.get("asset_subclass", ""),
        "quote_currency": kwargs.get("quote_currency", "USD"),
        "sector": kwargs.get("sector", ""),
        "symbol": kwargs.get("symbol", ""),
        "name": kwargs.get("name", ""),
        "constituents": kwargs.get("constituents", []),
        "classification_source": kwargs.get("classification_source", ""),
        "region": kwargs.get("region", ""),
    }


def test_heuristic_bond_fixed_income():
    result = svc._classify_heuristic(_inst("bond", quote_currency="USD"))
    assert result["sector"] == "Fixed income"
    assert result["region"] == "United States"
    assert result["source"] == "heuristic"


def test_heuristic_bond_sgd():
    result = svc._classify_heuristic(_inst("bond", quote_currency="SGD"))
    assert result["sector"] == "Fixed income"
    assert result["region"] == "Singapore"


def test_heuristic_crypto_digital_assets():
    result = svc._classify_heuristic(_inst("crypto"))
    assert result["sector"] == "Digital assets"
    assert result["region"] == "Global"


def test_heuristic_commodity_commodities():
    result = svc._classify_heuristic(_inst("commodity"))
    assert result["sector"] == "Commodities"
    assert result["region"] == "Global"


def test_heuristic_cash_no_sector():
    result = svc._classify_heuristic(_inst("cash"))
    assert result["sector"] == ""
    assert result["region"] == ""


def test_heuristic_property_no_sector():
    result = svc._classify_heuristic(_inst("property"))
    assert result["sector"] == ""
    assert result["region"] == ""


def test_heuristic_equity_keeps_gics_sector():
    result = svc._classify_heuristic(_inst("equity", sector="Technology", quote_currency="USD"))
    assert result["sector"] == "Technology"
    assert result["region"] == "United States"


def test_heuristic_equity_no_sector_blank():
    result = svc._classify_heuristic(_inst("equity", sector="", quote_currency="USD"))
    assert result["sector"] == ""
    assert result["region"] == "United States"


# ── intl-fund detection ──────────────────────────────────────────────────────


def test_intl_fund_world_symbol():
    result = svc._classify_heuristic(
        _inst("equity", asset_subclass="etf", symbol="SWRD", name="SPDR World ETF")
    )
    assert result["region"] == "Global"


def test_intl_fund_acwi():
    result = svc._classify_heuristic(
        _inst("equity", asset_subclass="etf", symbol="ACWI", name="iShares MSCI ACWI ETF")
    )
    assert result["region"] == "Global"


def test_intl_fund_em_name():
    result = svc._classify_heuristic(
        _inst(
            "equity", asset_subclass="etf", symbol="VWO", name="Vanguard FTSE Emerging Markets ETF"
        )
    )
    assert result["region"] == "Emerging Markets"


def test_intl_fund_msci_in_name():
    result = svc._classify_heuristic(
        _inst("equity", asset_subclass="etf", symbol="IWDA", name="iShares MSCI World ETF")
    )
    assert result["region"] == "Global"


def test_intl_fund_hint_is_a_whole_word_not_a_substring():
    """ "EM" inside "Semiconductor" is not Emerging Markets: a US-listed fund stays US."""
    result = svc._classify_heuristic(
        _inst("equity", asset_subclass="etf", symbol="ACMS", name="ACME Semiconductor ETF")
    )
    assert result["region"] == "United States"


@pytest.mark.parametrize(
    "symbol,name",
    [
        ("ACXU", "ACME Total International Stock ETF"),
        ("ACEF", "ACME MSCI EAFE ETF"),
        ("ACWX", "ACME FTSE All-World ex-US ETF"),
    ],
)
def test_intl_fund_ex_us_is_global_ex_us(symbol, name):
    """ex-US wins over WORLD: an all-world ex-US fund holds no US."""
    result = svc._classify_heuristic(
        _inst("equity", asset_subclass="etf", symbol=symbol, name=name)
    )
    assert result["region"] == "Global ex-US"


def test_intl_fund_not_detected_for_plain_stock():
    """A plain stock (not a fund subclass) with 'WORLD' in name doesn't trigger intl detection."""
    result = svc._classify_heuristic(
        _inst("equity", asset_subclass="stock", symbol="WORLDCO", name="World Company Inc")
    )
    # Falls back to currency-based region
    assert result["region"] == "United States"


# ── classify_instrument: manual precedence ───────────────────────────────────


def test_manual_precedence_via_constituents():
    """Non-empty constituents → source='manual', never overwritten by heuristic."""
    inst = {
        "asset_class": "equity",
        "sector": "Technology",
        "region": "United States",
        "quote_currency": "USD",
        "symbol": "",
        "name": "",
        "asset_subclass": "",
        "constituents": [{"weight": 1.0, "sector": "Technology", "region": "US", "label": ""}],
        "classification_source": "",
    }
    result = svc.classify_instrument(inst)
    assert result["source"] == "manual"
    assert result["sector"] == "Technology"


def test_manual_precedence_via_source_field():
    """classification_source=='manual' is respected even with empty constituents."""
    inst = {
        "asset_class": "bond",
        "sector": "Fixed income",
        "region": "Europe",
        "quote_currency": "EUR",
        "symbol": "",
        "name": "",
        "asset_subclass": "",
        "constituents": [],
        "classification_source": "manual",
    }
    result = svc.classify_instrument(inst)
    assert result["source"] == "manual"
    assert result["region"] == "Europe"


def test_classify_instrument_falls_to_heuristic():
    """Instrument with no manual override falls through to heuristic."""
    inst = {
        "asset_class": "crypto",
        "sector": "",
        "region": "",
        "quote_currency": "USD",
        "symbol": "BTC",
        "name": "Bitcoin",
        "asset_subclass": "coin",
        "constituents": [],
        "classification_source": "",
    }
    result = svc.classify_instrument(inst)
    assert result["sector"] == "Digital assets"
    assert result["region"] == "Global"
    assert result["source"] == "heuristic"


# ── enrich_instrument_classification idempotency ─────────────────────────────


@pytest.fixture
def isolated_store(tmp_path, monkeypatch, db_isolated):
    """InstrumentStore is DB-backed (plan 2, task 5) — db_isolated gives it a fresh
    per-test database."""
    return InstrumentStore()


def test_enrich_bond_fills_region(isolated_store):
    isolated_store.create({"symbol": "SGS", "asset_class": "bond", "quote_currency": "SGD"})
    result = svc.enrich_instrument_classification()
    assert result["checked"] >= 1
    assert result["updated"] == 1
    inst = isolated_store.all()[0]
    assert inst["sector"] == "Fixed income"
    assert inst["region"] == "Singapore"
    assert inst["classification_source"] == "heuristic"


def test_enrich_idempotent_second_run_zero_updates(isolated_store):
    """Re-running enrich must return updated==0 (already filled)."""
    isolated_store.create({"symbol": "BTC", "asset_class": "crypto", "quote_currency": "USD"})
    svc.enrich_instrument_classification()
    result2 = svc.enrich_instrument_classification()
    assert result2["updated"] == 0


def test_enrich_never_downgrades_source_level(isolated_store):
    """Instruments with classification_source='source' are never overwritten by heuristic."""
    isolated_store.create(
        {
            "symbol": "AAPL",
            "asset_class": "equity",
            "quote_currency": "USD",
            "sector": "Technology",
            "region": "United States",
            "classification_source": "source",
        }
    )
    result = svc.enrich_instrument_classification()
    # Row is checked but NOT updated — source level is above heuristic.
    assert result["checked"] == 1
    assert result["updated"] == 0


def test_enrich_never_downgrades_manual(isolated_store):
    """Instruments with classification_source='manual' are never overwritten."""
    isolated_store.create(
        {
            "symbol": "ETF1",
            "asset_class": "equity",
            "quote_currency": "USD",
            "sector": "Custom",
            "region": "Custom Region",
            "classification_source": "manual",
        }
    )
    svc.enrich_instrument_classification()
    inst = isolated_store.all()[0]
    assert inst["sector"] == "Custom"
    assert inst["region"] == "Custom Region"
    assert inst["classification_source"] == "manual"


# ── InstrumentStore factory coercion ─────────────────────────────────────────


def test_store_factory_defaults_region_empty(isolated_store):
    """Instruments created without region default to ''."""
    inst = isolated_store.create({"symbol": "AAPL", "quote_currency": "USD"})
    assert inst["region"] == ""
    assert inst["classification_source"] == ""
    assert inst["classification_updated_at"] is None


def test_store_factory_persists_region(isolated_store):
    """Explicitly supplied region is persisted and round-trips through disk."""
    inst = isolated_store.create(
        {
            "symbol": "AAPL",
            "quote_currency": "USD",
            "region": "United States",
            "classification_source": "heuristic",
        }
    )
    assert inst["region"] == "United States"
    assert inst["classification_source"] == "heuristic"

    reread = isolated_store.find_by_id(inst["id"])
    assert reread["region"] == "United States"
    assert reread["classification_source"] == "heuristic"


def test_store_factory_coerces_bad_source(isolated_store):
    """Unknown classification_source values are normalised to ''."""
    inst = isolated_store.create(
        {"symbol": "X", "quote_currency": "USD", "classification_source": "unknown_value"}
    )
    assert inst["classification_source"] == ""


def test_store_legacy_record_loads_with_defaults(db_isolated):
    """A legacy record (no region/classification_source fields) reads back the raw stored
    dict (no read-time fallback). The factory coercion runs at CREATE time, ensuring any newly
    written instrument carries the fields. A touch() on a legacy record transparently adds them.

    Seeded via the store's own ``insert()`` — a dumb, uncoerced write on both the retired
    JsonStore and the current PayloadTableStore — so this reproduces "a row written before
    these fields existed" without going through _make_instrument's coercion."""
    # A minimal legacy-format record (no new fields)
    legacy = {
        "id": "inst-old",
        "symbol": "OLD",
        "name": "Old Corp",
        "asset_class": "equity",
        "asset_subclass": "stock",
        "sector": "Industrials",
        "quote_currency": "USD",
        "price_source": "yahoo",
        "price_source_id": "OLD",
        "precision": 4,
        "unit": "",
        "quote_per_unit": 1.0,
        "identifiers": {},
        "manual_price": None,
        "constituents": [],
        "note": "",
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
    }
    store = InstrumentStore()
    store.insert(legacy)

    # Raw read returns the stored record as inserted (no read-time injection).
    raw = store.find_by_id("inst-old")
    assert raw is not None

    # After a touch() the stored record is updated by update_by_id which merges changes;
    # new fields will be present once explicitly written.
    updated = store.touch("inst-old", {"region": "", "classification_source": ""})
    assert updated is not None
    assert "region" in updated
    assert updated["region"] == ""
    assert "classification_source" in updated

    # classify_instrument tolerates missing keys via .get() — works on raw legacy dicts.
    classification = svc.classify_instrument(raw)
    assert isinstance(classification["sector"], str)
    assert isinstance(classification["region"], str)


def test_store_touch_round_trips_override(isolated_store):
    """touch() persists classification override fields cleanly."""
    inst = isolated_store.create(
        {"symbol": "VOO", "asset_class": "equity", "quote_currency": "USD"}
    )
    updated = isolated_store.touch(
        inst["id"],
        {"region": "Global", "classification_source": "manual"},
    )
    assert updated["region"] == "Global"
    assert updated["classification_source"] == "manual"
    reread = isolated_store.find_by_id(inst["id"])
    assert reread["region"] == "Global"
    assert reread["classification_source"] == "manual"
