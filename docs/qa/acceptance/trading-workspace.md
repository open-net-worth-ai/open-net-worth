# Trading workspace acceptance

Use a synthetic, isolated QA instance on ports 8090–8099. Never use the personal book or a live funded account for these UI checks. A successful UI action is confirmed by returned backend state and persisted records, not a toast alone. Real provider and broker acceptance remain separate evidence.

## TRD-01 — Review one persistent delegation

- GIVEN Trading, WHEN Create with Lucius is selected, THEN the existing conversation opens with delegation intent; an unsaved validated draft reaches the same review as Expert setup through trading.review_draft.
- WHEN a name, original mandate, executor, environment, period, allocation and limits are reviewed, THEN no workspace exists until the user confirms; a single confirmation creates and starts the delegated environment.
- WHEN the workspace reloads, THEN original instructions and current environment remain visible without requiring a second run-creation form.
- WHEN supplied Backtest bars are entered, THEN source/version and availability remain explicit and future bars are absent until the environment reaches them.
- WHEN the server rejects a configuration, THEN the error is visible and all draft fields remain editable.
- WHEN Live is selected, THEN explicit human authorization and dedicated broker account configuration are required; a refused start is never presented as running.

## TRD-02 — Control authority without rewriting history

- GIVEN a reviewed workspace, WHEN Start workspace, Pause, Resume or End workspace is confirmed, THEN the returned persistent state is reflected without changing its allocation/mode; cancellation and retained holdings are described accurately.
- WHEN paused work is amended, THEN the change is reviewed, its reason/before/after/effective revision persist, the original instruction remains exact, and prior order authority remains unchanged.
- GIVEN missing original history on a legacy workspace, THEN Not recorded is shown rather than a fabricated original or permanent loading state.
- GIVEN an unknown broker submission, THEN it is labeled Outcome unknown and no retry-order shortcut is offered.
- GIVEN an open order, WHEN cancellation is confirmed, THEN the request is sent once; pending cancellation and actual fills remain distinct.
- GIVEN a run needing reconciliation, THEN Start/Resume is unavailable and the reconciliation status/action is visible.

## TRD-03 — Inspect decisions and financial results

- GIVEN recorded work, THEN Market & portfolio and Agent work have equal prominence; native input/output, tool args/results, source provenance, files/versions, reported reasoning and explicit absences are inspectable. Large safe records have complete retrieval.
- WHEN a fill marker or decision is selected, THEN the same inspector links actual decision identity, data available then and recorded file/tool versions; absent links are not guessed.
- WHEN valid OHLCV exists, THEN candles/volume render from those bars; quote-only data remains explicitly quote-only.
- WHEN an order detail opens, THEN requested quantity, status, broker identity and actual partial fills/fees are distinguishable.
- WHEN Holdings opens, THEN simulated positions appear only in the trading portfolio and values are backend projections in explicit currencies.
- WHEN a valuation is incomplete, THEN equity/return display an unavailable state rather than zero; model cost is visibly separate from trading P&L.
- WHEN a fetch fails, THEN a retry action appears and any prior data remains visible with a stale/error indication.

## TRD-04 — Agent, script and external access

- GIVEN a running managed workspace, THEN the scheduler continues work and advances its environment automatically. Normal operation requires neither Run agent now nor Advance clicks.
- Real-provider acceptance must cover research/file persistence, actual isolated code/order execution, subsequent automatic wake/fill, same-session file reading, settled cost and no duplicate orders. A mock or a single provider cycle does not meet this criterion.
- WHEN strategy source executes, THEN the source and result are recorded and returned intents pass through the same order service; an isolation failure remains a visible error.
- WHEN an external credential is issued, THEN only the run-bound endpoint and scoped credential are shown, once; the credential never enters query cache or browser persistence.
- WHEN the credential dialog closes (including during an issue request), THEN the credential is discarded; reopening cannot reveal it again.
- WHEN Revoke run access is clicked, THEN server-side revocation succeeds and the credential cannot perform further gateway operations.

## TRD-05 — Compare participants

- GIVEN two runs sharing frozen conditions, WHEN both are selected into a named comparison, THEN results show backend return, drawdown, fees and separately reported model cost.
- WHEN participants share the same agent label, THEN each row remains distinguishable by run identity, mode, period and strategy version, with a link to that exact run.
- WHEN the comparison opens, THEN shared capital, reporting currency, universe, dataset and execution assumptions are visible alongside the backend-recorded equity series for each participant; later mandate changes invalidate an obsolete shared-scenario claim.
- GIVEN different scenarios, WHEN a comparison is attempted, THEN the server refusal is shown and no fabricated ranking appears.
- WHEN a participant result is opened, THEN the selected workspace/run is encoded in the URL and survives refresh/back navigation.

## TRD-06 — Layout and accessibility

- At 640, 1024 and 1440 widths, with chat open/closed and sidebar expanded/collapsed, controls remain usable and tables scroll inside their panel.
- With the collapsed Lucius pill present, scrolling to the end leaves the final order actions and holdings values reachable above it.
- Modals use the shared modal primitive, retain focus containment, support Escape, and clear the Lucius dock/panel.
- Labels identify every configuration field; page/run tabs support keyboard selection; status has text in addition to color.
- Empty, loading, rejected, disconnected/stale, and populated states each have captured synthetic-data evidence.
- Trading uses a candlestick icon, distinct from Investments' trend arrow; supplied product text is English.

## Automated evidence

`frontend/src/components/tabs/trading/Trading.interactions.test.tsx` covers shared delegation review, errors and execution controls. The attested `TRD-WORKSPACE` fixture runs `frontend/qa/trading-workspace.test.mjs` against the built app, checking persisted authority, actual scoped gateway evidence, automatic simulation fills, statements and responsive layers. The opt-in `tests/cli_live/test_trading_delegation_live.py` separately exercises two real-provider scheduler cycles. None establishes authenticated funded broker acceptance or the owner's final UI sign-off.
