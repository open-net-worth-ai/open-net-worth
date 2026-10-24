"""A level set on a NATIVE connector actually gates that connector's tools.

The defect these pin (2026-08-14): the permission ladder shipped for every connector
family, the REST routes accepted a level for any connector id, and the store saved it —
but ``get_tool_permission`` had exactly ONE production caller, ``mcp/client_manager``. So
setting ``plaid_sync_now`` to Blocked stored a row and changed nothing. Meanwhile a SECOND
store (``tool_grants``, binary) was what actually gated native tools, and the two could
disagree with no way for the user to tell which had won.

Both halves are fixed by one merge (ADR ``2026-08-14-one-permission-store-for-every-tool``):
one store, one dispatch-time read (``_confirm_gate.effective_level``), consumed once in
``registry.dispatch``.

Every test here drives the REAL ``registry.dispatch``. A predicate that returns the right
answer while nothing consumes it is precisely the bug being fixed, so nothing below asserts
against the predicate alone.
"""

from __future__ import annotations

import json

import pytest

from backend.ai.tools import _confirm_gate as gate
from backend.ai.tools import registry
from backend.services import tool_permissions as perms
from tests.auto_judge_support import stub_judge

# A native, connector-tagged tool: `@tool(connector="plaid")` in ai/tools/connectors.py.
# Deliberately a READ tool — it runs by default, but a level the owner stored gates it exactly
# like a write, and using one keeps these tests off the write path the old grant store
# already covered.
NATIVE_WRITE = "telegram_send_message"
NATIVE_TOOL = "plaid_get_status"
NATIVE_OWNER = "plaid"

# An UNTAGGED tool that writes — owned by the reserved `core` bucket. Its behaviour must be
# byte-identical to pre-merge, which is what `test_an_untagged_tool_is_unaffected` pins.
UNTAGGED_WRITE = "create_payees"
# An untagged READ tool. Nothing gates it, before or after.
UNTAGGED_READ = "get_accounts"


@pytest.fixture()
def armed(db_isolated, monkeypatch):
    """Gate armed, card-capable surface, EMPTY permission table.

    Every call site constructs ``ToolPermissionStore()`` fresh with ``engine=None`` (never
    cached), so ``db_isolated``'s engine redirect alone gives an isolated, empty table.
    """
    monkeypatch.setattr("backend.core.cost_context.get_cost_surface", lambda: "web")
    monkeypatch.setattr("backend.core.chat_session.get_session_id", lambda: "sess-perm")


_next_id = iter(f"toolu_perm_{i}" for i in range(1, 100_000))


async def _dispatch(name, args=None, tool_use_id=None):
    return await registry.dispatch(name, args or {}, tool_use_id=tool_use_id or next(_next_id))


# ── One level per outcome, on a NATIVE tool ───────────────────────────────────


@pytest.mark.asyncio
async def test_blocked_refuses_before_the_handler_runs(armed):
    """``blocked`` is a REFUSAL, not a prompt: nothing is staged and nothing executes."""
    perms.set_tool_permission(NATIVE_OWNER, NATIVE_TOOL, "blocked")

    text, proposed = await _dispatch(NATIVE_TOOL)

    assert proposed is None, "a blocked call must not raise a card — there is nothing to tap"
    body = json.loads(text)
    assert body["blocked_by"] == "tool_permission"
    assert body["tool"] == NATIVE_TOOL
    # The refusal names the permission as the reason and the surface that changes it, so the
    # model can explain it instead of confabulating a capability gap or routing around it.
    assert "BLOCKED by tool permissions" in body["error"]
    assert "Permissions" in body["error"]


@pytest.mark.asyncio
async def test_needs_approval_produces_a_confirm_card_and_runs_nothing(armed):
    """The DEFAULT for a connector tool, and what an explicit ``needs_approval`` stores.

    A PAUSE, not a fake result: ``text is None`` is what tells the turn loop to leave this
    tool_use result-less rather than inventing a placeholder (RFC 2026-07-22 §4.2).
    """
    perms.set_tool_permission(NATIVE_OWNER, NATIVE_TOOL, "needs_approval")

    text, proposed = await _dispatch(NATIVE_TOOL, tool_use_id="toolu_ask")

    assert text is None
    assert proposed["type"] == "tool_permission"
    assert proposed["grant_tool"] == NATIVE_TOOL
    assert proposed["tool_use_id"] == "toolu_ask"


@pytest.mark.asyncio
async def test_an_undecided_native_read_runs_by_default(armed):
    """No row at all — the default follows what the tool does, and a connector's read is a
    read: enabling the connector was the access grant, so it does not ask again."""
    assert perms.stored_tool_level(NATIVE_OWNER, NATIVE_TOOL) is None

    text, proposed = await _dispatch(NATIVE_TOOL, tool_use_id="toolu_default")

    assert proposed is None
    assert text is not None and "blocked_by" not in text


@pytest.mark.asyncio
async def test_an_undecided_native_write_asks_by_default(armed):
    """The same rule from the other side: a connector tool that SENDS asks, with no row."""
    assert perms.stored_tool_level("telegram", NATIVE_WRITE) is None

    text, proposed = await _dispatch(
        NATIVE_WRITE, {"message": "hi"}, tool_use_id="toolu_default_write"
    )

    assert text is None
    assert proposed["grant_tool"] == NATIVE_WRITE


@pytest.mark.asyncio
async def test_always_allow_fires_the_handler(armed):
    """``always_allow`` runs the real tool — no card, no pause."""
    perms.set_tool_permission(NATIVE_OWNER, NATIVE_TOOL, "always_allow")

    text, proposed = await _dispatch(NATIVE_TOOL)

    assert proposed is None
    # The handler really ran: a status read answers with its own payload, and a Plaid that
    # is not configured self-reports rather than being blocked by us.
    assert text is not None and json.loads(text)
    assert "blocked_by" not in text


@pytest.mark.asyncio
async def test_always_allow_fires_unattended_too(armed, monkeypatch):
    """``always_allow`` means allow EVERYWHERE — it is never silently degraded to a prompt
    because nobody is watching (``CLAUDE.md``, Security-critical areas). A surface that
    cannot render a card is exactly where that degradation would hide."""
    perms.set_tool_permission(NATIVE_OWNER, NATIVE_TOOL, "always_allow")
    monkeypatch.setattr("backend.core.cost_context.get_cost_surface", lambda: "scheduled")

    text, proposed = await _dispatch(NATIVE_TOOL)

    assert proposed is None
    assert text is not None and "blocked_by" not in text


@pytest.mark.asyncio
async def test_blocked_refuses_unattended_too(armed, monkeypatch):
    """The mirror of the rule above: a refusal is not softened by the absence of a human."""
    perms.set_tool_permission(NATIVE_OWNER, NATIVE_TOOL, "blocked")
    monkeypatch.setattr("backend.core.cost_context.get_cost_surface", lambda: "scheduled")

    text, _ = await _dispatch(NATIVE_TOOL)

    assert json.loads(text)["blocked_by"] == "tool_permission"


# ── The decision cannot leak between providers ────────────────────────────────


@pytest.mark.asyncio
async def test_a_level_on_one_provider_does_not_reach_its_sibling(armed):
    """``ai/tools/connectors.py`` records the incident: a single shared ``connector_sync``
    name meant one NAME-keyed grant, so "always allow Plaid syncs" would silently have
    allowed IBKR too. Per-provider tools plus the (owner, tool) key make that impossible —
    this pins that the merge kept the property: a stored ``needs_approval`` on Plaid's status
    read does not make IBKR's sibling read ask."""
    perms.set_tool_permission("plaid", "plaid_get_status", "needs_approval")

    assert perms.stored_tool_level("ibkr", "ibkr_get_status") is None
    text, proposed = await _dispatch("ibkr_get_status", tool_use_id="toolu_sibling")
    assert proposed is None and text is not None, "IBKR's status inherited Plaid's permission"


# ── Untagged tools are UNAFFECTED ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_untagged_read_tool_is_unaffected(armed):
    """A tool with no ``connector=`` tag has no connector to inherit a level from, and the
    merge must not invent a default that changes it. A read runs, as it always has —
    applying the ladder's ``needs_approval`` default here would card every read in the app.
    """
    assert gate.effective_level(UNTAGGED_READ) == "always_allow"

    text, proposed = await _dispatch(UNTAGGED_READ)

    assert proposed is None
    assert text is not None and "blocked_by" not in text


@pytest.mark.asyncio
async def test_an_untagged_write_tool_still_asks_exactly_as_before(armed):
    """The approval model's own verdict, unchanged: an unscoped write confirms until the
    owner says otherwise. This is the behaviour the absorbed ``tool_grants`` produced with
    an empty table, reproduced by an empty permission table."""
    text, proposed = await _dispatch(UNTAGGED_WRITE, {"name": "Acme"}, tool_use_id="toolu_core")

    assert text is None
    assert proposed["grant_tool"] == UNTAGGED_WRITE


@pytest.mark.asyncio
async def test_an_untagged_write_fires_once_the_owner_allows_it(armed):
    """The absorbed grant, in its new spelling: an ``always_allow`` row under ``core`` is
    exactly what a ``scope: "always"`` grant used to be, and it skips the card the same way.
    """
    perms.set_tool_permission(perms.CORE_CONNECTOR_ID, UNTAGGED_WRITE, "always_allow")

    text, proposed = await _dispatch(UNTAGGED_WRITE, {"name": "Acme"})

    assert proposed is None
    assert json.loads(text).get("ok") is not False


@pytest.mark.asyncio
async def test_an_untagged_tool_can_also_be_blocked(armed):
    """``blocked`` is the tier the binary grant store could not express at all — the
    capability the merge ADDS for unscoped tools rather than takes away."""
    perms.set_tool_permission(perms.CORE_CONNECTOR_ID, UNTAGGED_WRITE, "blocked")

    text, _ = await _dispatch(UNTAGGED_WRITE, {"name": "Acme"})

    assert json.loads(text)["blocked_by"] == "tool_permission"


# ── Blocked between the card and the tap ──────────────────────────────────────


@pytest.mark.asyncio
async def test_a_tool_blocked_while_its_card_waited_is_refused_on_redemption(armed):
    """Mirrors the external-MCP apply path, which re-reads the level before firing its
    STORED args. An approval that sat on screen while the owner changed their mind must not
    fire on the old verdict."""
    perms.set_tool_permission(NATIVE_OWNER, NATIVE_TOOL, "needs_approval")
    text, proposed = await _dispatch(NATIVE_TOOL, tool_use_id="toolu_race")
    assert text is None and proposed is not None

    perms.set_tool_permission(NATIVE_OWNER, NATIVE_TOOL, "blocked")

    # The server's own redemption call — never the model's.
    text, proposed = await registry.dispatch(
        NATIVE_TOOL, {gate.CONFIRM_TOKEN_KEY: "toolu_race"}, tool_use_id="toolu_race"
    )

    assert proposed is None
    assert json.loads(text)["blocked_by"] == "tool_permission"


# ── The dispatch sites that must consult the ladder ───────────────────────────


def test_every_dispatch_site_consults_the_permission_store():
    """The reworked descendant of ``test_get_tool_permission_has_exactly_one_production_
    caller``.

    That test pinned a GAP (one caller: external MCP) so it could not silently change. The
    gap is now closed, and what has to be pinned instead is the closure: this app has
    exactly TWO dispatch paths, and BOTH read the permission store.

      * internal tools -> ``ai/tools/registry.dispatch`` -> ``_confirm_gate.effective_level``
      * external MCP   -> ``mcp/client_manager``

    A THIRD reader appearing is not automatically wrong — but it is a new place a permission
    decision is interpreted, and it must be reviewed rather than merged unnoticed. That is
    the same protection the original test gave, aimed at the invariant that now holds.

    Parsed rather than grepped: these names appear in prose in several docstrings, and a
    mention is not a caller. Both call shapes count — ``perms.get_tool_permission(...)`` and
    a bare ``stored_tool_level(...)`` off a direct import are the same read.
    """
    import ast
    import pathlib

    backend = pathlib.Path(__file__).resolve().parents[1] / "backend"
    reads = {"get_tool_permission", "stored_tool_level", "yolo_enabled"}
    # Not dispatch sites, and each excluded for a stated reason rather than because it was
    # inconvenient. Keyed on the RELATIVE path: `api/tool_permissions.py` and
    # `services/tool_permissions.py` share a basename, and excluding the API layer by
    # accident would blind this test to the layer most likely to grow a second answer.
    not_a_dispatch_site = {
        "services/tool_permissions.py",  # DEFINES these reads
        "db/tool_permission_store.py",  # the storage underneath them
        "api/tool_permissions.py",  # REST: reads to DISPLAY and to write, never to decide
        # `_decorate`'s `_level` helper: the Connectors permission LADDER's displayed
        # default for an undecided tool (security review finding #4 / ADR
        # `2026-08-17-connector-ladder-shows-the-real-effective-default`) — reads
        # `yolo_enabled()` once per `list_connectors()` call to show what dispatch will
        # ACTUALLY do, since `services` cannot import `ai.tools._confirm_gate.
        # effective_level` itself (the dependency rule). Reads to DISPLAY, same shape as
        # `api/tool_permissions.py` above, never to decide whether a call fires.
        "services/connectors.py",
    }
    callers: dict[str, set[str]] = {}
    for path in backend.rglob("*.py"):
        rel = str(path.relative_to(backend))
        if rel in not_a_dispatch_site:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else node.func.id
                if isinstance(node.func, ast.Name)
                else None
            )
            if name in reads:
                callers.setdefault(rel, set()).add(name)

    assert set(callers) == {
        "ai/tools/_confirm_gate.py",
        "mcp/client_manager.py",
    }, (
        "the permission store is read somewhere new. Every dispatch site MUST consult it, "
        "and every consulting site must be a dispatch site — review this addition rather "
        f"than widening the assertion. Found: {sorted(callers)}"
    )


def test_the_reserved_core_bucket_can_never_collide_with_a_real_connector(db_isolated):
    """``core`` is a reserved OWNER id, not a connector. If a real connector ever claimed
    it, that connector's decisions and every unscoped tool's would share a namespace."""
    from backend.services.connectors import list_connectors

    ids = {c.id for c in list_connectors()}
    assert perms.CORE_CONNECTOR_ID not in ids


# ── End to end: the REST route the UI uses actually gates the tool ────────────


@pytest.mark.asyncio
async def test_a_level_set_through_the_connector_route_gates_a_native_tool(armed):
    """The whole claim, driven through the surface a user actually touches.

    ``PUT /api/connectors/{id}/permissions/{tool}`` is what the Connectors tab's ladder
    calls. Before the merge this route stored a row and changed nothing for a native
    connector; here the same request is followed by a real dispatch, and the dispatch obeys.
    """
    from fastapi.testclient import TestClient

    from backend.main import app

    with TestClient(app) as client:
        assert (
            client.put(
                f"/api/connectors/{NATIVE_OWNER}/permissions/{NATIVE_TOOL}",
                json={"level": "blocked"},
            ).status_code
            == 200
        )
        text, proposed = await _dispatch(NATIVE_TOOL)
        assert proposed is None
        assert json.loads(text)["blocked_by"] == "tool_permission"

        assert (
            client.put(
                f"/api/connectors/{NATIVE_OWNER}/permissions/{NATIVE_TOOL}",
                json={"level": "always_allow"},
            ).status_code
            == 200
        )
        text, proposed = await _dispatch(NATIVE_TOOL)
        assert proposed is None and "blocked_by" not in text

        assert (
            client.put(
                f"/api/connectors/{NATIVE_OWNER}/permissions/{NATIVE_TOOL}",
                json={"level": "needs_approval"},
            ).status_code
            == 200
        )
        text, proposed = await _dispatch(NATIVE_TOOL, tool_use_id="toolu_e2e_asks")
        assert text is None and proposed["grant_tool"] == NATIVE_TOOL

        # Clearing returns the read to its DEFAULT, which is to run.
        assert client.delete(f"/api/connectors/{NATIVE_OWNER}/permissions").status_code == 200
        text, proposed = await _dispatch(NATIVE_TOOL, tool_use_id="toolu_e2e_cleared")
        assert proposed is None and "blocked_by" not in text


@pytest.mark.asyncio
async def test_a_level_set_through_the_app_wide_route_gates_an_untagged_tool(armed):
    """The other surface — Settings › Permissions — writing the SAME store. The owner is
    resolved server-side, so a client that knows only the tool name still lands the row
    where the gate will look for it."""
    from fastapi.testclient import TestClient

    from backend.main import app

    with TestClient(app) as client:
        body = client.put(
            f"/api/tool-permissions/{UNTAGGED_WRITE}", json={"level": "blocked"}
        ).json()
        row = next(r for r in body["permissions"] if r["tool"] == UNTAGGED_WRITE)
        assert row["connector_id"] == perms.CORE_CONNECTOR_ID
        assert row["level"] == "blocked"

        text, _ = await _dispatch(UNTAGGED_WRITE, {"name": "Acme"})
        assert json.loads(text)["blocked_by"] == "tool_permission"

        # And the two surfaces show ONE truth: a decision made on the connector route is
        # listed by the app-wide route, because there is only one table for it to live in.
        client.put(
            f"/api/connectors/{NATIVE_OWNER}/permissions/{NATIVE_TOOL}",
            json={"level": "always_allow"},
        )
        listed = client.get("/api/tool-permissions").json()["permissions"]
        assert any(
            r["tool"] == NATIVE_TOOL
            and r["connector_id"] == NATIVE_OWNER
            and r["level"] == "always_allow"
            for r in listed
        )


# ── An Automation never inherits a permission granted elsewhere ───────────────


@pytest.fixture()
def in_automation(armed, monkeypatch):
    """Run the dispatch inside an Automation turn with a given approval mode."""
    from backend.core import automation_runner

    def _set(mode: str, always_allowed: tuple[str, ...] = ()):
        monkeypatch.setattr(
            automation_runner,
            "get_automation_approval",
            lambda: automation_runner.AutomationApprovalContext(
                automation_id="auto_1",
                run_id="run_1",
                session_id="sess-perm",
                mode=mode,
                always_allowed_tools=frozenset(always_allowed),
            ),
        )
        monkeypatch.setattr("backend.core.cost_context.get_cost_surface", lambda: "automation")

    return _set


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["manual", "auto", "skip"])
async def test_an_automation_inherits_a_global_always_allow_in_every_mode(in_automation, mode):
    """``always_allow`` means allow EVERYWHERE, unattended included (CLAUDE.md, Security-critical
    areas). Until 2026-10-11 a ``manual`` Automation still asked about a tool the owner had
    allowed globally; with one value per tool, Allow runs in every mode, an Automation's too.
    The mode only ever decides what ASK means."""
    perms.set_tool_permission(perms.CORE_CONNECTOR_ID, UNTAGGED_WRITE, "always_allow")
    in_automation(mode)

    text, proposed = await _dispatch(UNTAGGED_WRITE, {"name": "Acme"}, tool_use_id="toolu_auto1")

    assert proposed is None and text is not None and "blocked_by" not in text


@pytest.mark.asyncio
async def test_a_manual_automation_asks_about_a_tool_set_to_ask(in_automation):
    perms.set_tool_permission(perms.CORE_CONNECTOR_ID, UNTAGGED_WRITE, "needs_approval")
    in_automation("manual")

    text, proposed = await _dispatch(UNTAGGED_WRITE, {"name": "Acme"}, tool_use_id="toolu_auto2")

    assert text is None and proposed["grant_tool"] == UNTAGGED_WRITE


@pytest.mark.asyncio
async def test_an_automation_cannot_clear_a_block_in_any_mode(in_automation):
    """The mirror: ``skip`` means "skip the approval prompts", never "ignore a refusal".
    ``blocked`` is refused at dispatch, before any Automation-local verdict is consulted."""
    perms.set_tool_permission(perms.CORE_CONNECTOR_ID, UNTAGGED_WRITE, "blocked")
    in_automation("skip", always_allowed=(UNTAGGED_WRITE,))

    text, _ = await _dispatch(UNTAGGED_WRITE, {"name": "Acme"})

    assert json.loads(text)["blocked_by"] == "tool_permission"


@pytest.mark.asyncio
async def test_an_auto_automation_does_not_judge_an_undecided_connector_read(
    in_automation, monkeypatch
):
    """The approval TIER is "would this ask, absent the Automation's opinion" — the tool's
    shape OR its level. An undecided connector status READ is neither a write nor asking by
    level, so it runs and the judge is never consulted; the connector's own SEND is a write
    and does reach the judge."""
    judged: list[str] = []
    stub_judge(monkeypatch, "allow", judged)
    in_automation("auto")

    text, proposed = await _dispatch(NATIVE_TOOL)
    assert proposed is None and text is not None and "blocked_by" not in text
    assert judged == []

    await _dispatch(NATIVE_WRITE, {"message": "hi"})
    assert judged == [NATIVE_WRITE]


@pytest.mark.asyncio
async def test_a_self_staging_tool_obeys_the_level_and_the_automation_in_that_order(
    armed, in_automation
):
    """``registry.should_preview_write`` — the self-staging tools' preview path.

    It is no longer a second mechanism with its own precedence to keep in step: it
    DELEGATES to ``should_gate`` (ADR 2026-08-25-one-confirm-mechanism-for-every-tool), so
    what this pins is that a self-staging tool gets literally the same answer as every
    other tool — the value first, then the mode's meaning of Ask inside a run.
    """
    from backend.ai.tools import registry as reg
    from backend.core.tool_use_context import set_tool_use_id

    # Set the ContextVars the way dispatch does — they are not monkeypatchable (a
    # ContextVar's `get` is read-only), and setting them for real is closer to the path
    # under test anyway. The tool_use_id is what a pending card is KEYED by, so a call
    # without one can never be staged and is therefore never promised a card.
    reg._active_call.set({"invoked": "add_positions", "canonical": "add_positions"})
    set_tool_use_id("toolu_perm_test")

    # Outside a run: an undecided write previews, an always_allow one does not.
    assert reg.should_preview_write() is True
    perms.set_tool_permission(perms.CORE_CONNECTOR_ID, "add_positions", "always_allow")
    assert reg.should_preview_write() is False

    # Inside a manual run Ask still means a card, and the global Always allow set above still
    # runs: an Automation inherits Allow (the mode only decides what Ask means).
    in_automation("manual")
    assert reg.should_preview_write() is False
    perms.set_tool_permission(perms.CORE_CONNECTOR_ID, "add_positions", "needs_approval")
    assert reg.should_preview_write() is True
