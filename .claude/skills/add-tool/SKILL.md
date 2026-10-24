---
name: add-tool
description: How to add a new AI/chat tool to the Lucius tool registry (backend/ai/tools/). Use when adding, editing, or removing a tool the assistant can call — anything that becomes a TOOL_SCHEMAS entry or an execute_tool branch.
---

# Add an AI tool

AI tools live in the registry package `backend/ai/tools/` (RFC `.claude/rfcs/phase1/b1-tools.md`). The old 1,454-line `execute_tool` if-chain is gone — a tool is now **one `@tool`-decorated async function**. Do NOT add a tool any other way.

## The contract (preserve exactly)

A handler is `async def handler(input_data: dict) -> Tuple[str, Optional[dict]]`:
- **element 0** — a JSON string (the result text fed back to the model). Use `json.dumps(...)`.
- **element 1** — a `proposed_change` dict, **non-None ONLY** for the three `propose_position_*` tools (frontend/Telegram render a confirm card from it). Every other tool returns `None` here.
- The dispatcher (`registry.dispatch`) adds **no** try/except. Per-tool error behavior is your choice and must be deliberate: most tools let exceptions raise (assistant.py is the global catcher and converts a raise into `{"error": str(exc)}`); a few catch internally and return `{"ok": False, "error": ...}` (e.g. `add_transaction`, `calculate`). Pick one consistently with the group's siblings.

## Steps

1. **Pick the group module** in `backend/ai/tools/` (`portfolio`, `prices`, `profile`, `accounts`, `ledger`, `categories`, `statements`, `checkpoints`, `watchlist`, `memory`, `email_ingest`, `commitments`, `misc`). Add to the closest existing one; only create a new module for a genuinely new domain.
2. **Write the decorated handler.** Schema text (`description`, `input_schema`) goes verbatim in the decorator kwargs — co-located with the handler, which is the whole point (kills schema/handler drift):

   ```python
   from backend.ai.tools.registry import tool

   @tool(
       "my_tool",
       description="One clear sentence on when the model should call this.",
       input_schema={
           "type": "object",
           "properties": {"foo": {"type": "string", "description": "..."}},
           "required": ["foo"],
       },
   )
   async def my_tool(input_data: dict):
       foo = input_data["foo"]
       result = svc.do_something(foo)        # call a service, never recompute money math here
       return json.dumps({"result": result}), None
   ```

3. **Classify it (the sweep in `tests/test_tool_domains.py` fails otherwise).** The tag decides whether it asks by default: `mutates=("domain",)` if it changes something of the owner's (it asks — unless EVERY domain it writes is one of Lucius's own workspace domains in `_confirm_gate.WORKSPACE_DOMAINS` (`memory skills artifacts notification_log`), which seeds Always allow; a mix with any other domain still asks); `external_effect=True` on the decorator if it acts outside the owner's data — sends something out, runs code on their machine — which seeds Ask whatever it writes (never add a tool name to a list); `refreshes=("domain",)` if it is a READ that merely makes a screen refresh — a last-synced stamp, a cached/memoized result (it runs; adding one to the reviewed set in the test is a deliberate decision); neither if it writes nothing (add it to `READ_ONLY_TOOLS`, or `NO_DOMAIN_WRITE_TOOLS` if it sends something out). A `connector=` tag says WHOSE stored level applies, never what an undecided call does. The owner can set any tool to Needs approval or Blocked regardless.
4. **Respect the dependency rule.** A tool head validates input → calls **one service** (`backend/services/*`) → shapes JSON. No net-worth / P&L / FX math in the handler. `ai → services → db → core` only. (Some legacy handlers still lazily `import backend.api.x` inside the body — that is a known wart being unwound, not a pattern to copy; prefer a service.)
5. **If you create a new module**, add it to the import list in `backend/ai/tools/__init__.py` so its `@tool` registrations fire on package import.
6. **Register call-time names if monkeypatched.** Characterization tests do `monkeypatch.setattr(backend.ai.tools, "read_rows", …)`. If your handler reads such a name, import it via the package namespace (`import backend.ai.tools as _root; _root.read_rows()`) and re-export it from `__init__.py` / `_shared.py` — see the existing `read_rows`, `_NAMES_CACHE`, `read_actions` re-exports.
7. **Duplicate names raise** at import (`tool()` guards `_REGISTRY`). The name must be unique across all groups.

## Tests (required — see the `write-test` skill)

- The contract test `tests/test_char_tool_dispatch.py` snapshots the **set** of tool names and the proposed_change discipline. Adding a tool changes that set — update the snapshot intentionally and add a behavioral test for your tool (seed stores in `tmp_path`, drive with `asyncio.run(execute_tool("my_tool", {...}))`, assert on the parsed JSON).
- Network/LLM-touching tools (`web_search`, `fetch_page`, `scan_email_now`) get at most an `@pytest.mark.integration` smoke test — never a unit test that hits the wire.

## Docs

Add the tool to the "AI / chat tools" list in `CLAUDE.md` (and `FEATURES.md` if user-facing) in the same commit.

## Verify

`make lint && make test` (the fast gate). Backend import smoke (NEVER a server) — see the `run-app` skill.
