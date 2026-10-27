"""Applying a reviewed memory import.

The items arrive from the browser, so none of it is trusted: every one is re-validated here,
whatever the preview said. Memory lines go through the write choke as ``explicit``; the few
profile fields below go through the profile writer; instruction lines are appended to the
profile's ``notes`` (never replacing them). Anything else comes back in ``rejected`` with a
reason. ADR ``2026-10-25-memory-keeps-only-what-has-no-other-home`` (amendment 2's import flow).
"""

from __future__ import annotations

import logging

from pydantic import ValidationError as PydanticValidationError

from backend.ai import memory_structure as ms
from backend.ai.memory_write import apply_memory_change, has_identifier
from backend.db import MemoryStore
from backend.services import profile as profile_service

logger = logging.getLogger(__name__)

MAX_ITEMS = 300
TEXT_FIELD_MAX_CHARS = 80

TEXT_FIELDS = ("name", "nickname")
ENUM_FIELDS = ("marital_status", "risk_tolerance")
LIST_FIELDS = ("employment_types",)
IMPORTABLE_PROFILE_FIELDS = (*TEXT_FIELDS, *ENUM_FIELDS, *LIST_FIELDS)

DESTINATIONS = ("profile", "instructions", "memory", "skip")


def _squash(value) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def clean_profile_value(field, value):
    """The value to store for an importable profile field, or None when it is not valid."""
    if field not in IMPORTABLE_PROFILE_FIELDS:
        return None
    if field in TEXT_FIELDS:
        text = _squash(value)
        if not text or len(text) > TEXT_FIELD_MAX_CHARS or has_identifier(text):
            return None
        return text
    if field in LIST_FIELDS:
        parts = value if isinstance(value, list) else str(value or "").split(",")
        cleaned: object = [_squash(p).lower().replace(" ", "_") for p in parts if _squash(p)]
        if not cleaned:
            return None
    else:
        cleaned = _squash(value).lower().replace(" ", "_")
    from backend.ai.tools.profile import UpdateProfileChanges

    try:
        UpdateProfileChanges.model_validate({field: cleaned})
    except PydanticValidationError:
        return None
    return cleaned


def _reject(rejected: list[dict], item, destination: str, reason: str) -> None:
    line = item.get("line") if isinstance(item, dict) else None
    rejected.append(
        {
            "line": line if isinstance(line, str) else "",
            "destination": destination,
            "reason": reason,
        }
    )


def _instruction_lines(notes: str) -> set[str]:
    return {_squash(line).lstrip("-• ").casefold() for line in notes.splitlines() if line.strip()}


def _stage_profile(item: dict, changes: dict, rejected: list[dict]) -> bool:
    field = item.get("field")
    value = clean_profile_value(field, item.get("value"))
    if value is None:
        _reject(rejected, item, "profile", "bad_field")
        return False
    changes[field] = value
    return True


def _stage_instruction(item: dict, notes: str, seen: set[str], additions: list[str], rej) -> bool:
    text = _squash(item.get("value"))
    if not text:
        _reject(rej, item, "instructions", "empty")
    elif len(text) > ms.FACT_MAX_CHARS:
        _reject(rej, item, "instructions", "too_long")
    elif has_identifier(text):
        _reject(rej, item, "instructions", "identifier")
    elif text.lstrip("-• ").casefold() in seen:
        _reject(rej, item, "instructions", "already_there")
    else:
        seen.add(text.lstrip("-• ").casefold())
        additions.append(text)
        return True
    return False


def _write_profile(changes: dict, additions: list[str]) -> None:
    profile = profile_service.read_profile()
    profile.update(changes)
    if additions:
        existing = str(profile.get("notes") or "").rstrip()
        profile["notes"] = "\n".join(([existing] if existing else []) + additions)
    profile_service.write_profile(profile)


def _memory_op(item: dict, live: set[tuple[str, str]], rejected: list[dict]) -> dict | None:
    content = _squash(item.get("content") or item.get("line"))
    if not content:
        _reject(rejected, item, "", "malformed")
        return None
    section = ms.normalise_section(item.get("section"))
    if section is not None and (section, content.casefold()) in live:
        _reject(rejected, item, "memory", "duplicate")
        return None
    if section is not None:
        live.add((section, content.casefold()))
    op = {"content": content, "section": item.get("section"), "topic": item.get("topic")}
    if item.get("sensitive") is True:
        op["sensitive"] = True
    return op


def apply_import(items) -> dict:
    """Write the reviewed ``items`` and return ``{"applied": {profile, instructions, memory},
    "rejected": [{line, destination, reason}]}``."""
    applied = {"profile": 0, "instructions": 0, "memory": 0}
    rejected: list[dict] = []
    changes: dict = {}
    additions: list[str] = []
    memory_items: list[dict] = []
    notes = str(profile_service.read_profile().get("notes") or "")
    seen = _instruction_lines(notes)
    live = {
        (f["section"], _squash(f.get("content")).casefold())
        for f in MemoryStore().active_facts()
        if f.get("section")
    }
    for item in (items if isinstance(items, list) else [])[:MAX_ITEMS]:
        dest = item.get("destination") if isinstance(item, dict) else None
        if dest not in DESTINATIONS:
            _reject(rejected, item, "", "malformed")
        elif dest == "skip":
            _reject(rejected, item, "skip", "skipped")
        elif dest == "profile":
            applied["profile"] += _stage_profile(item, changes, rejected)
        elif dest == "instructions":
            applied["instructions"] += _stage_instruction(item, notes, seen, additions, rejected)
        else:
            op = _memory_op(item, live, rejected)
            if op:
                memory_items.append({"item": item, "op": op})
    if changes or additions:
        try:
            _write_profile(changes, additions)
        except Exception:
            logger.warning("memory import: the profile write failed", exc_info=True)
            for dest in ("profile", "instructions"):
                for _ in range(applied[dest]):
                    rejected.append({"line": "", "destination": dest, "reason": "write_failed"})
                applied[dest] = 0
    if memory_items:
        result = apply_memory_change(
            {"new": [m["op"] for m in memory_items]}, source="explicit", strict=False
        )
        applied["memory"] = result["added"]
        by_content = {m["op"]["content"]: m["item"] for m in memory_items}
        for r in result["rejected"]:
            _reject(rejected, by_content.get(r.get("content"), {}), "memory", r["reason"])
    return {"applied": applied, "rejected": rejected}
