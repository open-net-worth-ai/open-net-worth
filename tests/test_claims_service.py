"""services/claims.py — the cross-subject push-dedup registry (owner override of RFC
intel-signals-data-model §4's "NO-GO"; see
docs/adr/2026-08-30-claims-registry-cross-subject-push-dedup.md).

Three layers of coverage:
  * `group_key_for` — pure unit tests, including the RFC's own negative case (a price alert and
    an M&A topic on the same ticker must never collapse).
  * `apply_claim_push`/`is_recent_duplicate_push` — against a real `db_isolated` database.
  * A regression test reproducing the PROVEN incident shape at the exact seam this task
    modified (`analyst._push_decision`'s new gate): two independently-processed signals about
    the same real fact, the second one's push suppressed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import backend.services.claims as claims_svc

# ── group_key_for ────────────────────────────────────────────────────────────────────────


def test_group_key_for_asset_and_topic_on_same_instrument_collapse():
    """The core positive case: a topic row ('Kestrel Dash M&A') and an asset row
    ('Kestrel Dash SE') that both resolve to the SAME instrument_id get the SAME group_key —
    this is what lets the cross-tick dedup actually catch the proven incident."""
    asset_key = claims_svc.group_key_for("asset", ["inst_kstl"])
    topic_key = claims_svc.group_key_for("topic", ["inst_kstl"])
    assert asset_key is not None
    assert asset_key == topic_key


def test_group_key_for_price_alert_never_groups_even_on_the_same_ticker():
    """The RFC's own scoping caution, proven structurally: a price_alert row and an M&A-topic
    row sharing an instrument_id must NEVER collapse into the same claim — a price threshold
    crossing and a corporate development are different KINDS of things."""
    assert claims_svc.group_key_for("price_alert", ["inst_kstl"]) is None
    topic_key = claims_svc.group_key_for("topic", ["inst_kstl"])
    assert topic_key is not None
    # Even with the identical instrument, price_alert's own key is None — it can never equal
    # (or be mistaken for) the topic's real key.
    assert claims_svc.group_key_for("price_alert", ["inst_kstl"]) != topic_key


def test_group_key_for_daily_digest_and_monitoring_never_group():
    assert claims_svc.group_key_for("daily_digest", ["inst_kstl"]) is None
    assert claims_svc.group_key_for("monitoring", ["inst_kstl"]) is None


def test_group_key_for_groupable_types_all_resolve_with_one_instrument():
    for story_type in ("asset", "topic", "risk", "opportunity", "macro"):
        assert claims_svc.group_key_for(story_type, ["inst_x"]) == "news-fact:inst_x"


def test_group_key_for_zero_instruments_is_ungrouped():
    assert claims_svc.group_key_for("asset", []) is None
    assert claims_svc.group_key_for("topic", []) is None


def test_group_key_for_ambiguous_multi_instrument_is_ungrouped_not_guessed():
    """A topic linked to several tickers, or a risk finding spanning a whole sector/region, is
    never collapsed with anything — the same conservative posture `_linked_instrument_ids`
    already takes for an ambiguous ticker match."""
    assert claims_svc.group_key_for("topic", ["inst_a", "inst_b"]) is None
    assert claims_svc.group_key_for("risk", ["inst_a", "inst_b", "inst_c"]) is None


def test_group_key_for_dedupes_and_ignores_falsy_ids():
    # The same id repeated, plus an empty string, still resolves to exactly one distinct id.
    assert claims_svc.group_key_for("asset", ["inst_x", "inst_x", ""]) == "news-fact:inst_x"


# ── apply_claim_push / is_recent_duplicate_push ─────────────────────────────────────────────


def test_is_recent_duplicate_push_false_when_no_claim_exists(db_isolated):
    assert claims_svc.is_recent_duplicate_push("news-fact:inst_never_pushed") is False


def test_apply_claim_push_then_is_recent_duplicate_push_true(db_isolated):
    claims_svc.apply_claim_push(
        "news-fact:inst_x",
        story_id="story_asset",
        statement="Formal tender offer announced.",
        sources=[{"url": "https://example.com/a", "title": "x", "domain": "example.com"}],
        materiality="new",
        decision_relevant=True,
    )
    assert claims_svc.is_recent_duplicate_push("news-fact:inst_x") is True


def test_is_recent_duplicate_push_false_past_the_freshness_window(db_isolated):
    from backend.db.claims_store import ClaimsStore

    stale_pushed_at = (
        datetime.now(timezone.utc) - timedelta(hours=claims_svc._CLAIM_PUSH_FRESHNESS_HOURS + 1)
    ).isoformat()
    ClaimsStore().record_push(
        "news-fact:inst_x",
        story_id="story_asset",
        statement="Old news.",
        sources=[],
        materiality="new",
        decision_relevant=True,
        confirmed_at=None,
        now=stale_pushed_at,
    )
    assert claims_svc.is_recent_duplicate_push("news-fact:inst_x") is False


def test_is_recent_duplicate_push_ignores_unrelated_group_keys(db_isolated):
    claims_svc.apply_claim_push(
        "news-fact:inst_x",
        story_id="story_1",
        statement="Something happened to X.",
        sources=[],
        materiality="new",
        decision_relevant=True,
    )
    assert claims_svc.is_recent_duplicate_push("news-fact:inst_y") is False


# ── Regression: the proven incident (two signals, same real fact, second push suppressed) ──


_GROUNDED = {
    "duplicate": False,
    "flagged": False,
    "sources": [{"url": "https://reuters.com/tender-offer", "title": "x", "domain": "reuters.com"}],
    "source_url": "",
    "confirmed_at": None,
}


# With the delivery decider on, Lucius judges repeats instead (tests/test_crash_alert_news.py).
@pytest.mark.flags(intel_delivery_decider=False)
def test_second_signals_push_is_suppressed_as_duplicate_of_an_already_pushed_claim(db_isolated):
    """Direct reproduction of the proven production incident, with synthetic data: an
    asset-type story ("Kestrel Dash SE") and a topic-type story ("Kestrel Dash M&A") both
    resolve to the SAME instrument (`inst_kstl`) and both independently synthesize a materially
    new, grounded, decision-relevant verdict about the SAME real fact (a formal tender offer) —
    the asset side processes first and pushes; the topic side's own independent synthesis,
    ~10 hours later on a LATER sweep tick (past `_find_linked_pairs`'s same-tick-only reach),
    must now be suppressed as a duplicate of the claim the asset side already recorded."""
    import backend.monitoring.analyst as analyst_mod

    group_key = claims_svc.group_key_for("asset", ["inst_kstl"])
    assert group_key == claims_svc.group_key_for("topic", ["inst_kstl"])

    asset_row = {"created_at": datetime.now(timezone.utc).isoformat()}
    # The asset-side signal (tick 1): fresh, grounded, materially new — pushes.
    should_push, reason, _ = analyst_mod._push_decision(
        asset_row, "new", True, _GROUNDED, group_key=group_key
    )
    assert (should_push, reason) == (True, "")
    claims_svc.apply_claim_push(
        group_key,
        story_id="story_asset_kstl",
        statement="Kestrel Dash receives a formal tender offer.",
        sources=_GROUNDED["sources"],
        materiality="new",
        decision_relevant=True,
        confirmed_at=None,
    )

    # The topic-side signal (tick 2, ~10h later — still well within the freshness window, and
    # well past `analyst._STALE_PUSH_HOURS`, the DIFFERENT 6h "too-late-to-bother" window this
    # registry deliberately does not reuse): independently fresh, grounded, materially new.
    topic_row = {"created_at": (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()}
    should_push, reason, _ = analyst_mod._push_decision(
        topic_row, "new", True, _GROUNDED, group_key=group_key
    )
    assert (should_push, reason) == (False, "duplicate_claim")


def test_a_stories_own_first_push_for_a_group_key_is_never_suppressed(db_isolated):
    """Baseline: with no prior claim recorded for this group_key, a fresh/grounded/material row
    pushes normally — the new gate only fires once a claim already exists."""
    import backend.monitoring.analyst as analyst_mod

    group_key = claims_svc.group_key_for("asset", ["inst_never_seen_before"])
    row = {"created_at": datetime.now(timezone.utc).isoformat()}
    should_push, reason, _ = analyst_mod._push_decision(
        row, "new", True, _GROUNDED, group_key=group_key
    )
    assert (should_push, reason) == (True, "")


def test_a_price_alert_type_row_is_never_suppressed_by_an_unrelated_claim(db_isolated):
    """Defense in depth for the RFC's own conflation concern: even when a claim already exists
    for an instrument's news-fact group_key, a price_alert-type row on the SAME instrument never
    resolves a group_key at all (`group_key_for` returns None for that type) — so it can never
    be suppressed by this gate, regardless of what the asset/topic side already pushed."""
    import backend.monitoring.analyst as analyst_mod

    iid = "inst_price_alert_case"
    news_group_key = claims_svc.group_key_for("asset", [iid])
    claims_svc.apply_claim_push(
        news_group_key,
        story_id="story_asset",
        statement="Some material development.",
        sources=_GROUNDED["sources"],
        materiality="new",
        decision_relevant=True,
    )

    price_alert_group_key = claims_svc.group_key_for("price_alert", [iid])
    assert price_alert_group_key is None

    row = {"created_at": datetime.now(timezone.utc).isoformat()}
    should_push, reason, _ = analyst_mod._push_decision(
        row, "new", True, _GROUNDED, group_key=price_alert_group_key
    )
    assert (should_push, reason) == (True, "")
