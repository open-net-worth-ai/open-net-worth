---
title: Every unloaded tool is a bare name in the catalog, MCP and REST included
status: Accepted
date: 2026-11-11
deciders: repo owner + orchestrating agent (Claude)
---

# 2026-11-11. Every unloaded tool is a bare name in the catalog, MCP and REST included

## Context

Lucius's system prompt lists every tool he has not loaded (`ai/tools/catalog.py`). Internal
tools were already bare names in one flat list. External MCP tools were not: a small server got a
heading and a one-line summary per tool, a large one collapsed to a single roster line, and only
40 tool lines and 20 servers were listed at all. REST API tools were briefly given the same
layout. Claude Code, with the same discovery problem, lists every unloaded tool, MCP included,
as a bare name and loads a schema by search when it needs one.

## Decision

- Every tool Lucius has not loaded is a bare name in the catalog's one list: internal, REST API
  and external MCP alike. No per-server section, no summary, no roster line, no cap on servers or
  tools.
- A schema is loaded when it is needed, by `find_tools` or the discovery gate, as before.
- A server-chosen name is still cleaned before it enters the prompt (`_safe_external_name`:
  control characters, markup, whitespace and commas removed, 80 characters at most), so it can
  neither start a line of its own nor pass for a second name.
- MCP server instructions are unchanged: they are not part of the tool list.

## Consequences

**Positive**

- One layout for every tool, the one Claude Code uses.
- Server-written summaries no longer ride in every turn's cached prompt.
- Lucius always knows every tool's name; nothing is behind a search before he can call it.

**Negative**

- A server or API with very many tools adds a name each to every turn (about 5 tokens a name).
  The owner chose this over a cap: a cap is a difference from Claude Code.
- A cryptic tool name gets no hint until it is loaded; `find_tools` searches descriptions.

## Agent provenance

- **Agent:** Claude, in a session with the repo owner, who asked for the layout to match Claude
  Code and declined a safety cap.
- **Verified:** `tests/test_tool_catalog.py`, `tests/test_rest_connector.py` and the prompt,
  catalog, discovery and MCP suites.
