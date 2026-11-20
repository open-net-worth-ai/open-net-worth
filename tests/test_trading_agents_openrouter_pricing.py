"""TradingAgents prices an OpenRouter model by OpenRouter's own rows of the vendored price table,
including its variants and dated snapshots. Synthetic data (ticker ACME); model ids are real rows
of ``model_prices.json``."""

from __future__ import annotations

import pytest

from backend.ai.models import price_map
from backend.ai.models.pricing import list_price_rates
from backend.ai.trading_agents import bridge_models, rates
from backend.ai.trading_agents.bridge_models import ALIAS_QUICK, BridgeModel
from backend.core.errors import ModelUnavailableError
from backend.services.trading_agents import budget, settings
from backend.services.trading_agents.settings import ResolvedModels

pytestmark = pytest.mark.flags(trading_agents=True)

OPENROUTER = "https://openrouter.ai/api/v1"
SONNET = "anthropic/claude-sonnet-4.5"
DATED_UNKNOWN = "deepseek/deepseek-v4.1-flash-1017"
DATED_KNOWN = "deepseek/deepseek-v4-flash-0731"
ANALYSTS = ["market"]


@pytest.fixture(autouse=True)
def isolated_db(db_isolated):
    return db_isolated


@pytest.fixture
def openrouter(monkeypatch):
    monkeypatch.setattr(settings, "provider_base_url", lambda ref: OPENROUTER)


def _models(model: str) -> ResolvedModels:
    ref = f"openrouter:{model}"
    return ResolvedModels(deep=ref, quick=ref, deep_transport="api", quick_transport="api")


def _check(model: str) -> budget.Estimate:
    cfg = settings.load()
    return budget.check_can_start(cfg, _models(model), ANALYSTS, rates.rate_for_ref)


def _bridge_model(model: str) -> BridgeModel:
    return BridgeModel(
        ALIAS_QUICK, f"openrouter:{model}", model, "OpenRouter", "api", None, base_url=OPENROUTER
    )


def test_a_bare_model_id_has_no_openrouter_price_but_its_endpoint_finds_it():
    assert list_price_rates(SONNET) is None
    assert list_price_rates(SONNET, OPENROUTER) == pytest.approx((3.0, 15.0))


def test_an_openrouter_model_is_priced_by_the_estimate_and_the_start_check(openrouter):
    cfg = settings.load()
    got = budget.estimate(cfg, _models(SONNET), ANALYSTS, rates.rate_for_ref)
    assert got.priced and got.unpriced_models == () and got.median_usd > 0
    assert _check(SONNET).priced


def test_an_openrouter_model_is_reserved_at_its_price_not_the_dearest_rates(openrouter):
    model = _bridge_model(SONNET)
    expected = (3.0 * 1000 / 3 + model.output_ceiling_tokens * 15.0) / 1_000_000
    assert model.worst_case_usd(1000) == pytest.approx(expected)
    assert not model.is_unpriced(bridge_models.shape.Usage())


def test_a_free_variant_with_its_own_row_is_priced_at_zero(openrouter):
    free = "apodex/apodex-1.1-mini:free"
    assert list_price_rates(free, OPENROUTER) == (0.0, 0.0)
    assert _check(free).median_usd == 0.0


def test_a_variant_without_a_row_is_priced_as_the_model_it_varies(openrouter):
    assert list_price_rates(f"{SONNET}:thinking", OPENROUTER) == pytest.approx((3.0, 15.0))
    assert _check(f"{SONNET}:thinking").priced


def test_a_model_the_table_lacks_is_still_refused_with_the_specific_message(openrouter):
    with pytest.raises(ModelUnavailableError) as raised:
        _check("acme/no-such-model")
    assert "openrouter:acme/no-such-model has no known price" in raised.value.message
    assert "make refresh-prices" in raised.value.message
    assert budget.estimate(
        settings.load(), _models("acme/no-such-model"), ANALYSTS, rates.rate_for_ref
    ).unpriced_models == ("openrouter:acme/no-such-model",)


def test_a_dated_snapshot_the_table_lacks_is_priced_as_the_undated_model(openrouter):
    assert list_price_rates(DATED_UNKNOWN, OPENROUTER) is None
    rate = rates.rate_for_ref(f"openrouter:{DATED_UNKNOWN}")
    undated = list_price_rates("deepseek/deepseek-v4.1-flash", OPENROUTER)
    assert rate is not None and tuple(rate[:2]) == undated
    assert rate.priced_as == "deepseek/deepseek-v4.1-flash"
    got = _check(DATED_UNKNOWN)
    assert got.priced and got.as_dict()["approximate_prices"] == [
        {"model": f"openrouter:{DATED_UNKNOWN}", "priced_as": "deepseek/deepseek-v4.1-flash"}
    ]


def test_an_exact_dated_row_beats_the_stripped_one(openrouter):
    rate = rates.rate_for_ref(f"openrouter:{DATED_KNOWN}")
    assert rate is not None and rate.priced_as is None
    assert tuple(rate) == pytest.approx(
        price_map.vendored_rates("openrouter/" + DATED_KNOWN) + (None,)
    )


def test_a_dated_snapshot_whose_undated_model_is_unknown_too_is_refused(openrouter):
    assert rates.rate_for_ref("openrouter:acme/no-such-model-1017") is None
    with pytest.raises(ModelUnavailableError):
        _check("acme/no-such-model-1017")


@pytest.mark.parametrize("model", ["acme/model-4096", "acme/model-0000", "acme/model-1332"])
def test_a_number_that_is_not_a_date_is_not_stripped(model):
    assert price_map._dated_base(model) is None


@pytest.mark.parametrize(
    "model", ["acme/m-1017", "acme/m-251017", "acme/m-20251017", "acme/m-2025-10-17"]
)
def test_every_date_spelling_is_stripped(model):
    assert price_map._dated_base(model) == "acme/m"


def test_a_snapshot_reservation_is_doubled_and_still_not_unpriced(openrouter):
    exact = _bridge_model("deepseek/deepseek-v4.1-flash")
    snapshot = _bridge_model(DATED_UNKNOWN)
    assert snapshot.worst_case_usd(900) == pytest.approx(2 * exact.worst_case_usd(900))
    assert not snapshot.is_unpriced(bridge_models.shape.Usage())
    assert _bridge_model("acme/no-such-model").worst_case_usd(0) == pytest.approx(
        bridge_models._UNPRICED_RATES[1] * _bridge_model("acme/x").output_ceiling_tokens / 1_000_000
    )
