"""run_script tool — script-bundling-skills S3.

This tool is the ONLY entry point from an AI turn into the sandbox subprocess runner.
It is intentionally gated at multiple independent layers (S4) — ALL must be ON:

  1. **Active bundle scope** — ``activate_skill`` establishes
     ``_skill_scope.active_bundle()`` for a skill with a scripts/ folder. Ordinary tool
     discovery may offer ``run_script``, but dispatch refuses it without that active bundle
     context (default-deny, like the headless ceiling).

  2. **Instance kill switch** — ``app_config.script_skills_enabled()`` must be ``True``
     (default ``False``). Human-only (no Lucius tool). When OFF, no script ever reaches
     ``run_sandboxed`` regardless of caller.

Once all gates pass, the tool:
  - Resolves the declared ``script_id`` against the bundle's manifest (unknown id →
    refused; only MANIFEST-DECLARED scripts may run).
  - Calls ``backend.sandbox.run_sandboxed`` (the S2 subprocess runner) with the
    ``bundle_dir`` and the manifest's declared ``entry``.
  - Returns the ``SandboxResult`` as a JSON string — stdout is UNTRUSTED model-facing
    data and is size-capped by the runner; the tool never evaluates it (T8).

SECURITY-CRITICAL — this is the first live code-execution path. Flag ``/security-review``
before shipping. The current isolation level is S2 (Option A bare subprocess): strong
against secret/data exfiltration + resource exhaustion by construction, weaker against
syscall-level network escape on macOS (documented on ``SandboxResult.network_hard_denied``).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from pydantic import Field

from backend.ai.tools import _skill_scope
from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, bare_object_schema, schema_of

logger = logging.getLogger(__name__)


class RunScriptInput(ToolInput):
    """Schema-of-record for ``run_script`` (see ``schema_from_model``)."""

    script_id: str = Field(
        description=(
            "The id of the script to run — the filename stem of the script in the "
            "skill's scripts/ folder (e.g. 'normalize' for 'scripts/normalize.py'). "
            "Must exactly match a script present in scripts/."
        ),
    )
    skill_id: Optional[str] = Field(
        default=None,
        description=(
            "The active skill id whose script should run. Required when more than one "
            "bundled skill is active; optional when exactly one is active."
        ),
    )
    input: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "JSON-serializable input data passed to the script via SKILL_INPUT. "
            "The script reads this from its environment. Defaults to {}."
        ),
        json_schema_extra=bare_object_schema,
    )


_SCRIPTS_OFF = (
    "Script-bundling skills are disabled for this instance "
    "(Settings → Developer → Bundled script skills is off). "
    "Ask the operator to enable the feature."
)


def _script_execution_off() -> Optional[str]:
    """The refusal while the Bundled script skills switch is off, else ``None``.

    Whether a call needs the owner's say is the permission value plus the approval mode, the same
    in a chat and in an Automation run; this is only the feature's enable/disable (Settings →
    Developer → Bundled script skills). Registered as the tool's ``unavailable=`` (refused before
    any card or judge call) and re-checked by the handler (an approval redeemed after the switch
    went off still refuses).
    """
    from backend.db.app_config_store import script_skills_enabled

    return None if script_skills_enabled() else _SCRIPTS_OFF


@tool(
    "run_script",
    description=(
        "Run a bundled script from the current skill's scripts/ folder. "
        "This tool is ONLY available when you are executing a skill whose bundle carries scripts/. "
        "Provide the script_id (the filename stem of a script in scripts/, e.g. 'normalize' for "
        "'scripts/normalize.py'), skill_id when multiple bundled skills are active, and a "
        "JSON-serializable input dict. "
        "The script runs isolated in a sandboxed subprocess (no internet, no access to the ledger "
        "or secrets) and must return valid JSON on stdout. The result is pure compute — it never "
        "touches the ledger or database directly; any ledger mutations must go through the normal "
        "add_transactions / the recording tools after you receive the result."
    ),
    input_schema=schema_of(RunScriptInput, tool_name="run_script"),
    external_effect=True,
    unavailable=_script_execution_off,
)
async def run_script(input_data: dict):  # noqa: C901 — gate-heavy by design
    """Dispatch a script run through the sandbox after checking all S4 gates."""
    # ── Gate 1: active bundle scope ──────────────────────────────────────────────
    # Skill activation does not change the tool offer. It establishes the bundle context this
    # runtime gate requires. No active bundle = the model emitted run_script outside a
    # script-bundling skill or the activation context was lost.
    try:
        bundle = _skill_scope.active_bundle(str(input_data.get("skill_id") or "") or None)
    except ValueError as exc:
        return json.dumps({"error": str(exc)}), None
    if bundle is None:
        return json.dumps(
            {
                "error": (
                    "run_script is not available outside a script-bundling skill turn — "
                    "call activate_skill with a script-type skill first"
                )
            }
        ), None

    skill_id: str = bundle.get("skill_id", "")

    # ── Gate 2: the instance switch (also checked at dispatch, before any card) ──
    off = _script_execution_off()
    if off is not None:
        return json.dumps({"error": off}), None

    # ── Resolve the requested script from the manifest ───────────────────────────
    script_id: str = input_data.get("script_id", "").strip()
    if not script_id:
        return json.dumps({"error": "script_id is required"}), None

    manifest: dict = bundle.get("manifest") or {}
    manifest_scripts: list = manifest.get("scripts") or []

    declared = {s["id"]: s for s in manifest_scripts if s.get("id")}
    if script_id not in declared:
        available = sorted(declared.keys())
        return json.dumps(
            {
                "error": (
                    f"script_id '{script_id}' is not declared in the skill manifest. "
                    f"Available: {available}"
                )
            }
        ), None

    script_entry_info: dict = declared[script_id]
    entry: str = script_entry_info.get("entry", "")
    if not entry:
        return json.dumps({"error": f"manifest script '{script_id}' has no 'entry' declared"}), None

    # Derive limits from the manifest's per-script timeout (the manifest caps this;
    # the runner then re-clamps to HARD_CEILINGS — tighten-only).
    from backend.sandbox.runner import DEFAULT_LIMITS, SandboxError, SandboxLimits, run_sandboxed

    timeout_s = script_entry_info.get("timeout_s")
    if timeout_s is not None:
        try:
            limits = SandboxLimits(
                cpu_seconds=int(timeout_s),
                wall_seconds=float(timeout_s) + 5.0,
            )
        except (ValueError, TypeError):
            limits = DEFAULT_LIMITS
    else:
        limits = DEFAULT_LIMITS

    bundle_dir = bundle.get("bundle_dir")
    if bundle_dir is None:
        return json.dumps(
            {
                "error": (
                    "Bundle path is missing from the active skill context — "
                    "the skill folder may not exist on disk."
                )
            }
        ), None

    # ── Run the sandbox ───────────────────────────────────────────────────────────
    # run_sandboxed raises SandboxError only for PRE-EXEC refusals (missing dir, sha
    # mismatch, non-POSIX host). A launched run that fails returns ok=False — NEVER raises.
    # sha verification is handled by the sandbox if a bundle_sha is in the context; without
    # per-skill sha pinning the sandbox runs against the live folder content (the sandbox
    # isolation + global switches + the local user-data folder are the trust model).
    bundle_sha: str = bundle.get("bundle_sha") or ""
    folder_identity = bundle.get("folder_identity")
    if not bundle_sha or folder_identity is None:
        return json.dumps({"error": "active skill bundle has no integrity pin"}), None
    script_input = input_data.get("input") or {}
    try:
        from backend.db.skill_store import SkillStore
        from backend.services.skill_bundles import compute_pinned_bundle_sha

        with SkillStore().pinned_snapshot(skill_id) as (fresh_skill, pinned):
            if fresh_skill is None or not fresh_skill.get("enabled", True):
                return json.dumps({"error": "skill was removed or disabled after activation"}), None
            if pinned.identity != folder_identity:
                return json.dumps({"error": "skill folder changed after activation"}), None
            if compute_pinned_bundle_sha(pinned) != bundle_sha:
                return json.dumps({"error": "skill bundle changed after activation"}), None
            result = run_sandboxed(
                bundle_dir=pinned.path,
                version="",  # bundle_dir is already the resolved skill folder
                entry=entry,
                input_json=script_input,
                bundle_sha=bundle_sha,
                interpreter=script_entry_info.get("interpreter", "python"),
                limits=limits,
            )
            pinned.verify_identity()
    except SandboxError as exc:
        logger.warning("run_script[%s/%s] pre-exec refusal: %s", skill_id, script_id, exc)
        return json.dumps({"error": f"script refused before launch: {exc}"}), None

    # Return the full result dict to the model; stdout is UNTRUSTED model-facing data (T8).
    return json.dumps(result.as_dict(), ensure_ascii=False), None


_MAX_REFERENCE_BYTES = 100_000  # cap a single reference doc fed back to the model


class ReadSkillFileInput(ToolInput):
    """Schema-of-record for ``read_skill_file`` (see ``schema_from_model``)."""

    path: str = Field(
        description=(
            "Relative path of the file within the skill bundle, e.g. "
            "'references/examples.md' or 'scripts/helper.py'."
        ),
    )
    skill_id: Optional[str] = Field(
        default=None,
        description=(
            "The active skill id whose bundle should be read. Required when more than one "
            "bundled skill is active; optional when exactly one is active."
        ),
    )


@tool(
    "read_skill_file",
    description=(
        "Read a reference/support file from the CURRENTLY-RUNNING skill's bundle (e.g. "
        "'references/tool-catalog.md'). This is PROGRESSIVE DISCLOSURE: a skill's SKILL.md "
        "instructions point you to a reference file, and you load it ON DEMAND with this tool "
        "instead of having it all in context up front. Only available while running a skill that "
        "has bundled files. Pass the relative path exactly as the skill lists it and include "
        "skill_id when multiple bundled skills are active. Read-only — it returns the file's "
        "text and never executes anything."
    ),
    input_schema=schema_of(ReadSkillFileInput, tool_name="read_skill_file"),
)
async def read_skill_file(input_data: dict):
    """Read one file from the active skill bundle (jailed). Available only inside a skill run
    whose skill has a bundle (activate_skill sets the active-bundle scope). Read-only."""
    try:
        bundle = _skill_scope.active_bundle(str(input_data.get("skill_id") or "") or None)
    except ValueError as exc:
        return json.dumps({"error": str(exc)}), None
    if bundle is None:
        return json.dumps(
            {
                "error": (
                    "read_skill_file is only available while running a skill that has bundled "
                    "files — call activate_skill first."
                )
            }
        ), None

    bundle_dir = bundle.get("bundle_dir")
    if not bundle_dir:
        return json.dumps({"error": "the current skill has no bundle files"}), None

    path = str(input_data.get("path", "")).strip()
    if not path:
        return json.dumps({"error": "path is required"}), None

    if path not in set(bundle.get("files") or []):
        return json.dumps(
            {
                "error": f"file is not part of the activated bundle: {path}",
                "available_files": bundle.get("files") or [],
            }
        ), None

    from backend.db.skill_store import SkillStore, read_pinned_skill_text
    from backend.services.skill_bundles import compute_pinned_bundle_sha

    bundle_sha = str(bundle.get("bundle_sha") or "")
    folder_identity = bundle.get("folder_identity")
    if not bundle_sha or folder_identity is None:
        return json.dumps({"error": "active skill bundle has no integrity pin"}), None
    try:
        with SkillStore().pinned_snapshot(str(bundle.get("skill_id") or "")) as (
            fresh_skill,
            pinned,
        ):
            if fresh_skill is None or not fresh_skill.get("enabled", True):
                return json.dumps({"error": "skill was removed or disabled after activation"}), None
            if pinned.identity != folder_identity:
                return json.dumps({"error": "skill folder changed after activation"}), None
            if compute_pinned_bundle_sha(pinned) != bundle_sha:
                return json.dumps({"error": "skill bundle changed after activation"}), None
            text = read_pinned_skill_text(pinned, path)
            pinned.verify_identity()
    except UnicodeDecodeError:
        text = None
    if text is None:
        return json.dumps(
            {
                "error": f"file not found in bundle: {path}",
                "available_files": bundle.get("files") or [],
            }
        ), None

    truncated = len(text.encode("utf-8")) > _MAX_REFERENCE_BYTES
    return json.dumps(
        {"path": path, "content": text[:_MAX_REFERENCE_BYTES], "truncated": truncated},
        ensure_ascii=False,
    ), None
