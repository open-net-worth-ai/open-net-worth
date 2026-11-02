"""Feature-flag helpers for tests (docs/FEATURE_FLAGS.md § Test both ways).

Four ways to choose a flag's value in a test, most specific first:

- ``@pytest.mark.flag_both("name")`` — the test runs twice, ``[name=off]`` and ``[name=on]``;
- ``with flags(name=True):`` — inside a test body;
- ``@pytest.mark.flags(name=True)`` — for the whole test;
- ``pytest --flag name=on`` — for the whole run. Once a real flag ships, CI re-runs its
  affected test files this way, so the unchanged old tests keep proving the off path and the
  same tests prove the on one.

Every one goes through ``core.feature_flags.override``, which beats every other source.
``ONW_FLAG_*`` from the developer's shell is cleared for each test, so a run is reproducible.
"""

from __future__ import annotations

from datetime import date

from backend.core.feature_flags import Flag, override

flags = override

# A test-only declaration, merged into the registry for the test session only — it is not in the
# shipped registry (``core.feature_flags.FLAGS``). Its far ``remove_by`` is deliberate: it
# never ships, so the remove_by lint (which checks ``FLAGS``) does not apply to it.
EXAMPLE_FLAG = Flag(
    name="example_flag",
    description="Test-only example: shows the mechanism end to end. Never shipped.",
    default=False,
    owner="tests",
    added=date(2026, 10, 25),
    remove_by=date(2099, 12, 31),
)

TEST_FLAGS: tuple[Flag, ...] = (EXAMPLE_FLAG,)


def parse_cli(values: list[str]) -> dict[str, bool]:
    """``["a=on", "b=off"]`` → ``{"a": True, "b": False}`` for the ``--flag`` option."""
    out: dict[str, bool] = {}
    for item in values:
        name, sep, raw = item.partition("=")
        state = raw.strip().lower()
        if not sep or state not in {"on", "off", "1", "0", "true", "false"}:
            raise ValueError(f"--flag {item!r}: expected NAME=on or NAME=off")
        out[name.strip()] = state in {"on", "1", "true"}
    return out
