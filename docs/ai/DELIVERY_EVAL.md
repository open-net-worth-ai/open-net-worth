# Delivery-decider eval

Does a model decide **now / later / skip** for Intel notifications well enough to ship, and
how much quieter is it than the old pipeline? `python3 -m backend.evals.delivery` answers both
by driving the REAL decider (`backend/monitoring/delivery_decider.decide`) — the same prompt,
model call and code rails the app runs.

**Run it before opening a PR that changes the decider's prompt, its rails, or the model it
uses.** It spends the chosen model's quota and is never part of `make check`.

## Two parts

1. **Synthetic benchmark** (always runs). Fourteen hand-labelled ticks with made-up tickers
   (`backend/evals/delivery/cases.py`): a takeover bid, a CEO exit, a guidance cut, a price alert
   you set, a war at 3am — and the noise: target changes, a move with no cause, fund flows,
   three oil ticks on a known situation, a repeat of something already told, an instruction to
   ignore upgrades. A case may list an acceptable alternative where two calls are defensible.
   **Gate:** accuracy ≥ `--min-accuracy` (80%) AND zero *missed* (a "now" decided "skip").
2. **History replay** (`--history CLONE_DATA_DIR`). Rebuilds what the person was actually
   pushed over `--days` (default 14) from a QA clone of their book — story push logs, price-alert
   fires, watch-alert logs (`history.py`; a lower bound: push logs keep the last five per story)
   — and replays it tick by tick through the decider (`replay.py`), with the told record, the
   waiting pile and roundup timing evolving as they would live. A stronger model (`--judge`,
   default Opus) labels every past push a day at a time; that reference scores both the old
   pipeline (which pushed everything) and each decider model.

## Run it

```bash
python3 -m backend.evals.delivery --mock                         # zero cost: harness + scorer
python3 -m backend.evals.delivery --models claude_cli:haiku,claude_cli:sonnet
python3 -m backend.scripts.clone_data_for_qa --dest /tmp/onw-clone   # a clone, never the live book
python3 -m backend.evals.delivery --models claude_cli:sonnet \
    --history /tmp/onw-clone --days 14 --out /tmp/delivery-eval
```

Exit codes: **0** the synthetic gate passed for every model, **1** it failed.

## What it reports

| | Meaning |
|---|---|
| `before` | messages the old pipeline sent: total, per day, worst day, between 11pm and 7am |
| `after` | messages the decider sends: pushes (`now`) + roundups, per day, night pushes, items held, skipped, expired |
| `missed` | reference "now", decider "skip": an interruption the person needed never came |
| `delayed` | reference "now", decider "later": they heard it in the next roundup |
| `noise` | reference "skip", decider "now": an interruption they didn't need |
| `old_pipeline_quality` | the same scores for the old pipeline, whose every message was a push |

## Isolation and privacy

Synthetic runs use a throwaway `ONW_ROOT`; history runs point the backend at the CLONE you name
(it must contain `onw.db` and must not be the live tree) with `ONW_QA_MODE=1`. `--out` refuses a
path inside the repo: a history report holds the person's real headlines and holdings.
