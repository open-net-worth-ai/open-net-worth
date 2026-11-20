"""``core.unattended`` must stay ONE fact with ONE reader.

Its predecessor (``ai.tools._skill_scope.is_unattended``) started as this exact fact and
grew a second job: a tool-AUTHORIZATION ceiling checked on every dispatch. Because an
Automation's granted-tool list is empty until the owner approves something, that ceiling
refused every tool on every run — a scheduled job fetched mail, was refused on everything
after, and reported the refusals as a "configuration issue" (live, 2026-08-10).

Deleting the flag fixed the ceiling and silently killed the fact too, which ``run_script``'s
unsupervised-execution gate depends on. So it is back, deliberately narrow. These tests are
the narrowness.
"""

from __future__ import annotations

import ast
import pathlib

from backend.core.unattended import is_unattended, reset_unattended, set_unattended

_BACKEND = pathlib.Path(__file__).resolve().parents[1] / "backend"


def test_it_defaults_to_a_human_being_present():
    """A missing scope must never read as 'unsupervised' — that would silently unlock the
    code-execution gate for ordinary interactive chat."""
    assert is_unattended() is False


def test_set_and_reset_round_trip():
    token = set_unattended(True)
    try:
        assert is_unattended() is True
    finally:
        reset_unattended(token)
    assert is_unattended() is False


def test_exactly_one_production_reader():
    """THE guard. A second reader is the ceiling growing back.

    If you are adding one, the question to answer first is whether you actually mean "is
    anyone there?" — or "may this caller reach this tool?", which is a permission question
    and must not be answered from here. That conflation is the whole reason this file exists.
    """
    # Parsed, not grepped: the name appears in a lot of PROSE explaining the 2026-08-10
    # incident, and a comment is not a reader.
    readers = set()
    for path in _BACKEND.rglob("*.py"):
        if path.name == "unattended.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "is_unattended"
            ):
                readers.add(path.relative_to(_BACKEND).as_posix())
    assert readers == {"ai/tools/scripts.py"}, (
        f"core.unattended must have exactly one production reader; found {sorted(readers)}"
    )
