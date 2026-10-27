# Feature flags

A flag lets a feature that is big enough to need one ship dark and be switched on by the owner.
**Off means exactly the old behaviour, running the old code.** Decision and reasoning: ADR
[`2026-10-25-feature-flags-off-means-the-old-code`](adr/2026-10-25-feature-flags-off-means-the-old-code.md).

Most changes need no flag. Reach for one when a change is large or risky enough that the owner
wants to merge it before turning it on.

## The rules

1. **One switch point.** The flag picks between two complete code paths at one seam. No `if flag`
   scattered through code; the old path stays untouched.
2. **Additive data only** while the flag lives — new columns/tables/rows the old code ignores. No
   rename, delete or conversion until the flag is retired. On → off → on must be safe.
3. **Both paths tested.** The old tests stay as they are and prove the off path; the same tests
   (plus new ones) run with the flag on.
4. **Owner, reason, `remove_by`.** CI fails once a flag is past `remove_by`.
5. **Can't meet these? No flag** — and the PR says so.

## Declare a flag

Add one entry to `FLAGS` in [`backend/core/feature_flags.py`](../backend/core/feature_flags.py) —
the only place a flag is defined:

```python
FLAGS: tuple[Flag, ...] = (
    Flag(
        name="new_rollover",                       # snake_case; ONW_FLAG_NEW_ROLLOVER in env
        description="Carries overspending into next month the new way.",  # shown to the owner
        default=False,
        owner="owner",
        added=date(2026, 10, 25),
        remove_by=date(2026, 12, 31),
        kind="release",                            # or "kill_switch": an off-ramp for something live
    ),
)
```

It appears in Settings → Developer → Experimental automatically.

## Check it — at one seam

```python
from backend.services.feature_flags import enabled

rollover = new_rollover if enabled("new_rollover") else old_rollover   # the ONE switch point
```

```tsx
const on = useFlag('new_rollover')          // frontend/src/hooks/useFlag.ts
if (on === undefined) return <Skeleton />   // not loaded yet — never guess true
return on ? <NewRollover /> : <OldRollover />
```

Call `enabled` from `services` or `ai`. A router never branches on a flag — it calls one
service, and the seam lives there; code below `services` takes the choice from its caller.
`enabled` reads the database, so call it once at the seam, not in a loop. An unknown name
raises (`UnknownFeatureFlagError`; `useFlag` throws). `useFlag` never paints from a previous
session: the flag list is excluded from the query cache's localStorage mirror.

**Resolution, first match wins:** test override → public demo (always the default) →
`ONW_FLAG_<NAME>=1|0|true|false` → the owner's stored choice → the declared default. Any other
`ONW_FLAG_*` value is logged and ignored, so one bad line cannot take every flag down.

Only the signed-in owner changes a flag (`PUT /api/feature-flags/{name}`, `{"enabled": true |
false | null}`, `null` = reset). No Lucius tool may write one — `tests/test_feature_flags_guard.py`
fails if anything in `backend/` outside the flag implementation can reach a writer.

## Test both ways

Backend (helpers in [`tests/flags.py`](../tests/flags.py)):

```python
@pytest.mark.flag_both("new_rollover")         # runs [new_rollover=off] and [new_rollover=on]
def test_rta_identity_closes(...): ...

@pytest.mark.flags(new_rollover=True)          # one test, flag on
def test_new_only_behaviour(...): ...

def test_mixed(...):
    with flags(new_rollover=True): ...         # inside a test body
```

Run the existing suite for the area with the flag on — the unchanged run already proves off:

```bash
pytest tests/test_planning*.py --flag new_rollover=on
```

Add that second run to CI (`.github/workflows`) for as long as the flag lives. `ONW_FLAG_*` from
your shell is cleared for every test, so a run is reproducible.

Frontend (vitest):

```ts
import { overrideFlagsForTests } from '../hooks/useFlag'

describe.each([false, true])('rollover (new_rollover=%s)', (on) => {
  let restore: () => void
  beforeEach(() => (restore = overrideFlagsForTests({ new_rollover: on })))
  afterEach(() => restore())
  // the same assertions, both ways
})
```

## Retire it

One cleanup PR, before `remove_by`:

1. delete the old path and make the new one unconditional (or delete the new one if it lost);
2. delete the `Flag` entry and the both-ways CI run, and drop `flag_both` / `flags` markers;
3. only now, any destructive migration the new path was waiting for.

A stored value for a retired flag is ignored by every reader. If the flag needs longer, move
`remove_by` in a reviewed PR that says why.
