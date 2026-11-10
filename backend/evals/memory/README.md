# Memory benchmark

A live, opt-in gate for the memory model: it drives the real extract / sort / import / tidy passes
and two chat turns against synthetic data in a throwaway book and scores the resulting state.

```bash
make memory-eval-mock                    # zero cost, CI-safe
make memory-eval-mock PERSONA=sloppy     # must fail the gate
make memory-eval REPEATS=3               # live; spends the chosen model's quota
```

What it measures, the bar, the report and how to add a case: [`docs/ai/MEMORY_EVAL.md`](../../../docs/ai/MEMORY_EVAL.md).

| File | Role |
|---|---|
| `bootstrap.py` | isolates the data root (`ONW_ROOT` + `ONW_QA_MODE=1`) before any backend import |
| `spec.py`, `cases*.py` | the case table — data only |
| `checks*.py` | check builders: state in, `None` or the observed value out |
| `runner.py` | seeds the scratch book, runs the real pass, scores it |
| `scorer.py`, `report.py` | aggregation across repeats, the gate, the terminal report |
| `mock_replies.py`, `mock_import.py` | canned `good` / `sloppy` replies for `--mock` |
