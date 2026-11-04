---
title: Trading workspaces own execution and run-scoped agent authority
status: Accepted
date: 2026-11-04
deciders: Codex implementation team under the owner's issue 62 implementation request
---

# Trading workspaces own execution and run-scoped agent authority

## Context

[Issue #62](https://github.com/open-net-worth-ai/open-net-worth/issues/62) introduces autonomous
external money movement, portable across backtest, paper and live modes and independent of
the participant. Existing tool permissions determine whether an ordinary tool can execute;
they do not represent a funded account mandate. The operator-wide inbound MCP bearer is too
broad to hand to a contestant. The current script subprocess explicitly has same-user host
filesystem access. A local checkpoint restore cannot reverse an order at a broker.

## Decision

We keep a trading domain inside the application with one normalized execution choke for
all participants and modes. A run freezes environment, account bindings and mandate; agents
cannot promote a simulation to live through a tool argument. Ordinary tool permissions
remain unchanged. Only a trading-bound turn has a restricted tool contract and mandatory
run/account validation. External access uses revocable run-scoped credentials, whose hashes
live in a separate secret table. The gateway never accepts the operator bearer as a run grant.

Scripts execute in a pinned Docker image without host mounts, networking, privileges or
inherited credentials, with bounded resources and actual container termination. Parent-side
services validate returned order intents before execution. The generic script runner's
existing behavior is unchanged.

Trading submission identities, fills, grant revocations and operational history are
checkpoint-excluded and non-restorable. Live recovery reconciles broker facts before new
orders; booking fills uses the existing real-book chokes. Paper and backtest accounts use
separate trading records. Provider-specific MCP schemas are normalized behind reviewed
adapters; the retired Alpaca order tools are not restored.

## Consequences

### Positive

Agents share a stable environment contract. Neither scripts nor external participants need
broker credentials. Rewinding an ordinary conversation cannot resurrect a revoked grant or
forget a sent order. Unknown submissions retain their identity for reconciliation.

### Negative

Each live adapter must prove account/order/fill identity and match declared capabilities.
Managed live execution is unavailable for incompatible accounts or incomplete receipts.
Contained script execution requires a provisioned compatible Docker runtime/image; ordinary
application installation alone is insufficient. Historical LLM results may still reflect
knowledge in model training, even when tool data is correctly isolated.

### Follow-ups

Map provider, broker and UI evidence separately to issue #62. Require actual funded-account
authorization before real trades. Keep future broker importers on the same canonical fill
identity rather than deduplicating by price/quantity similarity.

## Alternatives considered

- Compose raw broker MCP calls in ordinary automations: lacks a single order/account authority
  and a common historical/paper/live contract.
- Give an external agent the operator bearer: exposes unrelated personal data and write tools.
- Treat a prompt or tool offer as the mandate boundary: does not validate an actual dispatch.
- Run autonomous code in the existing same-user subprocess: permits host-data and credential access.
- Restore local execution history with the book: risks sending an already-submitted order again.

## Agent provenance

- **Decided by:** Codex orchestrator with backend/runtime implementers and UI designer.
- **Lane / branch:** `feat/issue-62-trading-workspace`.
- **Alternatives weighed:** raw automations, participant-specific execution, shared operator authority,
  and isolated normalized execution.
- **Driving artifacts:** issue #62 and `.Codex/rfcs/autonomous-trading-workspace.md`.
- **Why this way (one line):** Account authority and execution evidence must remain with the
  environment when agent implementations and execution modes change.
