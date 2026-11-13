"""Skill-registry tools (P2-SK1).

A skill is a standard Agent Skills folder. These tools let Lucius list,
run, and author skills:

  * ``list_skills`` / ``activate_skill``     — auto-tier (read / orchestrate; proposed=None)
  * ``create_skill`` / ``update_skill`` / ``delete_skill`` — approve-tier
    (return a ``proposed_change`` the user confirms; the write happens in the
    matching ``apply_skill_*`` path, mirroring ``propose_position_*``)

SECURITY: ``activate_skill`` executes NO arbitrary code/shell. It validates a stored skill
and returns a server-private activation request. The assistant persists the rendered
instructions while ordinary tool discovery and every normal ONW security gate remain
unchanged. ``activate_skill`` itself never performs a domain write and returns
``proposed_change=None``.

Validation that needs the live registry lives in ``services.skills`` (reached via
the ``core.tool_registry_seam`` inversion), so this module respects the dependency
rule (ai → services → db → core; no db/registry money math here).
"""

from __future__ import annotations

import json
from typing import Any, Dict, Literal, Optional

from pydantic import Field

from backend.ai.skill_runtime import ACTIVATION_MARKER
from backend.ai.tools import _bounded as _b
from backend.ai.tools import _skill_scope, registry
from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, bare_object_schema, schema_of
from backend.core.text_edit import (
    TextEditError,
    edit_display,
    parse_text_edit,
    resolve_text_edits,
)
from backend.services import skill_files_authoring as skill_files
from backend.services import skills as skills_service

# Standard definition fields plus ONW's separate enable/disable bookkeeping.
# An allowlist keeps derived folder/runtime fields out of model-authored writes.
_AI_EDITABLE_FIELDS = frozenset(
    {"name", "description", "body", "license", "compatibility", "metadata", "enabled"}
)
_MAX_ACTIVATION_ARGUMENT_CHARS = 4096


def _store():
    from backend.db.skill_store import SkillStore

    return SkillStore()


# ── Read / orchestrate (auto-tier) ─────────────────────────────────────────────


class ListSkillsInput(ToolInput):
    """Schema-of-record for ``list_skills`` (see ``schema_from_model``)."""

    enabled_only: Optional[bool] = Field(
        default=None, description="If true, only return enabled skills (default false)"
    )
    skill_id: Optional[str] = Field(
        default=None,
        description=(
            "Return just this one skill, WITH its full instruction body. Use after "
            "the gallery to inspect or edit a specific skill."
        ),
    )


@tool(
    "list_skills",
    description=(
        "List the user's saved skills — named, reusable playbooks (e.g. 'monthly-close', "
        "'budget-review') you can run with activate_skill. Each row carries its description "
        "and standard metadata. Call this to see what one-click routines are "
        "available before "
        "suggesting or running one.\n\n"
        "The gallery OMITS each skill's instruction body — activate_skill loads it for you, so you "
        "rarely need it here. To read or edit one skill's instructions, call this again with "
        "skill_id=<id>."
    ),
    input_schema=schema_of(ListSkillsInput, tool_name="list_skills"),
)
async def list_skills(input_data: dict):
    known = skills_service.tool_names()
    skill_id = (input_data.get("skill_id") or "").strip()
    if skill_id:
        # Targeted single read — full record, instruction body included. This is the
        # re-fetch path that makes eliding the body on the gallery non-lossy.
        skill = _store().find_by_id(skill_id)
        if skill is None:
            return json.dumps(
                {"error": f"no skill with id {skill_id!r} — call list_skills for current ids"}
            ), None
        return json.dumps(
            [skills_service.to_list_row(skill, known_tools=known)], ensure_ascii=False
        ), None

    enabled = True if input_data.get("enabled_only") else None
    rows = _store().query_skills(enabled=enabled)
    out = [
        _b.elide_body(
            skills_service.to_list_row(s, known_tools=known),
            "body",
            f"call list_skills(skill_id='{s.get('id')}') for the full instructions",
        )
        for s in rows
    ]
    return json.dumps(out, ensure_ascii=False), None


class ActivateSkillInput(ToolInput):
    """Schema-of-record for ``activate_skill`` (see ``schema_from_model``)."""

    skill_id: str = Field(description="The skill's id (from list_skills)")
    arguments: Optional[str] = Field(
        default=None,
        max_length=4096,
        description=(
            "Optional Claude-compatible argument text. Use only when the skill instructions "
            "declare argument placeholders; otherwise the user's message already provides context."
        ),
    )


@tool(
    "activate_skill",
    description=(
        "Run a saved skill (a named playbook) by its id — get the id from list_skills. The host loads "
        "the skill's instructions as user-role context before your next response. Skills do not "
        "change the ordinary tool offer or its security policy. It performs no write itself — any change inside the skill goes through that tool's own permission, as usual."
    ),
    input_schema=schema_of(ActivateSkillInput, tool_name="activate_skill"),
)
async def activate_skill(input_data: dict):
    skill = _store().find_by_id(input_data.get("skill_id", ""))
    if skill is None:
        return json.dumps({"error": "unknown skill_id — call list_skills for current ids"}), None
    if not skill.get("enabled", True):
        return json.dumps({"error": "skill is disabled"}), None
    from backend.db.skill_files import allows_implicit_invocation, allows_model_invocation

    if not allows_implicit_invocation(skill.get("openai")) or not allows_model_invocation(
        skill.get("claude")
    ):
        return json.dumps({"error": "skill does not allow model invocation"}), None

    problems = skills_service.validate(skill)
    if problems:
        return json.dumps({"error": "invalid skill", "problems": problems}), None

    argument_text = str(input_data.get("arguments") or "")
    if len(argument_text) > _MAX_ACTIVATION_ARGUMENT_CHARS:
        return json.dumps(
            {"error": f"arguments exceed {_MAX_ACTIVATION_ARGUMENT_CHARS} characters"}
        ), None
    result = _activation_result(skill, argument_text=argument_text)
    return json.dumps(result, ensure_ascii=False), None


def activate_skill_scope(
    skill: dict,
    *,
    pinned_folder=None,
    model_invoked: bool = True,
) -> dict:
    """Apply one validated invocation's recursion and skill-resource context.

    Ordinary tool discovery and policy are intentionally untouched, matching Codex.
    """

    # Any skill that has folder files (references/ and/or scripts/) gets an active-bundle scope
    # so bundle-aware tools can validate resource access when reached through ordinary discovery.
    from backend.db.skill_store import is_scripted

    skill_id = skill.get("id", "")
    is_script = is_scripted(skill)
    from backend.services.skill_bundles import (
        compute_bundle_sha,
        compute_pinned_bundle_sha,
        read_bundle,
        read_pinned_bundle,
        version_dir,
    )

    _skill_folder = (
        pinned_folder.path if pinned_folder is not None else version_dir(str(skill_id), "live")
    )
    available_files: list[str] = []
    runnable_scripts: list[str] = []
    manifest: dict = {}
    bundle_files: list[str] = []
    try:
        manifest_data = (
            read_pinned_bundle(pinned_folder)
            if pinned_folder is not None
            else read_bundle(skill_id, "live")
        )
        manifest = manifest_data.get("manifest") or {}
        bundle_files = sorted(
            {
                *(manifest_data.get("files") or {}).keys(),
                *(manifest_data.get("binary_files") or []),
            }
        )
    except Exception:
        # If the folder can't be read the gated tools will surface the error.
        pass
    has_folder_files = bool(bundle_files)
    if has_folder_files or is_script:
        from pathlib import Path

        bundle_sha = (
            compute_pinned_bundle_sha(pinned_folder)
            if pinned_folder is not None
            else compute_bundle_sha(_skill_folder)
        )
        bundle_dir_resolved: Optional[Path] = (
            _skill_folder if pinned_folder is not None or _skill_folder.is_dir() else None
        )

        scripts_in_manifest = [s.get("id") for s in manifest.get("scripts", []) if s.get("id")]
        available_files = bundle_files
        runnable_scripts = scripts_in_manifest
        _skill_scope.set_active_bundle(
            {
                "skill_id": skill_id,
                "manifest": manifest,
                "bundle_dir": bundle_dir_resolved,
                "bundle_sha": bundle_sha,
                "folder_identity": pinned_folder.identity if pinned_folder is not None else None,
                "allowed_script_ids": set(scripts_in_manifest),
                "files": bundle_files,
            }
        )
    _skill_scope.activate_invocation(str(skill_id))

    return {
        "available_files": available_files,
        "runnable_scripts": runnable_scripts,
    }


def _activation_result(skill: dict, *, argument_text: str = "") -> dict:
    """Private host request plus the compact acknowledgement persisted for the model."""
    return {
        ACTIVATION_MARKER: {
            "skill_id": skill.get("id"),
            "source": "model",
            **({"argument_text": argument_text} if argument_text else {}),
        },
        "ok": True,
        "skill_id": skill.get("id"),
        "message": "Skill activation accepted by the host.",
    }


def _stage_or_write(change: dict) -> tuple[Optional[str], Optional[dict]]:
    """Stage a skill change as a confirm card — or WRITE it when there is no card to show or
    the user has already answered one.

    The single funnel the three skill propose tools return through. Until 2026-08-25 they
    returned ``{"proposed": True}`` and stored NOTHING: a ✓ asked the MODEL to re-call
    ``apply_skill_change`` with arguments it reconstructed, so what committed was whatever it
    remembered — a whole skill body, retyped. Now the change is stored under this call's
    ``tool_use_id`` and the server runs THOSE args (ADR
    ``2026-08-25-one-confirm-mechanism-for-every-tool``).

    The lost-update guarantee is unaffected and still lives in ``apply_skill_change``: a
    ``{old, new}`` body edit is re-resolved against what is stored at COMMIT time, so a skill
    hand-edited while the card sat on screen refuses rather than reverting that work.
    """
    if registry.should_preview_write():
        # The CALLER'S arguments, never the change derived from them: approval re-runs this
        # same handler, which re-derives (and re-validates) its change from its arguments.
        # Staging the change instead handed the handler a shape it rejects — "description is
        # required" on a confirm whose card had already shown a valid skill.
        return registry.stage_write(change)
    return _apply_skill_change(change)


# ── Author (approve-tier: stage a confirm card; the approval writes) ────────────


class CreateSkillInput(ToolInput):
    """Schema-of-record for ``create_skill`` (see ``schema_from_model``)."""

    name: str = Field(
        description=(
            "Short slug-style name — it is COERCED to the Agent-Skill slug form (lowercase "
            "a-z, 0-9 and hyphens, truncated to 64 chars) and becomes the skill's id, so "
            "'Monthly Close' is stored as 'monthly-close'. Prefer writing it that way."
        )
    )
    description: str = Field(
        description=(
            "One or two sentences on WHEN to use this skill — load-bearing, not decorative. "
            "It is the primary trigger text injected into the system prompt, and the model "
            "matches that catalog metadata to auto-invoke the skill "
            "without the user naming it. Cover the phrasings a user would actually say. "
            "Max 1024 characters."
        ),
    )
    body: Optional[str] = Field(
        default=None,
        description="Markdown instructions for the skill",
    )
    files: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Supporting text files for the skill folder, {relative path: content}. Keep the "
            "body to what is needed on every run; put detail the model needs only at one step "
            "in references/ (the body says when to read it, e.g. 'read references/slots.md "
            "when you fill the template') and fixed output templates in assets/. Only "
            "references/ or assets/, only .md .html .json .csv .txt, at most 10 files of "
            "100 KB. Scripts go through save_code_as_skill instead."
        ),
        json_schema_extra=bare_object_schema,
    )


@tool(
    "create_skill",
    description=(
        "Create a new skill (a saved playbook). Provide a name, a markdown body of "
        "instructions and, optionally, supporting files (references/, assets/). Skills use the "
        "ordinary tool discovery surface and never alter tool policy."
    ),
    input_schema=schema_of(CreateSkillInput, tool_name="create_skill"),
    mutates=("skills",),
)
async def create_skill(input_data: dict):
    candidate = {
        "name": input_data.get("name", ""),
        "description": input_data.get("description", ""),
        "body": input_data.get("body", ""),
    }
    problems = skills_service.validate(candidate, require_standard_fields=True)
    files = input_data.get("files") or None
    problems += skill_files.file_problems(files)
    if problems:
        return json.dumps({"error": "; ".join(problems)}), None
    display: dict = {"name": candidate["name"]}
    if files:
        candidate["files"] = files
        display["files"] = skill_files.describe(files)
    change = {
        "type": "skill_create",
        "skill": candidate,
        "display": display,
    }
    return _stage_or_write(change)


class UpdateSkillInput(ToolInput):
    """Schema-of-record for ``update_skill`` (see ``schema_from_model``)."""

    skill_id: str = Field(
        description=(
            "The skill to edit, by its stored id (from list_skills). An unknown id is "
            "refused rather than creating anything."
        )
    )
    changes: Dict[str, Any] = Field(
        description=(
            "Top-level fields to change (name, description, body, "
            "enabled, files). `files` adds or replaces supporting files ({path: content}, "
            "null removes one) under the same rules as create_skill. `body` takes EITHER a whole new body OR, preferred for a targeted "
            'change, an EDIT: {"old": "<exact text copied verbatim from '
            'list_skills(skill_id=…)>", "new": "<replacement>"}. Applied to the CURRENT '
            "stored body; REFUSED if `old` is missing or appears more than once, which means "
            "the body changed under you — re-read it instead of overwriting."
        ),
        json_schema_extra=bare_object_schema,
    )


@tool(
    "update_skill",
    description=(
        "Edit an existing skill (name, description, body, "
        "or enabled). Identify it by skill_id (from list_skills)."
    ),
    input_schema=schema_of(UpdateSkillInput, tool_name="update_skill"),
    mutates=("skills",),
)
async def update_skill(input_data: dict):
    sid = input_data.get("skill_id", "")
    existing = _store().find_by_id(sid)
    if existing is None:
        return json.dumps({"error": "unknown skill_id — call list_skills for current ids"}), None
    raw_changes = input_data.get("changes") or {}
    changes = {k: v for k, v in raw_changes.items() if k in _AI_EDITABLE_FIELDS}
    files = raw_changes.get("files") or None
    file_problems = skill_files.file_problems(files)
    if file_problems:
        return json.dumps({"error": "; ".join(file_problems)}), None
    # D7 (RFC 2026-07-20 §14.2): `body` may be an EDIT — {old, new} — instead of the whole
    # new body. Resolve a COPY to refuse a bad edit on THIS call and to validate the result;
    # the change dict keeps the UNRESOLVED edit so the apply path re-resolves against
    # whatever is stored then (see core.text_edit for why that ordering matters).
    body_edit = None
    try:
        body_edit = parse_text_edit(changes.get("body"))
        resolved = resolve_text_edits(
            changes, existing, fields=("body",), read_tool="list_skills(skill_id=…)"
        )
    except TextEditError as exc:
        return json.dumps({"error": str(exc)}), None
    # Validate the *result* (merge changes over the current skill) before confirmation.
    merged = {**existing, **resolved}
    problems = skills_service.validate(merged, require_standard_fields=True)
    if problems:
        return json.dumps({"error": "; ".join(problems)}), None
    display: dict = {
        "name": existing.get("name"),
        "changed_fields": sorted([*changes.keys(), *(["files"] if files else [])]),
    }
    if body_edit is not None:
        display["body_edit"] = edit_display(body_edit)
    if files:
        changes["files"] = files
        display["files"] = skill_files.describe(files)
    change = {
        "type": "skill_update",
        "skill_id": sid,
        "changes": changes,
        "display": display,
    }
    return _stage_or_write(change)


class DeleteSkillInput(ToolInput):
    """Schema-of-record for ``delete_skill`` (see ``schema_from_model``)."""

    skill_id: str = Field(description=("The skill to delete, by its stored id (from list_skills)."))


@tool(
    "delete_skill",
    description=("Delete a saved skill. Identify it by skill_id (from list_skills)."),
    input_schema=schema_of(DeleteSkillInput, tool_name="delete_skill"),
    mutates=("skills",),
)
async def delete_skill(input_data: dict):
    sid = input_data.get("skill_id", "")
    existing = _store().find_by_id(sid)
    if existing is None:
        return json.dumps({"error": "unknown skill_id — call list_skills for current ids"}), None
    change = {
        "type": "skill_delete",
        "skill_id": sid,
        "display": {"name": existing.get("name")},
    }
    return _stage_or_write(change)


class ApplySkillChangeInput(ToolInput):
    """Schema-of-record for ``apply_skill_change`` (see ``schema_from_model``)."""

    type: Literal["skill_create", "skill_update", "skill_delete"] = Field(
        description=(
            "Which confirmed change to apply — copy the `type` from the proposed change "
            "verbatim. It selects which of the other fields are read: skill_create reads "
            "`skill`, skill_update reads `skill_id` + `changes`, skill_delete reads `skill_id`."
        )
    )
    skill: Optional[Dict[str, Any]] = Field(
        default=None,
        description="For skill_create: the skill definition",
        json_schema_extra=bare_object_schema,
    )
    skill_id: Optional[str] = Field(default=None, description="For skill_update/skill_delete")
    changes: Optional[Dict[str, Any]] = Field(
        default=None,
        description="For skill_update: the field changes",
        json_schema_extra=bare_object_schema,
    )


def _record_skill_write(op: str, skill_id: str) -> None:
    """Report a skill write onto the turn's receipt (a no-op outside an assistant turn).

    This choke lives in ``ai/`` rather than ``services/``, which is exactly why the
    collection seam is in ``core`` — ``ai → core`` is a downward import under contract 1,
    and a services-layer-only collector would simply never see this write. Never raises."""
    try:
        from backend.core.write_receipt import record_write

        record_write(op, skill_id, entity_type="skill")
    except Exception:  # noqa: BLE001
        pass


def _apply_skill_change(input_data: dict) -> tuple[str, None]:
    store = _store()
    ctype = input_data.get("type")
    if ctype == "skill_create":
        raw_skill = input_data.get("skill") or {}
        skill = {k: v for k, v in raw_skill.items() if k in _AI_EDITABLE_FIELDS}
        problems = skills_service.validate(skill)
        if problems:
            return json.dumps({"ok": False, "error": "; ".join(problems)}), None
        files = raw_skill.get("files") or None
        problems = skill_files.file_problems(files)
        if problems:
            return json.dumps({"ok": False, "error": "; ".join(problems)}), None
        created = store.create({**skill, "author": "lucius"})
        skill_files.write_files((created or {}).get("id") or "", files)
        _record_skill_write("create", (created or {}).get("id") or "")
        return json.dumps({"ok": True, "skill": created}, ensure_ascii=False), None
    if ctype == "skill_update":
        raw_changes = input_data.get("changes") or {}
        changes = {k: v for k, v in raw_changes.items() if k in _AI_EDITABLE_FIELDS}
        files = raw_changes.get("files") or None
        file_problems = skill_files.file_problems(files)
        if file_problems:
            return json.dumps({"ok": False, "error": "; ".join(file_problems)}), None
        sid = input_data.get("skill_id", "")
        # Resolve a {old,new} body EDIT against what is stored RIGHT NOW — not against what
        # the propose step saw. If the user hand-edited this skill while the confirm card was
        # on screen, `old` no longer matches and this REFUSES rather than reverting their
        # work. That re-resolution is the whole lost-update guarantee (see core.text_edit).
        existing = store.find_by_id(sid)
        if existing is None:
            return json.dumps({"ok": False, "error": f"unknown skill_id: {sid}"}), None
        try:
            changes = resolve_text_edits(
                changes, existing, fields=("body",), read_tool="list_skills(skill_id=…)"
            )
        except TextEditError as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), None
        problems = skills_service.validate({**existing, **changes})
        if problems:
            return json.dumps({"ok": False, "error": "; ".join(problems)}), None
        updated = store.update_skill(sid, changes)
        if updated is not None:
            skill_files.write_files(sid, files)
            _record_skill_write("update", sid)
        return json.dumps({"ok": updated is not None, "skill": updated}, ensure_ascii=False), None
    if ctype == "skill_delete":
        skill_id = input_data.get("skill_id", "")
        ok = store.delete(skill_id)
        if ok:
            _record_skill_write("delete", skill_id)
        return json.dumps({"ok": ok}), None
    return json.dumps({"ok": False, "error": f"unknown change type: {ctype}"}), None
