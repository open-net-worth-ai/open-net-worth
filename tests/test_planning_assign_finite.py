"""``assign`` refuses a non-finite figure (it would otherwise be stored as the month's amount)."""

import pytest

from backend.core.errors import ValidationError
from backend.services import planning


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_assign_refuses_non_finite_amounts(db_isolated, bad):
    env = planning.apply_budget_change({"type": "envelope_create", "name": "Food"})["envelope"]
    with pytest.raises(ValidationError, match="finite"):
        planning.apply_budget_change(
            {"type": "assign", "month": "2026-01", "envelope_id": env["id"], "amount": bad}
        )
