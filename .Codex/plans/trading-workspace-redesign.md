# Trading workspace implementation plan

The product is a persistent workspace accepting a long-lived trading mandate. The user
describes it to Lucius or enters an expert form, reviews one contract, starts it, and follows
the executor's autonomous work. Any single agent, team, coding CLI or external tool-using
executor may participate through its supported adapter. No fixed strategy is required.

The actual page keeps original mandate/amendments and current work above two equally
visible regions: Market & portfolio and Agent work. Financial records link to actual
decisions, market inputs available then, tools and code/file versions. Missing information
remains visibly missing; provider summaries do not become invented full reasoning.

The owner cancelled the standalone HTML prototype and requested direct implementation,
testing/review and the actual page forwarded to MacBook. No prototype will be delivered.

## Work sequence

| Step | Implementation | Required evidence |
| --- | --- | --- |
| 1 | Archive paused work with clear unfinished status. | Commit pushed to the draft PR branch; financial/privacy checks recorded. |
| 2 | Shared conversational/form review and creation, immutable original mandate and amendments, current workspace deployment. | Both entrances create the same validated contract; past orders retain their authority. |
| 3 | Persistent executor lifecycle, scoped research/files/code, durable safe trace, automatic wake/recovery through existing turns/scheduler. | Real provider and independent executor; unattended continuation, pause/revoke and recovery without duplicate orders. |
| 4 | Actual OHLCV/provenance and as-of data, statements and linked evidence projections. | Valid bars, no future-data leakage, consistent cash/position/P&L/fee identity and explicit missing links. |
| 5 | Connected two-region UI, mandate/status, files/tool outputs, inspectors and candlestick Trading icon. | Browser interaction, responsive layout, stale/error/absence states and specialist review. |
| 6 | Carry common Backtest/Paper/Live and stock/crypto/multi-account/currency contracts through the redesign; complete approved venue integrations. | Authentic provider/venue evidence, deterministic replay and financial/reconciliation safeguards; separately authorized real-account scope and limits before real orders. |
| 7 | Comparable workspace evaluation and full end-to-end acceptance. | Common-window/data/assumptions disclosed; agent costs separate; owner review of actual MacBook page. |

These steps preserve the complete Issue #62 scope. They do not make funded venue validation
optional, or turn a screenshot or passing unit tests into acceptance of continuous autonomy.
Report implemented and still-blocked criteria separately.

Reuse Automations' conversation-to-unsaved-draft review, existing server-owned turns/scheduler,
chat/tool/artifact renderers, chart renderer, query cache/events and financial write chokes.
Do not create a second chat/scheduler/monitor stack. All supplied UI text is English.

References: `.Codex/rfcs/autonomous-trading-workspace-v2.md`,
[Issue #62](https://github.com/open-net-worth-ai/open-net-worth/issues/62),
[draft PR #63](https://github.com/open-net-worth-ai/open-net-worth/pull/63).

## Review checkpoint

The actual frontend replaces the run-first workflow; no standalone HTML was generated.
The shared creation/review, persistent mandate and native work capture, agent-selected market
subscriptions, candles/statements/evidence and responsive observation layers are implemented.
Trading now uses the existing candlestick icon. Real Codex acceptance separately covers
natural-language unsaved review, two automatic decision cycles with persistent files and an
actual isolated strategy/fill, and an independent HTTP MCP participant.

Browser QA found and fixed a shared-cache bug: disabled queries were being refreshed during
successful writes, causing a completed amendment to report an error. The fix preserves active
subscribers, handles synchronous read errors and has regression coverage. Other review fixes
bind activation to the reviewed revision, preserve multi-order evidence and retry identities,
reuse unchanged file versions, retain legacy quote metadata and recover orphaned leases.

The delivery report/PR records final check results. Full-suite baseline failures and archived
unfinished broker work remain separate from the passing Trading checks. Funded Binance/IBKR
execution and the owner's review are still required before calling the entire Issue #62 done.
