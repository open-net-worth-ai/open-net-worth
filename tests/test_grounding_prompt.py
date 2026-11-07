"""
Regression test for the C1 grounding guardrail (closes MEM-1, SP1-2, INV1-1).

Lucius narrates over the system of record. In the reviewed sessions it (a) asserted a
ONWAI sale that was never recorded — there was no realized event — and (b) claimed three
times that "manual entries bypass the rules engine," which is false (categorization
Automations fire on every transaction-create path). Both stem from trusting memory/narrative
over the data stores and theorizing a mechanism instead of re-reading state.

The fix is two pieces of prompt text:
  (A) a GROUNDING / SYSTEM-OF-RECORD guardrail in the STABLE half of the system prompt
      (backend/ai/prompts.build_system_prompt) — must live before CACHE_SPLIT_MARKER so it
      is cached, not in the volatile snapshot — plus a one-line reinforcement in the
      SELLING / disposal block;
  (B) an instruction in the memory extractor prompt (backend/ai/memory.extract_and_merge)
      to never record a transaction/sale/disposal/etc. as completed history, only as an
      intention.

These string-contains assertions guard the guardrail text from silent deletion (the
accepted pattern for prompt-content regressions in this repo, cf. test_prompt_networth.py).
Fully isolated (tmp_path stores, no network/LLM).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.db.account_store import AccountStore
from backend.db.transaction_store import TransactionStore


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    """Isolate every store + cache build_system_prompt / extract_and_merge touch.
    PriceHistoryStore and InstrumentStore both resolve against the per-test isolated
    database (db_isolated) — both are DB-backed (plan 2, tasks 3 and 5)."""
    from backend.db.profile_store import ProfileStore
    from backend.services import ledger

    # profile is DB-backed now (Wave 5, JSON->DB) — core.fx.base_currency() reads it through
    # the injected loader seam (services.fx_cache registers one backed by ProfileStore), so
    # seeding the store is enough; there is no more PROFILE_FILE/PROFILE_JSON module attr to
    # monkeypatch on either services.profile or core.fx.
    ProfileStore().set_raw({"base_currency": "USD", "date_of_birth": "1990-01-01"})

    fx_path = tmp_path / "fx_cache.json"
    fx_path.write_text(json.dumps({"rates": {"USD": 1.0}, "updated": "2026-06-03T00:00:00"}))
    import backend.services.fx_cache as fx_cache_mod

    monkeypatch.setattr(fx_cache_mod, "_FX_CACHE_FILE", fx_path)

    monkeypatch.setattr(ledger, "accounts", AccountStore())
    monkeypatch.setattr(ledger, "transactions", TransactionStore())

    # Skip the fresh-setup/onboarding branch (and avoid it reading live data files).
    monkeypatch.setattr("backend.ai.prompts.is_empty_book", lambda: False)
    return tmp_path


def _norm(text: str) -> str:
    """Lowercase + collapse whitespace so phrase checks survive line wrapping."""
    return " ".join(text.lower().split())


def test_system_prompt_has_grounding_guardrail_in_stable_half(isolated):
    from backend.ai.prompts import CACHE_SPLIT_MARKER, build_system_prompt

    prompt = build_system_prompt()
    assert CACHE_SPLIT_MARKER in prompt

    # Guardrail text MUST be in the STABLE (cached) half, not the volatile snapshot.
    stable, _volatile = prompt.split(CACHE_SPLIT_MARKER, 1)
    n = _norm(stable)

    # Records are authoritative on conflict; never invent a reconciliation.
    assert "the records win" in n
    assert "my notes say x, but the records show y — the records are authoritative" in n
    assert "never invent a reconciliation" in n

    # Re-read state before explaining surprising behavior.
    assert "re-read the relevant store" in n
    assert "list_automations" in n
    assert "never theorize a mechanism you have not verified" in n

    # Disposal grounding (INV1-1).
    assert "get_realized_pnl shows a realized event" in _norm(prompt)
    assert "proposed, not applied yet" in n

    # Categorization-rule truth (SP1-2, updated 2026-08-02): rules are event-triggered
    # Automations. They still cover every create path, but they run AFTER the commit — the
    # model must not describe a row as final the moment it lands, nor claim manual/AI entries
    # are exempt.
    assert "fire on every" in n
    assert "transaction-create path" in n
    assert "after the row commits" in n
    assert "never claim manual or ai entries bypass them" in n


def test_system_prompt_has_account_model_doctrine(isolated):
    """The stable prompt half must contain the custodian / securities-account / currency-wallet rule."""
    from backend.ai.prompts import CACHE_SPLIT_MARKER, build_system_prompt

    prompt = build_system_prompt()
    stable, _volatile = prompt.split(CACHE_SPLIT_MARKER, 1)
    n = _norm(stable)

    # Core doctrine phrases that guard against misplacing positions in currency wallets.
    assert "securities account" in n
    assert "currency wallet" in n
    assert "custodian" in n


def test_system_prompt_names_the_app_in_stable_half(isolated):
    """A digest called a broker's notice about this very app "a third-party app", because the
    prompt said only "this personal wealth portal"."""
    from backend.ai.prompts import CACHE_SPLIT_MARKER, build_system_prompt

    stable, _volatile = build_system_prompt().split(CACHE_SPLIT_MARKER, 1)

    assert "This app is Open Net Worth, and you run inside it." in stable
    assert "it means this app, not an outside third party" in stable


def test_system_prompt_checks_memory_before_flagging_in_stable_half(isolated):
    """A briefing asked the owner to sort out a charge they had already disputed with their bank.
    The fact was in its memory, labelled only "apply when relevant", and its skill said to list
    everything uncategorized."""
    from backend.ai.prompts import CACHE_SPLIT_MARKER, build_system_prompt

    stable, _volatile = build_system_prompt().split(CACHE_SPLIT_MARKER, 1)

    assert "CHECK WHAT YOU REMEMBER BEFORE YOU FLAG OR ASK" in stable
    assert "This holds in a report or a scheduled run" in stable
    assert "even when your instructions say to list everything of its kind" in stable
    assert "When you can't tell whether a remembered fact covers it" in stable
    assert "Never ask them to do what they have already done or decided." in stable
    assert "If it's still open, say where it stands in a few words" in stable
    # Not asking again must not mean forgetting: the owner still needs telling to recheck.
    assert "Once the date on something still open has arrived" in stable
    assert "remind them to recheck it" in stable


def test_selling_block_has_disposal_grounding_reinforcement(isolated):
    from backend.ai.prompts import build_system_prompt

    n = _norm(build_system_prompt())
    # One-line reinforcement next to the disposal flow.
    assert "a sale is recorded only if get_realized_pnl shows a realized event" in n
    assert "handled differently" in n  # the exact rationalization we forbid


def test_system_prompt_has_materiality_guardrail(isolated):
    """Owner report 2026-08-27: Lucius was answering "why is this off" questions by declaring
    a small discrepancy "negligible" and proposing to skip investigating it, instead of
    actually tracing the cause. Dollar size is not his call to make on the user's behalf —
    this guardrail forbids substituting a magnitude judgment for the actual investigation."""
    from backend.ai.prompts import CACHE_SPLIT_MARKER, build_system_prompt

    prompt = build_system_prompt()
    stable, _volatile = prompt.split(CACHE_SPLIT_MARKER, 1)
    n = _norm(stable)

    assert "materiality is the user's call, not yours" in n
    assert "a small dollar amount is never a reason to skip the investigation" in n
    assert "never reason from the number's size alone" in n
    assert (
        "call something rounding/timing/minor only after tracing it to a named, verified cause" in n
    )
    assert 'never spin "i couldn\'t find it" into "it doesn\'t matter."' in n


def test_lean_prompt_has_materiality_guardrail(isolated):
    """The headless/lean prompt (build_lean_system_prompt) is still live — it is the
    lean_prompt system prompt for main._headless_turn_runner, reached from
    services.self_harness via an Automation executor (the named Agent product itself was
    retired 2026-08-02, but this prompt builder was repurposed, not deleted). Same
    owner-report guardrail as the chat prompt, condensed to this block's terser style."""
    import backend.ai.tools  # noqa: F401  (populates the tool registry the prompt renders)
    from backend.ai.prompts import build_lean_system_prompt

    n = _norm(build_lean_system_prompt(None))
    assert "materiality is the user's call, not yours" in n
    assert "a small dollar amount is never a reason to skip" in n


def test_memory_extractor_prompt_forbids_recording_actions_as_history(isolated, monkeypatch):
    """The extract_and_merge prompt must instruct the model to never record a
    transaction/sale/disposal as completed history — only as an intention."""
    from backend.ai import memory

    captured: dict[str, str] = {}

    class _FakeProvider:
        async def complete(self, prompt, **kwargs):
            captured["prompt"] = prompt
            return '{"new": [], "updates": [], "remove": []}'

    monkeypatch.setattr(memory, "_provider", lambda: _FakeProvider())

    history = [
        {"role": "user", "content": "I'm thinking about selling 500 ONWAI shares next week."},
        {"role": "assistant", "content": "Want me to model that disposal?"},
    ]
    asyncio.run(memory.extract_and_merge(history))

    assert "prompt" in captured, "extractor never called the provider"
    n = _norm(captured["prompt"])

    assert "never record a transaction, sale, disposal, purchase, transfer, or rule-creation" in n
    assert "completed or dated fact" in n
    assert "system of record" in n
    # Capture as intention, not history.
    assert "capture it (if at all) as an intention" in n
    assert "never as history" in n
    # Prefer durable preferences/context over perishable figures.
    assert "prefer durable preferences/context over perishable figures" in n
