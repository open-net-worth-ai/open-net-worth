"""P0-B: Registry insertion-order characterization test.

RFC 2026-06-25-lucius-generic-entity-registry §3.2.

The search engine's stable sort preserves provider order within the same rank
tier, so the picker row order and cursor pages both depend on REGISTRY iteration
order.  This test captures today's exact sequence as a byte-identical baseline
so Phase 1's auto-collect (which imports submodules alphabetically) can prove it
sorts back to the same order via ``descriptor.order``.

Phase 2a adds 3 generic-provider types at orders 16–18: commitment, notification
(renamed 2026-08-20 from intel_signal), alert.  Phase 2b adds 4 more at orders
19–22: income, lot, grant, vest.  A later
disposal type appends at order=25 (23/24 are connector/report).  The 16 original
types are unchanged.  Eight more append at the tail in 2026-08-23 (commitment
sub-entities, positions/options/briefs/news sources, profile).

DO NOT reorder this list without also updating every descriptor's ``order`` field
and re-running the full test suite.
"""

from __future__ import annotations

from backend.services.mentions.registry import REGISTRY

_EXPECTED_ORDER = [
    "net_worth",
    "holding",
    "instrument",
    "account",
    # 2026-10-29 — payee moved ahead of transaction (owner: a payee's short name is usually
    # what "@herbal" means, not the transaction whose description also contains it).
    "payee",
    "transaction",
    "category",
    "budget_month",
    "target",
    "allocation_slice",
    "returns",
    "watchlist",
    "automation",
    "skill",
    # Phase 2a — generic-provider types
    "commitment",
    "notification",
    "alert",
    # Phase 2b — generic-provider investment money facts
    "income",
    "lot",
    "grant",
    "vest",
    # generic-provider type over the connectors service
    "connector",
    # generic-provider TYPE CATALOG over services/reports
    "report",
    # generic-provider investment money fact — realized disposals (RealizedEventStore).
    # order=25 (after connector=23/report=24), so it appends at the tail of the sort.
    "disposal",
    # envelope — the Plan row identity (envelope⊥category split), singletons.py (order>25)
    "envelope",
    # 2026-08-23 — eight types appended by the commitment-sub-entity work (5efe84ef).
    # They sort after `envelope`, so no existing tie-break order moved; this list and the
    # count below simply had not been updated with them.
    "installment",
    "funding_source",
    "commitment_action",
    "profile",
    "position",
    "option_contract",
    "asset_brief",
    "news_source",
    # 2026-09-06 — ADR 2026-09-06-assets-tab-faithful-port, decision 11. order=35, after
    # news_source (order=34), so it appends at the tail with no existing tie-break moved.
    "asset",
    # 2026-09-26 — ADR 2026-09-26-vault-is-a-view-over-existing-stores (shipped as Documents). order=36, after asset.
    "document",
]


def test_registry_key_order_is_byte_identical():
    """REGISTRY iteration order must match _EXPECTED_ORDER exactly.

    Any change to this order is a visible, test-detectable ranking regression
    (rows in the same rank tier break ties by provider order).
    """
    actual = list(REGISTRY.keys())
    assert actual == _EXPECTED_ORDER, (
        f"REGISTRY key order changed.\n"
        f"Expected: {_EXPECTED_ORDER}\n"
        f"Got:      {actual}\n"
        "Update _EXPECTED_ORDER AND every EntityDescriptor.order field together."
    )


def test_registry_has_exactly_35_types():
    # 26 until the "agent" type went with the Agents product (2026-08-02); 25 after.
    # 33 since the commitment sub-entities landed (2026-08-23); 34 since `asset` joined
    # (2026-09-06, ADR 2026-09-06-assets-tab-faithful-port); 35 since `document` (2026-09-26,
    # ADR 2026-09-26-vault-is-a-view-over-existing-stores) — see _EXPECTED_ORDER's tail.
    assert len(REGISTRY) == 35
