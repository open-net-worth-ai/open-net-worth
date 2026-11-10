"""The SHIPPED feature-flag registry: well-formed, and no flag outlives its ``remove_by``.

This is the remove_by lint (docs/FEATURE_FLAGS.md): when it fails, retire the flag — one PR
that deletes the old code path, the flag's declaration and its both-ways test runs — or, if the
owner agrees it needs longer, move ``remove_by`` in a reviewed PR that says why.
"""

from __future__ import annotations

from backend.core import feature_flags as ff
from backend.core.user_time import user_today


def test_the_shipped_registry_is_well_formed():
    ff.build_registry(ff.FLAGS)


def test_no_shipped_flag_is_past_its_remove_by():
    overdue = ff.expired(user_today(), list(ff.FLAGS))
    assert not overdue, "retire these flags (docs/FEATURE_FLAGS.md § Retiring): " + ", ".join(
        f"{f.name} (remove_by {f.remove_by}, owner {f.owner})" for f in overdue
    )
