---
title: IBKR Flex retired for IBKR's own hosted MCP server — hidden everywhere, code kept
status: Accepted
date: 2026-10-29
deciders: repo owner (product decision) + implementing agent (Claude)
---

# IBKR Flex retired for IBKR's own hosted MCP server

## Context

The built-in `ibkr` connector imports Interactive Brokers activity through the Flex Web
Service: a Flex token plus a hand-built Activity Flex Query, which must be set to XML. It is a
daily batch report, so figures arrive about a day behind, and it is read-only.

Interactive Brokers now publishes its own hosted MCP server
(`https://api.ibkr.com/v1/api/mcp-public`, on its AI-integrations page and in its connector
v1.1.5 release notes), added to the directory as the Verified `mcp-ibkr` entry. It signs in over
OAuth on IBKR's own page, reads live positions, balances, trades and P&L, and can place orders
the user approves. Offering both put two "Interactive Brokers" connectors side by side and made
new users choose the worse one's setup (a Flex Query) without knowing.

## Decision

Retire IBKR Flex the way Yodlee was hidden (`2026-08-14-hide-yodlee-enterprise-only-pricing`):
a hide, not a removal.

1. `disabled=True` on the `ibkr` kind in `services/connector_catalog.py` — so
   `ai.tools._connector_gate.DISABLED_CONNECTORS` hides `ibkr_get_status` /
   `ibkr_fetch_activity` from discovery unconditionally and `registry.dispatch` refuses them;
   `PROVIDER_KEYS` (derived from the same flag) drops it; `get_connector_status` neither reports
   nor accepts it.
2. Lucius-facing text no longer names it: the connector-id examples in `ai/tools/connectors.py`
   and the onboarding skill's Flex-Query XML reminder are gone.
3. The frontend directory entry moves to `catalog.RETIRED_CATALOG` and the sync template to
   `automationTemplates.RETIRED_SYNC_TEMPLATES` — kept, never shown — so the card, Automate
   button and template gallery no longer offer it. Its logo stays mapped: a connector a user
   already set up still lists with it, so they can see and remove it.
4. The product docs say it is no longer offered and point to the MCP server.

## Consequences

- Re-enabling is flipping `disabled` back and moving the two kept entries back into their lists.
- The `ibkr` store, API, service, section component and their tests are untouched.
- An existing IBKR Flex setup keeps its row but Lucius can no longer fetch it, exactly as with
  Yodlee; there is no automatic migration of its accounts to the MCP server.
