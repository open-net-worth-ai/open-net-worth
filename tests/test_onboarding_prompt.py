"""The onboarding hint Lucius carries while the book is empty (design spec §5.2), and the
"onboarding-new-user" skill that carries the actual playbook (2026-08-25).

The bug these pin shut, originally: for months the backend injected a block telling Lucius its
"ONLY job right now" was a five-question interview, while the frontend trigger that would
satisfy it was never armed — so a fresh user got silence, and any unrelated question got
railroaded.

The subtler one, found while wiring this up: the block used to gate on ``is_fresh_setup()``.
First run now STAMPS completion and then hands off to Lucius, so that gate would silence his
instructions at exactly the moment they are needed. It keys off ``is_empty_book()`` instead.

2026-08-25: the five-question interview itself moved out of the system prompt (injected on
EVERY turn while the book stayed empty) into the "onboarding-new-user" skill — loaded only when
actually activated. `ChatPanel`'s onboarding trigger now activates it deterministically via a
structured skill invocation on the handoff turn, the same "host-owned, not model-discretionary"
mechanism a `/slug` skill invocation uses. What stays in the system prompt is a short standing
reminder — the one thing pure host-side activation on the handoff turn can't cover: a LATER,
separate conversation started while the book is still empty.
"""

from __future__ import annotations

from backend.ai.prompts import build_system_prompt
from backend.db.onboarding_state_store import OnboardingStateStore
from backend.db.skill_store import SkillStore


def _onboarding_skill_body() -> str:
    SkillStore().seed_starters()
    skill = SkillStore().find_by_id("onboarding-new-user")
    assert skill is not None
    return skill["body"]


def test_an_empty_book_gets_the_onboarding_hint(db_isolated):
    assert "EMPTY BOOK" in build_system_prompt()


def test_the_hint_names_the_skill_to_activate(db_isolated):
    assert "onboarding-new-user" in build_system_prompt()


def test_the_hint_survives_first_run_completion(db_isolated):
    """The regression: Lucius onboards AFTER the stamp is written. Gating on the stamp would
    hand him an empty prompt precisely when he is supposed to be running onboarding."""
    OnboardingStateStore().mark_complete()
    assert "EMPTY BOOK" in build_system_prompt()


def test_the_hint_does_not_claim_onboarding_is_his_only_job(db_isolated):
    """A user asking something unrelated must get an answer, not an interview."""
    assert "ONLY job" not in build_system_prompt()


def test_a_populated_book_drops_the_hint(db_isolated):
    """It must disappear on its own once there is data — otherwise it rides every turn
    forever, costing tokens for guidance nobody needs."""
    from backend.db.account_store import AccountStore

    AccountStore().create({"name": "Meridian Bank Checking", "type": "cash", "currency": "SGD"})
    assert "EMPTY BOOK" not in build_system_prompt()


def test_the_two_predicates_answer_different_questions(db_isolated):
    """is_fresh_setup = "show first run?" (stamp). is_empty_book = "is there data?" (data).
    Collapsing them is the bug above."""
    from backend.services.setup import is_empty_book, is_fresh_setup

    assert is_fresh_setup() is True
    assert is_empty_book() is True

    OnboardingStateStore().mark_complete()
    assert is_fresh_setup() is False
    assert is_empty_book() is True


def test_the_skill_asks_for_the_nickname_not_just_the_name(db_isolated):
    """`nickname` is what Lucius actually addresses the user by (prompts.py's nickname_line),
    and neither onboarding flow ever asked for it."""
    assert "nickname" in _onboarding_skill_body().lower()


def test_the_skill_closes_with_honest_limits_stated_once(db_isolated):
    """Onboarding closes with one genuine, plain-spoken statement of what Lucius is (an AI
    that can be wrong, worth double-checking) and isn't (a licensed financial advisor — the
    decision and its risk stay the user's), and what its job actually is (effortless
    tracking/budgeting, and on investing: avoid FOMO and avoid losing money to carelessness —
    never a promise to make the user richer). Said ONCE, not as a per-message disclaimer."""
    body = _onboarding_skill_body()
    assert "not a licensed financial advisor" in body
    assert "double-checking" in body
    assert "FOMO" in body
    assert "not to make them richer" in body
    assert "not something to repeat every time investing comes up" in body


def test_the_skill_offers_a_holdings_screenshot_as_an_alternative_to_typing_each_position(
    db_isolated,
):
    """Typing every position one at a time is real friction for anyone with more than a
    couple of holdings. Lucius can already read an uploaded image via the configured vision
    reader, so onboarding offers it as a choice — but must confirm what it extracted before
    saving, since a cropped or blurry screenshot is an easy misread."""
    body = _onboarding_skill_body()
    assert "upload a screenshot or statement of their holdings" in body
    assert "confirm the full list back to them" in body
    assert "a cropped or blurry image is an easy misread" in body


def test_the_skill_does_not_restart_from_scratch_when_invoked_in_a_later_conversation(
    db_isolated,
):
    """Real report: a returning user (name/identity already saved from an earlier
    conversation) started a NEW conversation, and once the skill self-activated it played
    the full first-meeting ceremony again — including re-asking for a nickname it already
    had. The FINANCIAL PROFILE block in the system prompt already carries whatever is
    known, on every turn, with no tool call needed — the skill just has to be told to look
    at it before running its script instead of assuming a blank slate every time."""
    body = _onboarding_skill_body()
    assert "FINANCIAL PROFILE already in your context" in body
    assert "this isn't actually a first meeting" in body
    assert "never re-ask anything already sitting there" in body


def test_first_greeting_is_concise_before_asking_for_a_name(db_isolated):
    body = _onboarding_skill_body()
    assert "one or two plain" in body
    assert "Do not list capabilities" in body


def test_the_skill_is_honest_about_migration_not_existing(db_isolated):
    """YNAB/Actual budget migration is unbuilt. Lucius must not imply it works — the connector
    syncs transactions only."""
    body = _onboarding_skill_body()
    assert "YNAB" in body
    assert "not built yet" in body


def test_the_skill_is_both_host_and_model_invocable(db_isolated):
    """Host-owned on the handoff turn (ChatPanel's structured `skills` invocation) AND
    model-invocable later (the short prompt hint's `activate_skill` instruction) — neither
    path works without the other, so both must stay open."""
    from backend.db.skill_files import allows_model_invocation, allows_user_invocation

    SkillStore().seed_starters()
    skill = SkillStore().find_by_id("onboarding-new-user")
    assert skill is not None
    assert allows_user_invocation(skill.get("claude")) is True
    assert allows_model_invocation(skill.get("claude")) is True


def test_the_skill_mentions_ibkr_xml_format(db_isolated):
    """If the user sets up Interactive Brokers via Connectors, the onboarding skill instructs
    reminding them to set the Flex Query format to XML."""
    body = _onboarding_skill_body()
    assert "Interactive Brokers" in body
    assert "XML" in body


def _onboarding_reference(name: str) -> str:
    from backend.db.skill_store import STARTER_SKILLS

    starter = next(s for s in STARTER_SKILLS if s["id"] == "onboarding-new-user")
    return starter["files"][f"references/{name}"]


def test_the_skill_asks_nothing_personal_before_value(db_isolated):
    """Owner (2026-10-11, a friend's test): onboarding asked age, marriage, residence,
    citizenship and employment in one message before a single account existed. Personal
    questions now wait until something needs them; the flow gets money in first."""
    body = _onboarding_skill_body()
    assert "Personal questions too early" in body
    assert "all wait until something they ask needs one" in body
    assert "PROFILE ON DEMAND" in body
    # The old combined identity question is gone from the skill.
    for field in ("marital_status", "countries_of_residence", "tax_residencies"):
        assert field not in body


def test_the_skill_offers_profile_then_asks_what_they_want_first(db_isolated):
    """After the name: one optional line pointing at Settings › Profile, then what they want
    to do first, and the flow branches on it."""
    body = _onboarding_skill_body()
    assert "[Settings › Profile](/profile)" in body
    assert "ask what they'd like to do first" in body
    assert "Follow their answer" in body
    assert "references/retirement.md" in body


def test_the_skill_keeps_one_question_per_message_and_no_reaching_ahead(db_isolated):
    body = _onboarding_skill_body()
    assert "One question per message" in body
    assert "Do not reach ahead into a later step's topic" in body


def test_procedures_live_in_references_read_only_when_needed(db_isolated):
    """The import, investment and installment procedures moved out of the body (progressive
    disclosure) — the body names the file to read at the step that needs it."""
    body = _onboarding_skill_body()
    for name in (
        "statements.md",
        "investments.md",
        "installments.md",
        "budget.md",
        "retirement.md",
    ):
        assert f"references/{name}" in body
    statements = _onboarding_reference("statements.md")
    assert "`to_account_id`" in statements
    assert "never import it as spending and patch it afterwards" in statements
    assert "check the displayed balances" in statements
    assert "nothing to re-date or re-amount" in statements
    investments = _onboarding_reference("investments.md")
    assert "Resolve the ticker or coin name to its real instrument FIRST" in investments
    assert "REUSE ONE storage venue across all physical metals" in investments
    assert "full contract price" in _onboarding_reference("installments.md")


def test_the_prompt_asks_for_a_profile_field_only_when_an_answer_needs_it(db_isolated):
    """PROFILE ON DEMAND (main prompt, every conversation): ask one missing field with its
    reason when an answer depends on it; never chase a skipped one; a bare age is NEVER
    turned into an invented birth date (persona QA 2026-10-14: "I'm 44" was stored as
    1 January 1982); free-form context is memory, not the profile."""
    prompt = build_system_prompt()
    assert "PROFILE ON DEMAND" in prompt
    assert "Never ask for a field the question at hand does not need" in prompt
    assert "never re-ask one they skipped" in prompt
    assert "NEVER invent a value to store" in prompt
    assert "a bare age" in prompt and "is not a birth date" in prompt
    assert "January 1 of that birth year" not in prompt
    assert "is memory — see MEMORY below" in prompt
    assert "profile's `notes` field" not in prompt
    assert "Settings > Profile" in prompt


def test_the_book_starts_from_todays_balances_and_history_is_optional(db_isolated):
    """Owner (2026-10-11): start from today's balances; past transactions are an optional
    one-line offer, made only where they pay off now (spending, budget), never a requirement."""
    body = _onboarding_skill_body()
    assert "starts from today's balances — no history needed" in body
    assert "Past transactions are optional — never a requirement" in body
    assert "only on the spending or budget branch" in body


def test_the_skill_follows_the_house_authoring_guide(db_isolated):
    """skill-creator's authoring guide: the description carries trigger phrasings and a
    "Not for" line naming near-misses; the body leads with a Gotchas section of the mistakes
    seen in testing."""
    SkillStore().seed_starters()
    skill = SkillStore().find_by_id("onboarding-new-user")
    assert skill is not None
    desc = skill["description"]
    assert len(desc) <= 1024
    assert "help me get started" in desc
    assert "Not for importing a statement into a book that already has accounts" in desc
    assert skill["body"].lstrip().split("## Gotchas")[0].count("\n\n") <= 1
    for gotcha in (
        "Re-asking what you know",
        "Echoing around `show_guide`",
        "Bundling",
        "Re-offering",
    ):
        assert gotcha in skill["body"]


# ── Persona QA, 2026-10-14: behaviour the skill text must keep shut ─────────────────────────


def test_the_skill_never_invents_a_profile_fact(db_isolated):
    """Priya said "I'm 44" and 1 January 1982 was saved. An age is not a birth date."""
    body = _onboarding_skill_body()
    assert "Inventing a fact" in body
    assert '"I\'m 44" is not a birth date' in body
    assert "store nothing" in body


def test_the_attach_guide_is_for_the_spending_path_or_a_stated_file(db_isolated):
    """Tom (budget) and Priya (retirement) had no files and still got "Attach a statement…"."""
    body = _onboarding_skill_body()
    assert "Guides for the wrong person" in body
    assert "only when they are on the spending path or have said they have a statement" in body
    assert "leave the paperclip dark" in body
    prompt = build_system_prompt()
    assert "ONLY when they are tracking spending or have said they have a statement" in prompt


def test_the_broker_is_one_securities_account_and_positions_use_its_id(db_isolated):
    """James ended with two IBKR accounts and a stray Starting Balance. The custodian model
    (DATA_MODEL.md) stands: one securities account per broker plus a cash wallet per currency."""
    investments = _onboarding_reference("investments.md")
    assert "ONE securities account named after it" in investments
    assert "a separate cash wallet per currency" in investments
    assert "never record a position on a cash wallet" in investments
    assert "`account_id` to `add_positions` for EVERY holding" in investments
    assert 'never "tidy up" by creating another account' in investments


def test_the_budget_path_asks_the_style_and_adds_nothing_unasked(db_isolated):
    """Tom was told the budget "uses 50/30/20" before choosing, and got an S$800 Emergency fund."""
    body = _onboarding_skill_body()
    assert "ask in one line which style they want" in body
    assert "already means zero-based" in body
    assert "Name no method before they choose" in body
    assert "never an envelope you add or an amount you pick" in body


def test_the_email_offer_is_made_once_and_a_no_closes_it(db_isolated):
    """James was offered email logging twice; Mai again after she said she would rather not."""
    body = _onboarding_skill_body()
    assert "only once" in body
    assert "never raise email again" in body
    assert "closes it for good" in body


# ── Persona QA round 5, 2026-10-14: the budget and retirement paths ─────────────────────────


def test_the_budget_path_starts_from_the_starter_envelopes_and_clears_them_once(db_isolated):
    """Tom named Food and Going out beside a book that already had Groceries and Dining out, got
    both, and then had to ask for nine deletions."""
    body = _onboarding_skill_body()
    assert "Duplicating the starter envelopes" in body
    budget = _onboarding_reference("budget.md")
    assert "Call list_envelopes before creating anything" in budget
    assert "rename it to their word with rename_categories" in budget
    assert 'Never leave "Food" beside "Groceries"' in budget
    assert "Offer once to clear the starters" in budget
    assert "never raise it again" in budget
    assert "delete_categories" in budget


def test_the_budget_is_not_done_until_cash_on_hand_is_assigned(db_isolated):
    """Tom was told "your budget is in place" at S$0 assigned / S$2,150 Ready to Assign, from a
    plan sized to the S$3,600 pay that had not arrived."""
    body = _onboarding_skill_body()
    assert "with nothing assigned" in body
    budget = _onboarding_reference("budget.md")
    assert "`ready_to_assign` is the cash they have today" in budget
    assert "Their monthly pay is NOT cash yet" in budget
    assert "ONE assign_budget call" in budget
    assert 'Do not say the budget is "in place"' in budget
    assert "until get_budget_month shows money assigned" in budget


def test_a_new_envelope_needs_no_link_step(db_isolated):
    """create_envelope links its own category now (persona QA round 5)."""
    budget = _onboarding_reference("budget.md")
    assert "no separate category step" in budget


def test_the_retirement_path_enters_the_outlook_for_them(db_isolated):
    """Priya was told to fill the Outlook in herself, and a guessed birth day was saved. Only a
    date she gives is saved; a month and year becomes the 1st only on a clear yes."""
    body = _onboarding_skill_body()
    assert "never tell them to fill the screen in themselves" in body
    retirement = _onboarding_reference("retirement.md")
    assert "never send them to the screen to fill it in" in retirement
    assert "Birth date: only the one they give" in retirement
    assert "save it only on a clear yes" in retirement
    assert "store nothing" in retirement
    assert "simulate_retirement with no arguments" in retirement
    assert "Never fill a field with a guess or an estimate of your own" in retirement


def test_an_estimate_is_never_offered_for_saving(db_isolated):
    """Mai was offered "the true monthly spend is likely nearer SGD 1,400" saved to her profile."""
    prompt = build_system_prompt()
    assert "An ESTIMATE is not a stated value either" in prompt
    assert "never offer to save it" in prompt
    assert "never offer to store your own estimate" in _onboarding_skill_body()


def test_the_prompt_saves_only_a_birth_date_the_user_gives(db_isolated):
    """A month and year was saved as the 1st on implied consent: a day nobody gave."""
    prompt = build_system_prompt()
    assert "ask for their date of birth and save only a date they give" in prompt
    assert "save it only on a clear yes" in prompt


def test_the_prompt_writes_money_the_way_the_screens_do(db_isolated):
    """Lucius wrote "SGD 11,057" while every screen says "S$11,057"."""
    prompt = build_system_prompt()
    assert "CURRENCY NOTATION" in prompt
    assert "S$11,057" in prompt
    assert "never as an ISO code" in prompt


def test_a_comparison_question_gets_the_comparison_in_the_same_reply(db_isolated):
    """The owner asked "this month vs last month" and got totals, then "want me to compare?"."""
    prompt = build_system_prompt()
    assert "ANSWER THE WHOLE QUESTION" in prompt
    assert "in the same reply" in prompt
    assert 'Never stop at the totals and ask "want me to compare' in prompt


# ── Persona QA round 6, 2026-10-14 ──────────────────────────────────────────────────────────


def test_derived_figures_come_from_a_tool_not_from_the_models_head(db_isolated):
    """Lucius wrote "Everything else S$14,141" for 20,396 - 3,105 - 2,150 (it is 15,141)."""
    from backend.ai.tools._groups import CORE_TOOL_NAMES

    prompt = build_system_prompt()
    assert "DERIVED FIGURES" in prompt
    assert "Never do the arithmetic in your head" in prompt
    assert "calculate" in CORE_TOOL_NAMES


# ── Persona QA round 8, 2026-10-14 ──────────────────────────────────────────────────────────


def test_a_card_purchase_is_not_funded_twice(db_isolated):
    """After a S$12 card lunch Lucius told Tom to "put the extra S$12 from your next pay"; the
    category had already moved it into the card's payment envelope."""
    prompt = build_system_prompt()
    assert (
        "moves its amount from the category's envelope into the card's payment envelope" in prompt
    )
    assert "never tell the user to fund it again" in prompt
    assert "Only an OVERSPENT category needs covering" in prompt
    assert "ask once whether this month's has already been paid" in prompt
    budget = _onboarding_reference("budget.md")
    assert "Never tell them to fund it again" in budget
    assert "Only an overspent category needs covering" in budget
    assert "ask once whether this month's has already been paid" in budget


def test_a_statement_reconciles_only_when_it_ties_and_failures_are_plain(db_isolated):
    prompt = build_system_prompt()
    assert 'say a statement "reconciles" only when its balance ties to the cent' in prompt
    assert '"close, with a gap of S$47.24"' in prompt
    assert "the comparison tool would not read it" in prompt  # quoted as the thing NOT to say


def test_choices_are_one_sentence_not_a_bullet_list(db_isolated):
    """The first message listed five choices as bullets AND as buttons; people clicked the
    bullets."""
    prompt = build_system_prompt()
    assert "never also list them as bullets or numbers" in prompt
    assert "Pick one below, or tell me in your own words." in prompt
    body = _onboarding_skill_body()
    assert "ONE sentence, never a bullet or numbered list" in body
    assert "\n   - see where their money goes" not in body


def test_an_import_labels_income_too(db_isolated):
    """Mai's imported salary and interest stayed Uncategorized, so the briefing told her to tag
    them and Money flow showed "Uncategorized". A fresh book has no income category at all."""
    from backend.db.category_store import CategoryStore

    assert not [c for c in CategoryStore().all() if c.get("kind") == "income"]
    statements = _onboarding_reference("statements.md")
    assert "Income is part of that pass" in statements
    assert 'create "Salary" and "Interest" (kind `income`) with create_categories' in statements
