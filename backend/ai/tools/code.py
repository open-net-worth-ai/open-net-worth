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
  * ONE permission value, decided by the ordinary gate (ADR
    ``2026-10-11-permissions-are-one-value-per-tool``). ``run_code`` declares
    ``external_effect=True`` (it executes code on the owner's machine), so it is seeded **Ask**
    like any other tool that acts outside the owner's data: a card in Manual, the Auto judge in
    Auto, runs in Skip, and the owner can set it to Always allow or Block in Settings →
    Permissions. This handler has NO approval logic of its own any more — it used to stage its
    own ``code_run`` card on "the first run in a conversation" and honour an instance-wide
    ``code_execution_auto_run`` flag, a second, hidden permission the Permissions list could not
    show. That flag became a stored ``core:run_code`` Always allow (revision
    ``202610201400``); an approval runs the arguments the SERVER staged under the tool_use_id,
    so the model cannot substitute different code on apply.
  * Kill switch (RFC §4.3): ``app_config.code_execution_enabled`` (default OFF) hard-blocks
    everything. It is the feature's enable/disable, not an approval layer, and it applies to
    every call whatever the permission value: Always allow on a switched-off feature still
    refuses.

Pure compute: a script NEVER touches the ledger/DB. Any mutation is still Lucius calling the
normal add_transaction / apply_* tools after reading the result.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Literal, Optional

from pydantic import Field

from backend.ai.tools import _confirm_gate as _gate
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
    "Once it's on, ask me again."
)


def _code_execution_off() -> Optional[str]:
    """The refusal while the kill switch is off, else ``None``. Registered as the tool's
    ``unavailable=`` (so dispatch refuses before any card or judge call) and re-checked by the
    handler (so an approval redeemed after the switch went off still refuses)."""
    return None if _kill_switch_on() else _CODE_OFF


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
        "running a quick script. The user may be asked to approve the script before it runs."
    ),
    input_schema=schema_of(RunCodeInput, tool_name="run_code"),
    external_effect=True,
    unavailable=_code_execution_off,
)
async def run_code(input_data: dict):
    """Run model-authored code in the sandbox, behind the kill switch. Whether the owner is asked
    first is the ordinary permission gate's question, answered before this handler runs."""
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

    return await _run_and_format(source, script_input)


# ── Save-as-skill bridge (RFC C5 — the "write a tool" half) ───────────────────────────────
# Turns an ad-hoc script into a reusable SCRIPT-SKILL bundle (parent RFC docs/rfc/
# script-bundling-skills.md). Approve-tier propose → apply, mirroring create_skill /
# apply_skill_change. SECURITY: confirmed authoring creates an ordinary enabled skill but grants
# ZERO code-execution authority: run_script still requires the human-only instance switch and is an
# ordinary tool for permissions (seeded Ask). The model cannot change the switch.

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
    mutates=("skills",),
    self_stages=True,
)
async def save_code_as_skill(input_data: dict):
    from backend.db.skill_files import INTERPRETER_BY_EXT
    from backend.services import skills as skills_service

    gated = False
    if input_data.get(_gate.CONFIRM_TOKEN_KEY):
        entry = _gate.redeem(input_data[_gate.CONFIRM_TOKEN_KEY], "save_code_as_skill")
        if entry is None:
            return _gate.unknown_token_result("save_code_as_skill")
        input_data = _gate.staged_args(entry)
    else:
        gated = registry.should_preview_write()

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
    # Not gated means WRITE: a redemption, an Always allow, or an unattended caller with no card
    # to show.
    if not gated:
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
