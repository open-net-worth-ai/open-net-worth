"""The context window starts where the fold ends and moves on token pressure (flag
``token_context_window``).

Each class guards one reason the old window counted messages, or one failure it had:

- ``TestCountIsNotSize`` — a chat of short messages never compacts just for being long.
- ``TestNoHole`` — the old window slid past the fold every ten messages after the first fold,
  dropping messages neither the window nor the summary held. Here start == fold end, always.
- ``TestCacheStability`` — the reason for ``_WINDOW_SLIDE_CHUNK``: the start must move rarely.
- ``TestRecentWorkStaysVerbatim`` — live incident 2026-08-09: an Automation's own tool calls
  pushed its "successful writes" report out of a 30-message window before the owner asked.
- ``TestToolPairsStayTogether`` — a cut never separates a tool call from its result.
- ``TestRewindNeverTrustsAStaleFold`` — a fold over rewound messages is not reused.
- ``TestFoldFailure`` — a failed summary never moves the start past what a summary covers.
- ``TestBudget`` — small and 1M-context models.
- ``TestSimulatedSessions`` — long random sessions replayed turn by turn, both policies.
"""

from __future__ import annotations

import asyncio
import random
from unittest.mock import patch

import pytest

from backend.ai import context_window as cw
from backend.ai.assistant import (
    MAX_HISTORY_MESSAGES,
    _compute_turn_prefix_state,
    _prepare_messages,
)

K = 1_000


def user(text: str = "hi", size: int = 0) -> dict:
    return {"role": "user", "content": text + "x" * size}


def assistant(text: str = "ok", size: int = 0) -> dict:
    return {"role": "assistant", "content": text + "x" * size}


def tool_call(tid: str, size: int = 0) -> list[dict]:
    """An assistant tool_use and the user tool_result that answers it."""
    return [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": tid, "name": "t", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tid, "content": "r" * size}],
        },
    ]


def chat(turns: int, user_size: int = 10, reply_size: int = 10, tools: int = 0, tool_size=0):
    h: list[dict] = []
    for t in range(turns):
        h.append(user(f"u{t}", user_size))
        for j in range(tools):
            h.extend(tool_call(f"t{t}_{j}", tool_size))
        h.append(assistant(f"a{t}", reply_size))
    return h


def plan(history, fold=None, *, window=200 * K, measured=None, turn_start=None):
    if turn_start is None:
        turn_start = len(history) - 1
    return cw.plan_window(
        history, fold, turn_start=turn_start, window_tokens=window, measured_prompt=measured
    )


def fold_at(history, idx, *, digest=True) -> dict:
    data = {"summary": "S", "folded_through_idx": idx, "verbatim_user_messages": []}
    if digest:
        data["boundary_digest"] = cw.boundary_digest(history, idx)
    return data


class TestCountIsNotSize:
    def test_two_hundred_short_messages_do_not_compact(self):
        history = chat(100)  # 200 messages; the old window folded at 70
        assert len(history) > MAX_HISTORY_MESSAGES + 10
        assert _compute_turn_prefix_state(history) > 0  # what the old path did
        p = plan(history)
        assert p.start == 0 and p.fold_to is None

    def test_a_few_huge_messages_do(self):
        history = chat(10, tools=1, tool_size=60 * K)  # ~150K tokens of tool output
        p = plan(history)
        assert p.prompt_tokens > p.budget.trigger
        assert p.fold_to is not None and p.fold_to > 0


class TestNoHole:
    def test_the_start_is_exactly_where_the_fold_ends(self):
        history = chat(60)
        p = plan(history, fold_at(history, 40))
        assert p.start == 40 and p.fold_to is None

    def test_old_path_has_the_hole_this_closes(self):
        """Characterises the flag-off bug: after a first fold at 10, the count window moves
        to 20 at 85 messages while the fold still ends at 10, and with low pressure no
        re-fold runs. Messages 10–19 reach the model in neither form."""
        history = chat(43)[:85]
        assert _compute_turn_prefix_state(history) == 20
        msgs = _prepare_messages(history, window_start=20, fold_summary="S", fold_verbatim=[])
        sent = " ".join(str(m["content"]) for m in msgs)
        assert "u5x" not in sent  # message 10 (turn 5's user message) is gone
        # The token window for the same conversation starts at the fold's end instead.
        assert plan(history, fold_at(history, 10)).start == 10


class TestCacheStability:
    def test_start_does_not_move_across_ordinary_turns(self):
        history = chat(30, tools=1, tool_size=2 * K)
        starts = set()
        for _ in range(40):
            history += [user("next", 50), assistant("reply", 400)]
            starts.add(plan(history).start)
        assert starts == {0}

    def test_after_a_fold_the_next_one_is_far_away(self):
        window = 200 * K
        history = chat(70, tools=2, tool_size=3 * K)
        p = plan(history, window=window)
        assert p.fold_to
        fold = fold_at(history, p.fold_to)
        after = cw.approx_tokens(history[p.fold_to :]) + cw.DEFAULT_OVERHEAD_TOKENS
        assert after <= p.budget.target + 10 * K  # landed near the target, not the trigger
        turns_until_next = 0
        while True:
            history += [user("q", 100), *tool_call(f"x{turns_until_next}", 3 * K), assistant("a")]
            turns_until_next += 1
            if plan(history, fold, window=window).fold_to:
                break
        assert turns_until_next >= 20  # the old window moved every 2.5 such turns


class TestRecentWorkStaysVerbatim:
    def test_the_last_exchanges_are_never_folded_however_large(self):
        """The 2026-08-09 shape: an Automation run of many big tool calls, then the owner
        asks about it. The run's report and its calls stay in the window."""
        history = chat(40, tools=1, tool_size=8 * K)
        run_start = len(history)
        history.append(user("run the import"))
        for j in range(30):
            history.extend(tool_call(f"r{j}", 8 * K))
        history.append(assistant("REPORT: 30 transactions added"))
        history.append(user("did you add the transfer?"))
        p = plan(history)
        assert p.fold_to is not None
        assert p.fold_to <= run_start  # the whole run and its report stay verbatim

    def test_pressure_from_the_protected_tail_alone_folds_nothing(self):
        """When the run itself is the bulk, folding the little before it frees nothing worth
        a cache break; the prompt goes out whole, as it would have before."""
        history = chat(3) + [user("run the import")]
        for j in range(30):
            history.extend(tool_call(f"r{j}", 16 * K))
        history += [assistant("REPORT"), user("and?")]
        assert plan(history).fold_to is None

    def test_the_last_twenty_messages_always_stay(self):
        history = chat(80, user_size=4 * K, reply_size=4 * K)
        p = plan(history)
        assert p.fold_to is not None
        assert p.fold_to <= len(history) - cw.MIN_TAIL_MESSAGES

    def test_this_turn_is_never_cut(self):
        history = chat(5, tools=1, tool_size=200 * K)
        turn_start = len(history) - 2
        p = plan(history, turn_start=turn_start)
        assert p.fold_to is None or p.fold_to <= turn_start


class TestToolPairsStayTogether:
    def test_every_cut_opens_on_a_real_user_message(self):
        history = chat(40, tools=3, tool_size=3 * K)
        p = plan(history)
        assert p.fold_to is not None
        assert cw.is_turn_boundary(history[p.fold_to])
        msgs = _prepare_messages(history, window_start=p.fold_to, fold_summary="S")
        ids = {
            b["id"]
            for m in msgs
            if isinstance(m["content"], list)
            for b in m["content"]
            if b.get("type") == "tool_use"
        }
        for m in msgs:
            if isinstance(m["content"], list):
                for b in m["content"]:
                    if b.get("type") == "tool_result":
                        assert b["tool_use_id"] in ids


class TestRewindNeverTrustsAStaleFold:
    def test_fold_past_the_end_is_ignored(self):
        history = chat(10)
        assert cw.trusted_start(fold_at(chat(40), 60), history) == 0

    def test_rewound_then_regrown_conversation_is_detected(self):
        original = chat(40)
        fold = fold_at(original, 50)
        rewound = original[:30] + chat(20)  # rewound to 30, then 40 new messages
        assert len(rewound) >= 50
        assert cw.trusted_start(fold, rewound) == 0

    def test_a_reload_adding_timestamps_and_ids_keeps_the_fold_trusted(self):
        history = chat(40)
        fold = fold_at(history, 50)
        reloaded = [
            {**m, "ts": 1_700_000_000_000 + i, "clientId": f"c{i}"} for i, m in enumerate(history)
        ]
        assert cw.trusted_start(fold, reloaded) == 50

    def test_a_fold_written_before_the_digest_existed_is_trusted_by_length(self):
        history = chat(40)
        assert cw.trusted_start(fold_at(history, 50, digest=False), history) == 50


class TestBudget:
    def test_one_million_window_is_capped(self):
        b = cw.budget_for(1_000_000)
        assert b.trigger == cw.TRIGGER_CAP_TOKENS and b.target == cw.TARGET_CAP_TOKENS

    def test_small_window_uses_fractions(self):
        b = cw.budget_for(32 * K)
        assert b.trigger == int(32 * K * 0.55) and b.target == int(32 * K * 0.30)

    def test_measured_overhead_counts(self):
        history = chat(20, tools=1, tool_size=4 * K)
        quiet = plan(history, measured=None)
        loud = plan(history, measured=cw.approx_tokens(history[:-1]) + 100 * K)
        assert loud.prompt_tokens > quiet.prompt_tokens


class TestFoldSteps:
    def test_a_long_range_folds_in_steps_on_turn_boundaries(self):
        history = chat(60, reply_size=20 * K)
        text = lambda ms: " ".join(str(m["content"]) for m in ms)  # noqa: E731
        steps = cw.fold_steps(history, 0, 100, text)
        assert steps[-1] == 100 and len(steps) > 1
        assert all(cw.is_turn_boundary(history[s]) for s in steps)
        assert steps == sorted(steps)


# ── The turn itself: fold, digest, failure ─────────────────────────────────────


class _Provider:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    async def stream_complete(self, prompt, **_kw):
        self.calls += 1
        if self.fail:
            raise RuntimeError("summary model down")
        yield {"type": "text_delta", "text": "SUMMARY"}


def _turn(history, *, fail=False, measured=None, fold=None, resumed=False):
    from backend.ai import assistant as a

    store: dict = {"fold": fold}
    provider = _Provider(fail)
    ctx = a.ModelTurnContext(
        model="m", provider_key="", transport="anthropic", base_url="", provider=provider
    )
    rows = (
        [{"model": "m", "peak_prompt_tokens": measured, "context_window": 200 * K}]
        if measured
        else []
    )
    out: dict = {}
    events: list = []

    async def go():
        async for ev in a._token_window_turn(
            "sid", history, len(history) - 1, "m", "", out, resumed=resumed
        ):
            events.append(ev)

    with (
        patch.object(a, "_load_fold", side_effect=lambda _s: store["fold"] and dict(store["fold"])),
        patch.object(a, "_save_fold", side_effect=lambda _s, d: store.__setitem__("fold", dict(d))),
        patch.object(a, "resolve_model_context", return_value=ctx),
        patch("backend.services.cost.session_chat_records", return_value=rows),
    ):
        asyncio.run(go())
    return out, events, store["fold"], provider


class TestTurn:
    def test_quiet_turn_makes_no_summary_call(self):
        out, events, fold, provider = _turn(chat(100))
        assert out["start"] == 0 and out["summary"] is None
        assert provider.calls == 0 and events == []

    def test_pressured_turn_folds_and_starts_where_the_fold_ends(self):
        history = chat(70, tools=2, tool_size=3 * K)
        out, events, fold, _ = _turn(history)
        assert events[0] == {"type": "compacting_start"}
        assert events[-1] == {"type": "compacting_done"}
        assert out["start"] == fold["folded_through_idx"] > 0
        assert fold["boundary_digest"] == cw.boundary_digest(history, out["start"])
        assert out["summary"] == "SUMMARY"

    def test_failed_summary_keeps_the_start(self):
        history = chat(70, tools=2, tool_size=3 * K)
        out, _, fold, provider = _turn(history, fail=True)
        assert provider.calls >= 1
        assert out["start"] == 0 and out["summary"] is None and fold is None

    def test_failed_summary_keeps_an_earlier_fold(self):
        history = chat(70, tools=2, tool_size=3 * K)
        earlier = fold_at(history, 4)
        out, _, fold, _ = _turn(history, fail=True, fold=earlier)
        assert out["start"] == 4 and out["summary"] == "S"


class TestReviewFindings:
    def test_a_resumed_turn_never_folds_and_keeps_its_window(self):
        """A turn continuing after an approval is the same logical turn: the last measured
        prompt already includes its rounds, so planning again would count them twice and
        move the window between rounds of one turn."""
        history = chat(70, tools=2, tool_size=3 * K)
        earlier = fold_at(history, 4)
        out, events, _, provider = _turn(history, fold=earlier, resumed=True)
        assert provider.calls == 0 and events == []
        assert out["start"] == 4 and out["summary"] == "S"

    def test_a_stale_fold_at_the_same_index_is_not_taken_for_a_failed_steps_work(self):
        history = chat(70, tools=2, tool_size=3 * K)
        step = plan(history).fold_to
        assert step
        stale = {
            "summary": "OTHER CONVERSATION",
            "folded_through_idx": step,
            "boundary_digest": "not-this-one",
        }
        out, _, fold, _ = _turn(history, fail=True, fold=stale)
        assert out["start"] == 0 and out["summary"] is None
        assert fold["boundary_digest"] == "not-this-one"  # never re-stamped as trusted


# ── Evaluation: long random sessions, both policies, turn by turn ──────────────


def _random_session(seed: int, turns: int) -> list[list[dict]]:
    rnd = random.Random(seed)
    out = []
    for t in range(turns):
        msgs = [user(f"u{t}", rnd.choice([5, 20, 200, 2 * K]))]
        for j in range(rnd.choice([0, 0, 1, 2, 4, 8])):
            msgs += tool_call(f"t{t}_{j}", rnd.choice([200, 2 * K, 8 * K, 30 * K]))
        msgs.append(assistant(f"a{t}", rnd.choice([100, 1 * K, 3 * K])))
        out.append(msgs)
    return out


def _replay(turns, *, window, fail_rate=0.0, seed=0):
    rnd = random.Random(seed)
    history: list[dict] = []
    fold = None
    moves = folds = 0
    worst_prompt = 0
    last_start = 0
    for msgs in turns:
        turn_start = len(history)
        history += msgs[:1]
        p = plan(history, fold, window=window, turn_start=turn_start)
        start = p.start
        if p.fold_to:
            folds += 1
            if rnd.random() >= fail_rate:
                fold = fold_at(history, p.fold_to)
                start = p.fold_to
        # The invariant: everything before the window is covered by the fold.
        assert start == cw.trusted_start(fold, history)
        moves += start != last_start
        last_start = start
        history += msgs[1:]
        worst_prompt = max(
            worst_prompt, cw.approx_tokens(history[start:]) + cw.DEFAULT_OVERHEAD_TOKENS
        )
    return {"moves": moves, "folds": folds, "worst": worst_prompt, "len": len(history)}


def _old_moves(turns) -> tuple[int, int]:
    history: list[dict] = []
    last = moves = holes = 0
    folded = 0
    for msgs in turns:
        history += msgs
        start = _compute_turn_prefix_state(history)
        if start != last:
            moves += 1
            if folded == 0:
                folded = start  # first fold is unconditional; later ones need pressure
            holes += start > folded
        last = start
    return moves, holes


class TestSimulatedSessions:
    @pytest.mark.parametrize("seed", range(12))
    @pytest.mark.parametrize("window", [32 * K, 200 * K, 1_000_000])
    def test_no_hole_and_rare_moves(self, seed, window):
        turns = _random_session(seed, 120)
        new = _replay(turns, window=window, seed=seed)
        old_moves, _ = _old_moves(turns)
        assert new["moves"] <= old_moves
        if window >= 200 * K:
            # Over the trigger only while the tail floor itself is that big.
            assert new["worst"] <= cw.budget_for(window).trigger + 80 * K

    @pytest.mark.parametrize("seed", range(8))
    def test_folds_that_fail_never_open_a_hole(self, seed):
        _replay(_random_session(seed, 120), window=200 * K, fail_rate=0.4, seed=seed)

    def test_the_old_policy_does_open_holes_on_short_message_chats(self):
        _, holes = _old_moves([[user("q"), assistant("a")] for _ in range(80)])
        assert holes > 0
