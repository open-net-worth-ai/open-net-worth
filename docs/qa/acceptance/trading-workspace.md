# Trading workspace acceptance

Use a synthetic, isolated QA instance on ports 8090–8099. Never use the personal book or a live funded account for these UI checks. A successful UI action is confirmed by returned backend state and persisted records, not a toast alone. Real provider and broker acceptance remain separate evidence.

## TRD-01 — Configure a workspace and run

- GIVEN the Trading destination has no workspaces, WHEN it opens, THEN the empty-state action opens the shared workspace modal.
- WHEN a name, instructions, participant and limits are saved, THEN the workspace opens with those values retained after reload.
- WHEN a run is created with a mode, explicit period and two account allocations, THEN it appears in the run selector with immutable mode and account bindings.
- WHEN Backtest and Load synthetic example are selected, THEN the dataset is explicitly labeled synthetic; its stock and crypto observations are submitted to the backend.
- WHEN the server rejects a configuration, THEN the error is visible and all draft fields remain editable.
- WHEN Live is selected, THEN explicit human authorization and dedicated broker account configuration are required; a refused start is never presented as running.

## TRD-02 — Operate a run

- GIVEN a ready run, WHEN Start run is clicked, THEN the frozen mandate is reviewed through confirmation and the backend moves the run to running.
- WHEN Pause, Resume or Stop run is confirmed, THEN the returned state is reflected without changing mode; the text describes cancellation and retained holdings accurately.
- GIVEN an unknown broker submission, THEN it is labeled Outcome unknown and no retry-order shortcut is offered.
- GIVEN an open order, WHEN cancellation is confirmed, THEN the request is sent once; pending cancellation and actual fills remain distinct.
- GIVEN a run needing reconciliation, THEN Start/Resume is unavailable and the reconciliation status/action is visible.

## TRD-03 — Inspect decisions and financial results

- GIVEN activity and script results, WHEN Activity or Artifacts is opened, THEN timestamps, summaries and bounded detail/source are inspectable; previous/next pages retain access to older records.
- WHEN an order detail opens, THEN requested quantity, status, broker identity and actual partial fills/fees are distinguishable.
- WHEN Holdings opens, THEN simulated positions appear only in the trading portfolio and values are backend projections in explicit currencies.
- WHEN a valuation is incomplete, THEN equity/return display an unavailable state rather than zero; model cost is visibly separate from trading P&L.
- WHEN a fetch fails, THEN a retry action appears and any prior data remains visible with a stale/error indication.

## TRD-04 — Agent, script and external access

- GIVEN a running Lucius run, WHEN Run agent now executes a decision cycle, THEN its outcome, decisions, usage and orders can be inspected in Activity/Orders.
- WHEN strategy source executes, THEN the source and result are recorded and returned intents pass through the same order service; an isolation failure remains a visible error.
- WHEN an external credential is issued, THEN only the run-bound endpoint and scoped credential are shown, once; the credential never enters query cache or browser persistence.
- WHEN the credential dialog closes (including during an issue request), THEN the credential is discarded; reopening cannot reveal it again.
- WHEN Revoke run access is clicked, THEN server-side revocation succeeds and the credential cannot perform further gateway operations.

## TRD-05 — Compare participants

- GIVEN two runs sharing frozen conditions, WHEN both are selected into a named comparison, THEN results show backend return, drawdown, fees and separately reported model cost.
- WHEN participants share the same agent label, THEN each row remains distinguishable by run identity, mode, period and strategy version, with a link to that exact run.
- WHEN the comparison opens, THEN shared capital, reporting currency, universe, dataset and execution assumptions are visible alongside the backend-recorded equity series for each participant.
- GIVEN different scenarios, WHEN a comparison is attempted, THEN the server refusal is shown and no fabricated ranking appears.
- WHEN a participant result is opened, THEN the selected workspace/run is encoded in the URL and survives refresh/back navigation.

## TRD-06 — Layout and accessibility

- At 640, 1024 and 1440 widths, with chat open/closed and sidebar expanded/collapsed, controls remain usable and tables scroll inside their panel.
- With the collapsed Lucius pill present, scrolling to the end leaves the final order actions and holdings values reachable above it.
- Modals use the shared modal primitive, retain focus containment, support Escape, and clear the Lucius dock/panel.
- Labels identify every configuration field; page/run tabs support keyboard selection; status has text in addition to color.
- Empty, loading, rejected, disconnected/stale, and populated states each have captured synthetic-data evidence.

## Automated evidence

`frontend/src/components/tabs/trading/Trading.interactions.test.tsx` covers workspace creation/refusal, multi-account synthetic backtest configuration, live authorization/refusal, cancellation confirmation, unknown submission behavior, one-time credentials, and isolated-script errors. These component tests do not establish browser layout, actual provider decisions, broker execution, or live acceptance.
