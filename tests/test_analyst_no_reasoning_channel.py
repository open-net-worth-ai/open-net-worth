"""The analyst's structured output never asks the model for its hidden reasoning.

Until 2026-11-19 its first field was `analysis`, described as a place to "think it through …
not shown to the user". With thinking on, Claude refused every such request as
`[reasoning_extraction]`, so the analyst failed ~1.5k rows a day (live-probed: 3/3 refused with
that field, 4/4 answered with a plain `assessment`). The leading field stays — it steadies the
JSON and gives a non-thinking model a "look first" step — but it is a visible assessment.
"""

from __future__ import annotations

import json
import re

from backend.ai.tools.schema_from_model import schema_of
from backend.monitoring import analyst

_REASONING_CHANNEL = re.compile(
    r"think (it )?through|not shown to the user|hidden (thinking|reasoning)|scratch ?pad|"
    r"chain of thought|your reasoning",
    re.IGNORECASE,
)


def _schema() -> dict:
    return schema_of(analyst._AnalysisOutput, drop_docstrings=True)


def test_the_leading_field_is_a_plain_assessment():
    props = list(_schema()["properties"])
    assert props[0] == "assessment"
    assert "analysis" not in props


def test_no_field_description_frames_a_hidden_reasoning_channel():
    text = json.dumps(_schema())
    assert not _REASONING_CHANNEL.search(text), _REASONING_CHANNEL.search(text)


def test_the_prompt_never_asks_for_hidden_reasoning():
    for want_key_issues in (False, True):
        for is_held in (False, True):
            skeleton = analyst._output_skeleton(want_key_issues, is_held)
            assert skeleton.startswith('{"assessment": ')
            assert not _REASONING_CHANNEL.search(skeleton)
    row = {
        "id": "pan_t",
        "subject": "asset:inst-acme",
        "ticker": "ACME",
        "signal_id": "news-inst-acme",
        "trigger": "news_spike",
        "payload": {"label": "ACME Corp", "asset_class": "equity"},
    }
    evidence = [{"title": "ACME raises guidance", "source": "Example", "url": "", "snippet": ""}]
    user, system = analyst._build_prompts(row, evidence, is_held=True, want_key_issues=True)
    for prompt in (user, system):
        assert '"analysis"' not in prompt
        assert not _REASONING_CHANNEL.search(prompt), _REASONING_CHANNEL.search(prompt)
