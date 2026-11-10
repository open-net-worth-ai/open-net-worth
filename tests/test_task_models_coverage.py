"""Every routable task must be pinnable — from the API and from the UI.

Scar: the owner pinned every task the Settings panel offered
to a Claude Code CLI model, then watched the logs show
``complete_done source=news_screen model=z-ai/glm-5.2`` and concluded their choice was being
ignored. It wasn't. ``news_screen`` is a real, separately-routable task that had a field in
``TaskModelsUpdate`` but NO row in the panel — so it stayed unpinned and fell back to the
instance default. Six more tasks (price_monitor, csv_schema, the statement_* stages) had
neither, and were unpinnable by any means.

The failure is silent by construction: Pydantic drops an undeclared field without error, and
a task with no UI row simply never appears. Nothing anywhere reports the gap — only a log
line naming a model the owner thought they had replaced. Hence these tests: the three lists
(store / API / UI) must never drift apart again.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.api.models import TaskModelsUpdate
from backend.db.model_config_store import VALID_TASKS

# The Task models card Settings actually renders. This used to check
# `settings/TaskModelsPanel.tsx`, a pre-redesign copy nothing mounted, which let a new task
# ship with no visible row ("title", 2026-09-28); that copy has since been deleted.
_PANEL = Path(__file__).resolve().parents[1] / (
    "frontend/src/components/tabs/settings/sections/ModelSection.tsx"
)


def _ui_task_keys() -> set[str]:
    source = _PANEL.read_text(encoding="utf-8")
    groups = source[source.index("const TASK_GROUPS") : source.index("const TASK_ROWS")]
    # Each row is a `['key', 'Label', 'help', vision?]` tuple; a group header is
    # `['Group', [` — its name is followed by a list, never by a label string.
    return set(re.findall(r"\[\s*'(\w+)',\s*'", groups))


# A task that is routable and storable always, but whose Settings row exists only while its flag is
# on (the Auto-approval judge: nothing reads the pin with ``permissions_v2`` off). The store and
# the API accept it either way - the pin is additive data - so the flag-off panel is main's panel.
_FLAG_GATED_TASKS = {"approval": "permissions_v2"}


def _ui_flag_gated_task_keys() -> set[str]:
    source = _PANEL.read_text(encoding="utf-8")
    block = source[source.index("const TASK_GROUPS_V2") : source.index("const TASK_ROWS_V2")]
    return set(re.findall(r"\[\s*'(\w+)',\s*'", block))


def test_every_routable_task_is_settable_via_the_api():
    """A task the store can route but the request body cannot name is silently undroppable
    into the config — it can only ever run on the instance default."""
    missing = VALID_TASKS - set(TaskModelsUpdate.model_fields)
    assert not missing, (
        f"tasks routable by ModelConfigStore but absent from TaskModelsUpdate: {sorted(missing)}. "
        "Pydantic drops them without error, so they can never be pinned."
    )


def test_the_api_invents_no_task_the_store_cannot_route():
    """The reverse drift: a field here that set_task_models would reject as unknown."""
    extra = set(TaskModelsUpdate.model_fields) - VALID_TASKS
    assert not extra, f"TaskModelsUpdate names tasks that are not in VALID_TASKS: {sorted(extra)}"


def test_every_routable_task_has_a_row_in_the_settings_panel():
    """An API-settable task with no UI control is unreachable for anyone not writing curl —
    which is how news_screen kept running on the old provider."""
    missing = VALID_TASKS - _ui_task_keys() - set(_FLAG_GATED_TASKS)
    assert not missing, (
        f"tasks with no row in Settings → Task models (sections/ModelSection.tsx): {sorted(missing)}. "
        "They fall back to the instance default with nothing in the UI to say so."
    )


def test_a_flag_gated_task_has_its_row_only_behind_its_flag():
    """With the flag off the panel is exactly main's (no row for the task); with it on the row is
    there. The task itself stays routable either way."""
    assert set(_FLAG_GATED_TASKS) <= VALID_TASKS
    assert not set(_FLAG_GATED_TASKS) & _ui_task_keys()
    assert set(_FLAG_GATED_TASKS) <= _ui_flag_gated_task_keys()


def test_news_screen_specifically_is_pinnable():
    """The exact reported case, pinned as its own regression."""
    assert "news_screen" in VALID_TASKS
    assert "news_screen" in TaskModelsUpdate.model_fields
    assert "news_screen" in _ui_task_keys()


def test_analysis_specifically_is_pinnable():
    """The 2026-08-25 case, pinned as its own regression: `analyst._analysis_model()` always
    called `task_model_override("analysis")`, but "analysis" was never added to VALID_TASKS —
    since that read never validates against the set, it silently, permanently returned None,
    and every Analyst push-synthesis call ran on the instance default (measured: `claude_cli`
    with reasoning on, 32%/42% structured-output failure rates in live testing/production)
    with no field or UI row to ever let anyone route it elsewhere."""
    assert "analysis" in VALID_TASKS
    assert "analysis" in TaskModelsUpdate.model_fields
    assert "analysis" in _ui_task_keys()


def test_a_pinned_task_actually_round_trips(tmp_path, monkeypatch):
    """End to end through the store: what the API accepts must come back out resolvable."""
    from backend.db.model_config_store import ModelConfigStore

    for key in (
        "AI_PROVIDER",
        "AI_MODEL",
        "ONW_TEST_DEFAULT_MODEL",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
    ):
        monkeypatch.delenv(key, raising=False)
    # model_config is DB-backed now (Wave 5) — conftest's autouse Tier-2 table clear
    # already isolates it per test, no path kwarg needed.
    store = ModelConfigStore()
    store.save({"provider": "anthropic", "model": "claude-sonnet-4-6"})

    store.set_task_models({t: f"anthropic:model-for-{t}" for t in sorted(VALID_TASKS)})

    for task in VALID_TASKS:
        assert store.task_model_override(task) == f"anthropic:model-for-{task}", task
