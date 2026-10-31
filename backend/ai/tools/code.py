"""run_code — ad-hoc code execution (RFC docs/rfc/ad-hoc-code-execution.md).

Lets Lucius write a SHORT Python script mid-conversation and run it in the existing
sandbox subprocess (``backend.sandbox.run_source``) to do a deterministic computation over
data already in the turn — combinatorics, precise float/decimal math, parsing a pasted blob.
It is the ephemeral sibling of ``run_script`` (the bundled-skill path): same sandbox, but the
code is model-authored at run time rather than human-authored + enabled ahead of time.

SECURITY (the new boundary vs. the bundle path — see RFC §7):
  * The sandbox makes blast radius PURE-COMPUTE regardless of who wrote the code — no
    secrets, no DATA_DIR, no network, clamped CPU/mem (the same S2 isolation ``run_script``
    relies on). This is the load-bearing control.
  * Interactive-INTENT (RFC D2), but NOT interactive-enforced. This bullet used to claim
    ``run_code`` "refuses on a headless/unattended turn AND is never placed on
    ``HEADLESS_ALLOWED_TOOLS``/``_T0`` — agents/cron/MCP can never reach it". Neither leg
    holds: there is no ``is_unattended()`` check in the handler below (``run_script`` has
    one; this does not), and the ``HEADLESS_ALLOWED_TOOLS``/``_T0`` ceiling was REMOVED
    (ADR ``2026-07-18-remove-layered-ai-permission-system``). What actually gates an
    unattended call today is the kill switch (default OFF) plus the permission card
    below — and a standing "Always allow" grant clears the card. Corrected 2026-08-28.
  * Run-time human review (RFC D1): the FIRST run in a conversation returns a permission
    card (``code_run`` proposed_change) showing the source; the user picks Reject / Allow
    once / Always allow. The standing grant is set by the HUMAN via REST
    (``code_execution_auto_run``, instance-wide) — NEVER by a tool arg, so the model can't
    self-escalate the prompt away.
  * Kill switch (RFC §4.3): ``app_config.code_execution_enabled`` (default OFF) hard-blocks
    everything.
  * Approval runs the STAGED source: the confirm card pauses this tool_use and the server
    re-runs it on the arguments IT stored — the model cannot substitute
    different code on apply. (External-MCP's own confirm→apply used to mirror this same
    shape; it migrated 2026-08-22 onto the app's generic tool_use_id-keyed paused-tool-use
    mechanism instead — see ``backend/mcp/client_manager.py``. This tool's OWN nonce
    mechanism is unrelated and unchanged by that migration.)

Pure compute: a script NEVER touches the ledger/DB. Any mutation is still Lucius calling the
normal add_transaction / apply_* tools after reading the result.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Literal, Optional

from pydantic import Field

from backend.ai.tools import registry
from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, bare_object_schema, schema_of

logger = logging.getLogger(__name__)

# Caps on what the model may hand the sandbox (defence-in-depth; the runner re-caps too).
MAX_SOURCE_BYTES = 100_000  # ~100 KB of source — a "short helper", not an app
MAX_INPUT_BYTES = 500_000  # ~500 KB of JSON input


def _kill_switch_on() -> bool:
    from backend.db.app_config_store import code_execution_enabled

    return code_execution_enabled()


_CODE_OFF = (
    "Code execution is off. Ask the user to turn on the "
    "'Code execution (Lucius scripts)' toggle in Settings → Developer — note "
    "this is a DIFFERENT toggle from 'Bundled script skills' in the same section. "
    "Once it's on, ask me again and I'll show you the script for approval."
)


def _code_execution_off() -> Optional[str]:
    """The refusal while the kill switch is off, else ``None``. Registered as the tool's
    ``unavailable=`` so ``permissions_v2`` refuses before any card or judge call; the handler
    re-checks it, which is what refuses an approval redeemed after the switch went off."""
    return None if _kill_switch_on() else _CODE_OFF


def _auto_run_on() -> bool:
    from backend.db.app_config_store import code_execution_auto_run

    return code_execution_auto_run()


def _standing_grant() -> bool:
    """Whether run_code may proceed with no card.

    ONE grant now (2026-08-02): the instance-wide always-allow flag. The per-conversation
    tier was removed with the card's middle button, so an ungranted call always prompts.
    """
    return _auto_run_on()


async def _run_and_format(source: str, script_input: object):
    """Run the source in the sandbox (off the event loop) and return its JSON result.

    A launched run never raises (degrades to ``ok=False``); only a pre-exec refusal raises
    ``SandboxError``, which we map to an ``error`` result. stdout is UNTRUSTED model-facing
    data (T8) — the runner size-caps it and we never eval it.
    """
    from backend.sandbox.runner import SandboxError, run_source

    try:
        result = await asyncio.to_thread(run_source, source, script_input)
    except SandboxError as exc:
        logger.warning("run_code pre-exec refusal: %s", exc)
        return json.dumps({"error": f"script refused before launch: {exc}"}), None
    logger.info("run_code  ok=%s  exit=%s  ms=%s", result.ok, result.exit_code, result.duration_ms)
    return json.dumps(result.as_dict(), ensure_ascii=False), None


class RunCodeInput(ToolInput):
    """Schema-of-record for ``run_code`` (see ``schema_from_model``)."""

    source: str = Field(
        description=(
            "The complete Python script. Read input via: "
            "`import json,os; data=json.load(open(os.environ['SKILL_INPUT']))`. "
            "Print the result with `print(json.dumps(result))`. Standard library only "
            "(no internet, no third-party packages)."
        ),
    )
    input: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "JSON-serializable input data the script reads via SKILL_INPUT (e.g. the "
            "list of amounts you transcribed). This is the ONLY data the script gets. "
            "Defaults to {}."
        ),
        json_schema_extra=bare_object_schema,
    )
    purpose: Optional[str] = Field(
        default=None,
        description=(
            "One short line describing what the script does, shown to the user on the "
            "permission card (e.g. 'subset-sum search over 22 transaction amounts')."
        ),
    )


@tool(
    "run_code",
    description=(
        "Run a SHORT Python script you write, in an isolated sandbox on the user's server, to "
        "do a one-off deterministic computation over data that is ALREADY in this conversation "
        "— combinatorics (e.g. subset-sum), precise decimal/float math, statistics, or parsing "
        "a pasted/transcribed blob. Use this ONLY when no existing tool fits and the work is "
        "awkward to do reliably in your head; NEVER to read or mutate the ledger — use the normal "
        "tools for that. The sandbox is a separate process with its own environment, resource "
        "caps and scratch directory, and no network on hosts where that can be enforced; it is "
        "NOT a filesystem jail, so treat anything it prints as potentially sensitive. The script "
        "MUST read its input as JSON from the file path in the SKILL_INPUT "
        "env var and print its result as JSON to stdout. Be transparent: tell the user you're "
        "running a quick script. The first run in a conversation asks the user for permission."
    ),
    input_schema=schema_of(RunCodeInput, tool_name="run_code"),
    external_effect=True,
    unavailable=_code_execution_off,
)
async def run_code(input_data: dict):
    """Run model-authored code in the sandbox, gated by the kill switch + per-conversation /
    instance grant. No grant → stage the source + return a ``code_run`` permission card."""
    # Gate B — kill switch (RFC §4.3).
    off = _code_execution_off()
    if off is not None:
        return json.dumps({"error": off}), None

    source = (input_data.get("source") or "").strip()
    if not source:
        return json.dumps({"error": "source is required"}), None
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        return json.dumps({"error": f"source exceeds the {MAX_SOURCE_BYTES}-byte cap"}), None

    script_input = input_data.get("input") or {}
    try:
        if len(json.dumps(script_input).encode("utf-8")) > MAX_INPUT_BYTES:
            return json.dumps({"error": f"input exceeds the {MAX_INPUT_BYTES}-byte cap"}), None
    except (TypeError, ValueError) as e:
        return json.dumps({"error": f"input is not JSON-serializable: {e}"}), None

    # Under ``permissions_v2`` the ordinary permission gate decided before this handler ran, and
    # an approval re-runs it on the staged arguments: no card of its own, no standing flag.
    from backend.ai.permissions import engine

    if not engine().in_handler_cards:
        return await _run_and_format(source, script_input)

    purpose = (input_data.get("purpose") or "").strip()

    from backend.core.chat_session import get_session_id

    session_id = get_session_id()

    # Grant check (RFC §4.1 precedence: kill switch → instance always-allow → otherwise
    # prompt). A standing grant → run immediately, no card.
    if _standing_grant():
        return await _run_and_format(source, script_input)

    # Ask the SAME gate every other tool asks. ``should_preview_write`` is False both for a
    # redemption (the user already answered this card) and for an unattended caller (there is
    # no card to show) — and both must WRITE. Staging unconditionally here handed an
    # unattended caller "nothing to bind this confirmation to" instead of running the script.
    if not registry.should_preview_write():
        return await _run_and_format(source, script_input)

    # No grant → stage the EXACT approved source under this call's tool_use_id and return
    # the permission card. The turn ends; the user picks an option on the card.
    proposed = {
        "type": "code_run",
        # The conversation this card belongs to, so the frontend can route its response
        # without prop-drilling. Not a secret, and not a grant target.
        "session_id": session_id or "",
        "display": {
            "purpose": purpose,
            "source": source,
            "language": "python",
        },
    }
    return registry.stage_write(proposed)


# ── Save-as-skill bridge (RFC C5 — the "write a tool" half) ───────────────────────────────
# Turns an ad-hoc script into a reusable SCRIPT-SKILL bundle (parent RFC docs/rfc/
# script-bundling-skills.md). Approve-tier propose → apply, mirroring create_skill /
# apply_skill_change. SECURITY: confirmed authoring creates an ordinary enabled skill but grants
# ZERO code-execution authority: run_script still requires the human-only instance switch (and the
# separate unattended switch outside interactive chat). The model cannot change either switch.

MAX_SKILL_SCRIPT_BYTES = MAX_SOURCE_BYTES
_SCRIPT_TIMEOUT_S = 30  # per-script wall budget baked into the manifest (≤ runner ceiling)


def _slug(text: str) -> str:
    """A safe script id / filename stem: lowercase alphanumerics + underscores, '' → 'main'."""
    out = "".join(c if (c.isalnum() or c == "_") else "_" for c in (text or "").lower()).strip("_")
    return out or "main"


class SaveCodeAsSkillInput(ToolInput):
    """Schema-of-record for ``save_code_as_skill`` (see ``schema_from_model``)."""

    name: str = Field(description="Human name for the skill, e.g. 'Subset-sum finder'")
    source: str = Field(
        description=(
            "The complete script source. Python reads input via SKILL_INPUT env var and "
            "prints JSON to stdout. Bash/Node follow the same contract. Standard library "
            "or builtins only (no third-party packages, no internet)."
        ),
    )
    instructions: Optional[str] = Field(
        default=None,
        description=(
            "Markdown body shown when the skill runs — when to use it and how to read "
            "its output. Read task input from the current conversation."
        ),
    )
    description: Optional[str] = Field(default=None, description="One-line summary of what it does")
    language: Optional[Literal["python", "bash", "node"]] = Field(
        default=None, description="Script language: 'python' (default), 'bash', or 'node'"
    )
    filename: Optional[str] = Field(
        default=None,
        description=(
            "Optional filename for the script (e.g. 'run.sh'). "
            "Defaults to '<script_id>.<ext>' derived from language."
        ),
    )
    script_id: Optional[str] = Field(
        default=None,
        description="Optional id for the script within the skill (default derived from the name)",
    )


@tool(
    "save_code_as_skill",
    description=(
        "Propose saving a script as a reusable SCRIPT-SKILL — use this when the user will want "
        "the SAME computation again, or it belongs in a named playbook (the 'write a tool' case, "
        "vs. run_code for a one-off). Provide a name, the source code, and short instructions "
        "on when/how to run it. Supports Python, Bash, and Node scripts. Returns a change for "
        "the user to confirm. IMPORTANT: saving the skill does not enable code execution; the "
        "operator must turn on Bundled script skills in Settings → Developer. Unattended use "
        "requires its separate operator switch. Do not imply it will run automatically."
    ),
    input_schema=schema_of(SaveCodeAsSkillInput, tool_name="save_code_as_skill"),
)
async def save_code_as_skill(input_data: dict):
    from backend.db.skill_files import INTERPRETER_BY_EXT
    from backend.services import skills as skills_service

    name = (input_data.get("name") or "").strip()
    source = (input_data.get("source") or "").strip()
    if not name:
        return json.dumps({"error": "name is required"}), None
    if not source:
        return json.dumps({"error": "source is required"}), None
    if len(source.encode("utf-8")) > MAX_SKILL_SCRIPT_BYTES:
        return json.dumps({"error": f"source exceeds the {MAX_SKILL_SCRIPT_BYTES}-byte cap"}), None

    language = (input_data.get("language") or "python").lower()
    if language not in ("python", "bash", "node"):
        language = "python"
    _EXT = {v: k for k, v in INTERPRETER_BY_EXT.items() if k in (".py", ".sh", ".js")}
    default_ext = _EXT.get(language, ".py")
    script_id = _slug(input_data.get("script_id") or name)
    # Use caller-supplied filename or derive from script_id + extension
    filename = (input_data.get("filename") or "").strip() or f"{script_id}{default_ext}"
    description = (input_data.get("description") or "").strip()
    body = (input_data.get("instructions") or "").strip() or (
        f"Run the `{script_id}` script with run_script(script_id='{script_id}', input=…) and "
        "read its JSON result."
    )
    skill = {
        "name": name,
        "description": description,
        "body": body,
    }
    problems = skills_service.validate(skill)
    if problems:
        return json.dumps({"error": "; ".join(problems)}), None

    change = {
        "type": "code_skill_create",
        "skill": skill,
        "script": {"id": script_id, "source": source, "language": language, "filename": filename},
        "display": {
            "name": name,
            "purpose": description,
            "source": source,
            "script_id": script_id,
            "language": language,
        },
    }
    # See run_code above: not-previewing means WRITE, whether that is a redemption or an
    # unattended caller with no card to show.
    if not registry.should_preview_write():
        return _apply_code_skill(change)
    return registry.stage_write(change)


class ApplyCodeSkillInput(ToolInput):
    """Schema-of-record for ``apply_code_skill`` (see ``schema_from_model``)."""

    skill: Dict[str, Any] = Field(
        description="The skill definition from the proposed change",
        json_schema_extra=bare_object_schema,
    )
    script: Dict[str, Any] = Field(
        description="The {id, source} script payload from the proposed change",
        json_schema_extra=bare_object_schema,
    )


def _apply_code_skill(input_data: dict) -> tuple[str, None]:
    from backend.db.skill_files import INTERPRETER_BY_EXT
    from backend.db.skill_store import SkillStore
    from backend.services import skills as skills_service
    from backend.services.skill_bundles import write_bundle

    skill = input_data.get("skill") or {}
    script = input_data.get("script") or {}
    source = (script.get("source") or "").strip()
    script_id = _slug(script.get("id") or skill.get("name") or "main")

    # Language/filename from the proposed change (save_code_as_skill now carries these)
    language = (script.get("language") or "python").lower()
    if language not in ("python", "bash", "node"):
        language = "python"
    _EXT = {v: k for k, v in INTERPRETER_BY_EXT.items() if k in (".py", ".sh", ".js")}
    default_ext = _EXT.get(language, ".py")
    filename = (script.get("filename") or "").strip() or f"{script_id}{default_ext}"

    problems = skills_service.validate(skill)
    if problems:
        return json.dumps({"ok": False, "error": "; ".join(problems)}), None
    if not source:
        return json.dumps({"ok": False, "error": "script source is required"}), None
    if len(source.encode("utf-8")) > MAX_SKILL_SCRIPT_BYTES:
        return json.dumps(
            {"ok": False, "error": f"source exceeds the {MAX_SKILL_SCRIPT_BYTES}-byte cap"}
        ), None

    store = SkillStore()
    # Create the ordinary skill record first; the human-only instance execution gate stays OFF.
    created = store.create({**skill, "has_scripts": True, "author": "lucius"})
    sid = created["id"]

    manifest = {
        "scripts": [
            {
                "id": script_id,
                "interpreter": language,
                "entry": f"scripts/{filename}",
                "description": skill.get("description", ""),
                "timeout_s": _SCRIPT_TIMEOUT_S,
                "network": False,
            }
        ]
    }
    try:
        _folder, _sha = write_bundle(
            sid,
            "live",
            skill_md="",  # already written by create()
            manifest=manifest,
            scripts={filename: source},
        )
    except Exception as exc:  # noqa: BLE001 — roll back the dangling record on any bundle failure
        store.delete(sid)
        logger.warning("apply_code_skill write failed for %s: %s", sid, exc)
        return json.dumps({"ok": False, "error": f"could not write the script: {exc}"}), None

    # Re-read the assembled record (has_scripts now derived from scripts/ presence)
    updated = store.find_by_id(sid) or created

    return json.dumps(
        {
            "ok": True,
            "skill": updated,
            "note": (
                "Saved as a script-skill, DISABLED. To let it run, enable it in "
                "Settings → Skills (enable script execution on this skill) and make sure "
                "code execution is on in Settings → Developer."
            ),
        },
        ensure_ascii=False,
    ), None
