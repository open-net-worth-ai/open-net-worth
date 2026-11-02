"""``permissions_v2`` is declared as specified, and ``ai.permissions.engine()`` is its one switch.

The declaration is the contract in ``docs/launch/pr9-flag-design.md``: off by default, owned by
the owner, retired by 2027-01-31 (CI fails after that date until the old path is deleted).
"""

from __future__ import annotations

from datetime import date

import pytest

from backend.ai import permissions
from backend.core import feature_flags
from tests.flags import flags


def _declared() -> feature_flags.Flag:
    (flag,) = [f for f in feature_flags.FLAGS if f.name == "permissions_v2"]
    return flag


def test_the_flag_is_declared_as_specified():
    flag = _declared()

    assert flag.default is False
    assert flag.owner == "owner"
    assert flag.added == date(2026, 10, 31)
    assert flag.remove_by == date(2027, 1, 31)
    assert flag.kind == "release"
    assert flag.description.strip()


def test_the_flag_is_in_the_registry_and_is_declared_only_once():
    assert "permissions_v2" in feature_flags.REGISTRY
    assert [f.name for f in feature_flags.FLAGS].count("permissions_v2") == 1


def test_the_description_points_at_the_product_page():
    assert "Permissions v2" in _declared().description


@pytest.mark.flags(permissions_v2=False)
def test_with_the_flag_off_the_engine_is_the_old_gate():
    engine = permissions.engine()

    assert isinstance(engine, permissions.OldEngine)
    assert engine._gate.__name__ == "backend.ai.tools._confirm_gate"
    assert engine.policy.__name__ == "backend.ai._confirm_gate"
    assert engine.in_handler_cards is True
    assert engine.unattended_script_switch is True
    assert engine.automation_blocked_lists is False
    assert engine.labels_answered_cards is False


@pytest.mark.flags(permissions_v2=True)
def test_with_the_flag_on_the_engine_is_the_new_gate():
    engine = permissions.engine()

    assert isinstance(engine, permissions.NewEngine)
    assert engine._gate.__name__ == "backend.ai.tools._confirm_gate_v2"
    assert engine.policy.__name__ == "backend.ai._confirm_gate_v2"
    assert engine.in_handler_cards is False
    assert engine.unattended_script_switch is False
    assert engine.automation_blocked_lists is True
    assert engine.labels_answered_cards is True


def test_the_engine_follows_the_flag_each_time_it_is_asked():
    with flags(permissions_v2=True):
        assert isinstance(permissions.engine(), permissions.NewEngine)
    with flags(permissions_v2=False):
        assert isinstance(permissions.engine(), permissions.OldEngine)
    with flags(permissions_v2=True):
        assert isinstance(permissions.engine(), permissions.NewEngine)


def test_an_unnamed_gate_function_is_reached_through_the_engine():
    with flags(permissions_v2=False):
        old = permissions.engine()
        assert old.effective_level is old._gate.effective_level
    with flags(permissions_v2=True):
        new = permissions.engine()
        assert new.effective_level is new._gate.effective_level


def test_the_permission_api_follows_the_flag():
    from backend.ai.permission_api import PermissionApi
    from backend.ai.permission_api_v2 import PermissionApiV2

    with flags(permissions_v2=False):
        assert type(permissions.permission_api()) is PermissionApi
    with flags(permissions_v2=True):
        assert type(permissions.permission_api()) is PermissionApiV2


def test_the_old_gate_modules_know_nothing_of_the_new_ones():
    """The off path is main's code: it must not import the new gate or the new service."""
    import inspect

    from backend.ai import _confirm_gate
    from backend.ai.tools import _confirm_gate as tools_gate
    from backend.services import tool_permissions

    for module in (_confirm_gate, tools_gate, tool_permissions):
        source = inspect.getsource(module)
        assert "_confirm_gate_v2" not in source, module.__name__
        assert "tool_permissions_v2" not in source, module.__name__
