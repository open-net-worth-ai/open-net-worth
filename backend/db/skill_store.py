"""
SkillStore — file-first skill registry backed by a folder-per-skill layout.

A skill IS a folder: DATA_DIR/skills/<skill_id>/
  SKILL.md          — standard Agent-Skill frontmatter + body. THE skill. Mutable.
  scripts/          — runnable scripts (language by extension or shebang)
  references/       — docs read on demand (progressive disclosure)
  assets/           — static files preserved verbatim

``agents/openai.yaml`` is optional and carries OpenAI UI/dependency/invocation metadata.
A skill carries NO tool list: the experimental ``allowed-tools`` field (like Claude Code's
``disallowed-tools``) is dropped on read, so the next write removes it from a legacy file.
A skill never changes the ordinary tool offer or policy.

Runtime state (non-content, never content-truth) lives in ONE small flat file
DATA_DIR/skills_state.json keyed by skill id:
  {enabled, author, created_at, updated_at, invocation_count}

has_scripts is DERIVED live from scripts/ presence.

Jail: folder names are slugified ids; path-traversal refused via _resolve_in_folder.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import stat
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Iterator

from backend.core.anchored_dir import AnchoredDir, open_dir
from backend.core.errors import DataIntegrityError, ValidationError
from backend.core.io import (
    data_maintenance_lock,
    fire_write_hook,
    fsync_directory,
)
from backend.core.paths import SKILLS_DIR, SKILLS_STATE_JSON
from backend.core.user_time import user_today
from backend.db.skill_files import (
    _user_metadata,
    build_openai_yaml,
    build_skill_md,
    clean_claude_extensions,
    parse_claude_extensions,
    parse_openai_yaml,
    parse_skill_md,
    validate_openai_yaml,
    validate_skill_md_frontmatter,
)
from backend.db.skill_state_store import SkillStateStore

logger = logging.getLogger(__name__)

AUTHORS = {"builtin", "user", "lucius"}

# Top-level fields a PUT/update_skill may patch (id/created_at/author are immutable).
EDITABLE_FIELDS = {
    "name",
    "description",
    "body",
    "license",
    "compatibility",
    "metadata",
    "claude",
    "openai",
    "enabled",
}

MAX_SKILL_NAME_LEN = 64
_SKILL_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

_STATE_FIELDS = frozenset({"enabled", "author", "created_at", "updated_at", "invocation_count"})
_REQUIRED_STATE_FIELDS = frozenset({"enabled", "author", "created_at", "updated_at"})
_STATE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _today() -> str:
    return user_today().isoformat()


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def normalize_skill_name(name: str) -> str:
    """Coerce a skill name to the Agent-Skill slug constraint (lowercase a-z0-9-, ≤64)."""
    return _slugify(name)[:MAX_SKILL_NAME_LEN].strip("-")


def validate_skill_id(sid: str) -> str:
    """Return a canonical skill id or fail before it can become a path segment."""
    if (
        not isinstance(sid, str)
        or not sid
        or len(sid) > MAX_SKILL_NAME_LEN
        or _SKILL_ID_RE.fullmatch(sid) is None
    ):
        raise ValidationError(
            "skill_id must be a canonical slug: lowercase letters/numbers separated by "
            "single hyphens (maximum 64 characters)",
            skill_id=sid,
        )
    return sid


def _str_map(value) -> dict:
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items()}


def is_scripted(skill: dict) -> bool:
    """True iff the skill's folder contains a scripts/ directory."""
    return bool(skill.get("has_scripts"))


# ── State (skills_state table, WAVE 5C JSON->DB) ───────────────────────────────

_state_store = SkillStateStore()


def _load_state() -> dict:
    """Every skill's state row, keyed by skill id — fail-closed shape validation on the
    way out. A DB-backed store has no file-corruption failure mode to opt in or out of (a
    read either succeeds or the database itself is broken — CLAUDE.md's storage-engine
    invariant), so this no longer quarantines bytes; the row-shape checks below stay,
    guarding the same "never default-enable or silently coerce a malformed row" contract
    against a genuine software bug (a hand-edited row, an incomplete migration) rather
    than on-disk corruption."""
    state = _state_store.all_as_dict()
    try:
        for sid, row in state.items():
            validate_skill_id(sid)
            if not isinstance(row, dict):
                raise ValueError("every skill state row must be an object")
            missing = _REQUIRED_STATE_FIELDS - set(row)
            if missing:
                raise ValueError(f"missing required skill state fields: {sorted(missing)}")
            unknown = set(row) - _STATE_FIELDS
            if unknown:
                raise ValueError(f"unknown skill state fields: {sorted(unknown)}")
            if "enabled" in row and type(row["enabled"]) is not bool:
                raise ValueError("skill state enabled must be a boolean")
            if "author" in row and row["author"] not in AUTHORS:
                raise ValueError("skill state author is invalid")
            count = row.get("invocation_count")
            if count is not None and (
                not isinstance(count, int) or isinstance(count, bool) or count < 0
            ):
                raise ValueError("skill state invocation_count must be a non-negative integer")
            for field in ("created_at", "updated_at"):
                value = row.get(field)
                if not isinstance(value, str) or _STATE_DATE_RE.fullmatch(value) is None:
                    raise ValueError(f"skill state {field} must be an ISO date")
                date.fromisoformat(value)
    except Exception as exc:
        raise DataIntegrityError(
            "skills_state failed validation; refusing to default-enable skills or overwrite "
            "runtime state — see the chained error for which row/field is malformed.",
            path="skills_state",
        ) from exc
    return state


def _save_state(state: dict) -> None:
    _state_store.replace_all_keyed(state)


def _complete_state_row(
    row: dict | None = None,
    *,
    enabled: bool | None = None,
    author: str | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
) -> dict:
    """Return one total fail-closed state row; absent legacy state is never enabled."""
    now = _today()
    complete = dict(row or {})
    complete.setdefault("enabled", False)
    complete.setdefault("author", "user")
    complete.setdefault("created_at", now)
    complete.setdefault("updated_at", now)
    if enabled is not None:
        complete["enabled"] = enabled
    if author is not None:
        complete["author"] = author
    if created_at is not None:
        complete["created_at"] = created_at
    if updated_at is not None:
        complete["updated_at"] = updated_at
    return complete


def _state_for(sid: str) -> dict:
    return _load_state().get(sid) or {}


@contextmanager
def skill_store_lock(*, exclusive: bool) -> Iterator[None]:
    """Hold the one cross-process lock protecting skill folders and runtime state."""
    with data_maintenance_lock(SKILLS_STATE_JSON.parent, exclusive=exclusive):
        yield


@dataclass
class PinnedSkillFolder:
    """One verified lexical skill entry pinned by parent/folder anchors and inode."""

    skill_id: str
    parent: AnchoredDir
    folder: AnchoredDir
    identity: tuple[int, int]

    @property
    def path(self) -> Path:
        return SKILLS_DIR / self.skill_id

    def verify_identity(self) -> None:
        try:
            current = self.parent.stat(self.skill_id)
        except FileNotFoundError as exc:
            raise ValidationError(
                f"skill '{self.skill_id}' folder identity changed during the transaction",
                skill_id=self.skill_id,
            ) from exc
        if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != self.identity:
            raise ValidationError(
                f"skill '{self.skill_id}' folder identity changed during the transaction",
                skill_id=self.skill_id,
            )


@contextmanager
def pinned_skill_folder(
    sid: str,
    *,
    create: bool = False,
    must_create: bool = False,
) -> Iterator[PinnedSkillFolder]:
    """Open a lexical child without following it and keep its inode pinned until exit."""
    sid = validate_skill_id(sid)
    if must_create:
        create = True
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    parent = open_dir(SKILLS_DIR)
    folder: AnchoredDir | None = None
    try:
        try:
            metadata = parent.stat(sid)
        except FileNotFoundError:
            if not create:
                raise
            parent.mkdir(sid)
            parent.fsync()
            metadata = parent.stat(sid)
        else:
            if must_create:
                raise ValidationError(
                    f"skill '{sid}' folder appeared during create; refusing to overwrite it",
                    skill_id=sid,
                )
        if not stat.S_ISDIR(metadata.st_mode):
            kind = "symlink" if stat.S_ISLNK(metadata.st_mode) else "non-directory"
            raise ValidationError(
                f"skill '{sid}' root must not be a {kind}",
                skill_id=sid,
            )
        folder = parent.open_child(sid)
        pinned_metadata = folder.fstat()
        identity = (pinned_metadata.st_dev, pinned_metadata.st_ino)
        if identity != (metadata.st_dev, metadata.st_ino):
            raise ValidationError(
                f"skill '{sid}' folder identity changed while it was opened",
                skill_id=sid,
            )
        pinned = PinnedSkillFolder(sid, parent, folder, identity)
        pinned.verify_identity()
        yield pinned
    finally:
        if folder is not None:
            folder.close()
        parent.close()


def _relative_parts(relative: str) -> tuple[str, ...]:
    path = PurePosixPath(relative)
    if not relative or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValidationError(f"skill path '{relative}' escapes the skill folder", path=relative)
    return path.parts


def _open_relative_dir(root: AnchoredDir, parts: tuple[str, ...], *, create: bool) -> AnchoredDir:
    current = root.dup()
    try:
        for part in parts:
            if create:
                try:
                    current.mkdir(part)
                    current.fsync()
                except FileExistsError:
                    pass
            next_dir = current.open_child(part)
            current.close()
            current = next_dir
        return current
    except Exception:
        current.close()
        raise


def write_pinned_skill_bytes(folder: PinnedSkillFolder, relative: str, data: bytes) -> None:
    """Atomically replace one regular file relative to a pinned skill directory."""
    folder.verify_identity()
    parts = _relative_parts(relative)
    directory = _open_relative_dir(folder.folder, parts[:-1], create=True)
    filename = parts[-1]
    temp_name = f".{filename}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    descriptor: int | None = None
    fire_write_hook(folder.path / relative)
    try:
        try:
            existing = directory.stat(filename)
        except FileNotFoundError:
            existing = None
        if existing is not None and not stat.S_ISREG(existing.st_mode):
            raise ValidationError(f"skill path '{relative}' must be a regular file")
        descriptor = directory.open_file(temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        offset = 0
        while offset < len(data):
            offset += os.write(descriptor, data[offset:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        directory.replace(temp_name, filename)
        directory.fsync()
        folder.verify_identity()
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        try:
            directory.unlink(temp_name)
        except OSError:
            pass
        raise
    finally:
        directory.close()


def write_pinned_skill_text(folder: PinnedSkillFolder, relative: str, content: str) -> None:
    """UTF-8 text wrapper over the byte-preserving pinned writer."""
    write_pinned_skill_bytes(folder, relative, content.encode("utf-8"))


def remove_pinned_skill_file(folder: PinnedSkillFolder, relative: str) -> None:
    """Remove one regular file below a pinned skill folder; missing is a no-op."""
    folder.verify_identity()
    parts = _relative_parts(relative)
    try:
        directory = _open_relative_dir(folder.folder, parts[:-1], create=False)
    except FileNotFoundError:
        return
    try:
        try:
            existing = directory.stat(parts[-1])
        except FileNotFoundError:
            return
        if not stat.S_ISREG(existing.st_mode):
            raise ValidationError(f"skill path '{relative}' must be a regular file")
        fire_write_hook(folder.path / relative)
        directory.unlink(parts[-1])
        directory.fsync()
        folder.verify_identity()
    finally:
        directory.close()


def read_pinned_skill_bytes(folder: PinnedSkillFolder, relative: str) -> bytes | None:
    """Read one regular file beneath a pinned skill directory without following symlinks."""
    parts = _relative_parts(relative)
    try:
        directory = _open_relative_dir(folder.folder, parts[:-1], create=False)
    except FileNotFoundError:
        return None
    try:
        try:
            before = directory.stat(parts[-1])
            descriptor = directory.open_file(parts[-1], os.O_RDONLY)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ValidationError(f"skill path '{relative}' must be a regular file") from exc
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
                before.st_dev,
                before.st_ino,
            ):
                raise ValidationError(f"skill file '{relative}' identity changed while reading")
            chunks: list[bytes] = []
            while chunk := os.read(descriptor, 1024 * 1024):
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            os.close(descriptor)
    finally:
        directory.close()


def read_pinned_skill_text(folder: PinnedSkillFolder, relative: str) -> str | None:
    data = read_pinned_skill_bytes(folder, relative)
    return None if data is None else data.decode("utf-8")


def pinned_skill_tree(folder: PinnedSkillFolder) -> dict[str, bytes]:
    """Snapshot every regular file below a pinned folder; reject every symlink."""
    files: dict[str, bytes] = {}

    def walk(directory: AnchoredDir, prefix: tuple[str, ...]) -> None:
        for name in sorted(directory.listdir()):
            metadata = directory.stat(name)
            relative = "/".join((*prefix, name))
            if stat.S_ISLNK(metadata.st_mode):
                raise ValidationError(f"skill path '{relative}' must not be a symlink")
            if stat.S_ISDIR(metadata.st_mode):
                with directory.open_child(name) as child:
                    opened = child.fstat()
                    if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                        raise ValidationError(
                            f"skill directory '{relative}' identity changed while reading"
                        )
                    walk(child, (*prefix, name))
            elif stat.S_ISREG(metadata.st_mode):
                data = read_pinned_skill_bytes(folder, relative)
                if data is not None:
                    files[relative] = data
            else:
                raise ValidationError(f"unsupported skill file type: '{relative}'")

    walk(folder.folder, ())
    folder.verify_identity()
    return files


def pinned_directory_has_entries(folder: PinnedSkillFolder, relative: str) -> bool:
    parts = _relative_parts(relative)
    try:
        directory = _open_relative_dir(folder.folder, parts, create=False)
    except FileNotFoundError:
        return False
    try:
        return bool(directory.listdir())
    finally:
        directory.close()


def _remove_pinned_tree(directory: AnchoredDir) -> None:
    """Remove children through pinned anchors without following any entry."""
    for name in sorted(directory.listdir()):
        metadata = directory.stat(name)
        if stat.S_ISDIR(metadata.st_mode):
            with directory.open_child(name) as child:
                opened = child.fstat()
                identity = (opened.st_dev, opened.st_ino)
                if identity != (metadata.st_dev, metadata.st_ino):
                    raise ValidationError(
                        f"skill directory '{name}' identity changed during delete"
                    )
                _remove_pinned_tree(child)
                current = directory.stat(name)
                if (current.st_dev, current.st_ino) != identity:
                    raise ValidationError(
                        f"skill directory '{name}' identity changed during delete"
                    )
            directory.rmdir(name)
        else:
            directory.unlink(name)
    directory.fsync()


def _mutate_state(mutate):
    """Serialize one full skills-state read-modify-write across threads/processes."""
    with skill_store_lock(exclusive=True):
        state = _load_state()
        changed, result = mutate(state)
        if changed:
            _save_state(state)
        return result


def _update_state(sid: str, patch: dict) -> None:
    def apply(state: dict) -> tuple[bool, None]:
        row = _complete_state_row(state.get(sid))
        row.update(patch)
        state[sid] = row
        return True, None

    _mutate_state(apply)


# ── Folder I/O helpers ────────────────────────────────────────────────────────


def _write_skill_folder(
    folder: Path,
    record: dict,
    *,
    pinned: PinnedSkillFolder | None = None,
) -> None:
    """Write the portable skill definition into ``folder``."""
    skill_md = build_skill_md(record)
    if pinned is not None:
        write_pinned_skill_text(pinned, "SKILL.md", skill_md)
        openai_yaml = build_openai_yaml(record.get("openai"))
        if openai_yaml:
            write_pinned_skill_text(pinned, "agents/openai.yaml", openai_yaml)
        else:
            remove_pinned_skill_file(pinned, "agents/openai.yaml")
        return
    folder.mkdir(parents=True, exist_ok=True)
    with pinned_skill_folder(record["id"]) as opened:
        write_pinned_skill_text(opened, "SKILL.md", skill_md)
        opened.verify_identity()


def _read_skill_folder(
    folder: Path,
    sid: str,
    *,
    state: dict | None = None,
    pinned: PinnedSkillFolder | None = None,
) -> dict | None:
    """Assemble a skill record dict from the folder + state. Returns None if missing."""
    if pinned is None:
        try:
            with pinned_skill_folder(sid) as opened:
                return _read_skill_folder(folder, sid, state=state, pinned=opened)
        except FileNotFoundError:
            return None
    skill_md_text = read_pinned_skill_text(pinned, "SKILL.md")
    if skill_md_text is None:
        return None
    meta, body = parse_skill_md(skill_md_text)
    openai_yaml_text = read_pinned_skill_text(pinned, "agents/openai.yaml")
    format_problems = validate_skill_md_frontmatter(meta) + validate_openai_yaml(openai_yaml_text)
    openai = parse_openai_yaml(openai_yaml_text)
    state_row = (state if state is not None else _load_state()).get(sid)
    # has_scripts is live: check folder
    has_scripts = pinned_directory_has_entries(pinned, "scripts")
    record: dict = {
        "id": sid,
        "name": str(meta.get("name") or sid),
        "description": str(meta.get("description") or ""),
        "body": body,
        "license": str(meta.get("license") or ""),
        "compatibility": str(meta.get("compatibility") or ""),
        "metadata": _str_map(_user_metadata(meta)),
        "claude": parse_claude_extensions(meta),
        "openai": openai,
        # Direct filesystem edits bypass the API/import validators. Never let a lossy parse of
        # malformed invocation controls restore permissive defaults; keep the row visible so the
        # Skills editor can repair it, but make every invocation path fail closed.
        "enabled": (state_row["enabled"] if state_row is not None else False)
        and not format_problems,
        "author": state_row["author"] if state_row is not None else "user",
        "created_at": state_row["created_at"] if state_row is not None else _today(),
        "updated_at": state_row["updated_at"] if state_row is not None else _today(),
    }
    if has_scripts:
        record["has_scripts"] = True
    if format_problems:
        record["validation_errors"] = format_problems
    pinned.verify_identity()
    return record


class SkillStore:
    def __init__(self) -> None:
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    # ── Create ────────────────────────────────────────────────────────────────

    def create(self, data: dict) -> dict:
        """Build and persist a skill folder + state row. Returns the assembled record."""
        name = normalize_skill_name(data.get("name") or "")
        candidate = validate_skill_id(
            data.get("id") or name or _slugify(data.get("description", "")) or "skill"
        )
        author = data.get("author")
        if author not in AUTHORS:
            author = "user"
        now = _today()
        with skill_store_lock(exclusive=True):
            state = _load_state()
            sid = self._unique_id(candidate)
            record = {
                "id": sid,
                "name": name or sid,
                # API/import boundaries require an explicit description. The low-level store is
                # also used by built-in installers and tests, so keep its historical name-only
                # call shape while still writing a valid Agent Skills file.
                "description": str(data.get("description") or name or sid),
                "body": data.get("body", ""),
                "license": data.get("license", ""),
                "compatibility": str(data.get("compatibility") or ""),
                "metadata": _str_map(data.get("metadata")),
                "claude": clean_claude_extensions(data.get("claude")),
                "openai": dict(data.get("openai") or {})
                if isinstance(data.get("openai"), dict)
                else {},
                "enabled": bool(data.get("enabled", True)),
                "author": author,
                "created_at": now,
                "updated_at": now,
            }
            # The disabled row is durable before any content can become visible. A folder or
            # final-state failure therefore leaves a non-invocable tombstone.
            state[sid] = _complete_state_row(
                enabled=False,
                author=author,
                created_at=record["created_at"],
                updated_at=now,
            )
            _save_state(state)
            with pinned_skill_folder(sid, must_create=True) as pinned:
                _write_skill_folder(pinned.path, record, pinned=pinned)
                pinned.verify_identity()
                fsync_directory(SKILLS_DIR)
                state[sid]["enabled"] = record["enabled"]
                _save_state(state)
                return _read_skill_folder(pinned.path, sid, state=state, pinned=pinned) or record

    def _unique_id(self, candidate: str) -> str:
        existing = (
            {f.name for f in SKILLS_DIR.iterdir() if f.is_dir()} if SKILLS_DIR.exists() else set()
        )
        if candidate not in existing:
            return candidate
        n = 2
        while f"{candidate}-{n}" in existing:
            n += 1
        return f"{candidate}-{n}"

    # ── Update ────────────────────────────────────────────────────────────────

    def update_skill(self, sid: str, changes: dict) -> dict | None:
        """Patch editable fields and rewrite SKILL.md in place. Stamp ``updated_at``."""
        sid = validate_skill_id(sid)
        clean = {k: v for k, v in changes.items() if k in EDITABLE_FIELDS}
        if "name" in clean:
            normalized = normalize_skill_name(clean["name"])
            if normalized:
                clean["name"] = normalized
            else:
                clean.pop("name")
        if "license" in clean:
            clean["license"] = str(clean["license"] or "")
        if "compatibility" in clean:
            clean["compatibility"] = str(clean["compatibility"] or "")
        if "metadata" in clean:
            clean["metadata"] = _str_map(clean["metadata"])
        if "claude" in clean:
            clean["claude"] = clean_claude_extensions(clean["claude"])
        if "openai" in clean:
            clean["openai"] = dict(clean["openai"]) if isinstance(clean["openai"], dict) else {}
        if "enabled" in clean:
            clean["enabled"] = bool(clean["enabled"])
        clean["updated_at"] = _today()
        with skill_store_lock(exclusive=True):
            state = _load_state()
            try:
                with pinned_skill_folder(sid) as pinned:
                    existing = _read_skill_folder(pinned.path, sid, state=state, pinned=pinned)
                    if existing is None:
                        return None
                    merged = {**existing, **clean}
                    final_enabled = clean.get("enabled", existing["enabled"])
                    state_row = _complete_state_row(
                        state.get(sid), enabled=False, updated_at=clean["updated_at"]
                    )
                    state[sid] = state_row
                    _save_state(state)
                    pinned.verify_identity()
                    _write_skill_folder(pinned.path, merged, pinned=pinned)
                    pinned.verify_identity()
                    state_row["enabled"] = final_enabled
                    _save_state(state)
                    return _read_skill_folder(pinned.path, sid, state=state, pinned=pinned)
            except FileNotFoundError:
                return None

    # ── Read ──────────────────────────────────────────────────────────────────

    @contextmanager
    def pinned_snapshot(self, sid: str) -> Iterator[tuple[dict | None, PinnedSkillFolder | None]]:
        """Hold one descriptor-pinned record snapshot for invocation/bundle activation."""
        if not sid:
            yield None, None
            return
        with skill_store_lock(exclusive=False):
            state = _load_state()
            try:
                with pinned_skill_folder(sid) as pinned:
                    record = _read_skill_folder(pinned.path, sid, state=state, pinned=pinned)
                    pinned.verify_identity()
                    yield record, pinned
                    pinned.verify_identity()
            except FileNotFoundError:
                yield None, None

    def find_by_id(self, sid: str) -> dict | None:
        if not sid:
            return None
        with skill_store_lock(exclusive=False):
            state = _load_state()
            try:
                with pinned_skill_folder(sid) as pinned:
                    record = _read_skill_folder(pinned.path, sid, state=state, pinned=pinned)
                    pinned.verify_identity()
                    return record
            except FileNotFoundError:
                return None

    def all(self) -> list[dict]:
        with skill_store_lock(exclusive=False):
            state = _load_state()
            if not SKILLS_DIR.exists():
                return []
            result = []
            for folder in sorted(SKILLS_DIR.iterdir()):
                if not folder.is_dir():
                    continue
                with pinned_skill_folder(folder.name) as pinned:
                    record = _read_skill_folder(
                        pinned.path, folder.name, state=state, pinned=pinned
                    )
                    pinned.verify_identity()
                    if record is not None:
                        result.append(record)
            return result

    def query_skills(
        self,
        enabled: bool | None = None,
        author: str | None = None,
    ) -> list[dict]:
        items = self.all()
        if enabled is not None:
            items = [s for s in items if bool(s.get("enabled", True)) == enabled]
        if author is not None:
            items = [s for s in items if s.get("author") == author]
        return items

    def invocation_counts(self) -> dict[str, int]:
        """Return persisted positive invocation counts keyed by skill id."""
        counts: dict[str, int] = {}
        with skill_store_lock(exclusive=False):
            for sid, row in _load_state().items():
                count = row.get("invocation_count", 0)
                if count > 0:
                    counts[sid] = count
        return counts

    def increment_invocation_count(self, sid: str) -> int:
        """Atomically increment one existing skill's persisted invocation count."""
        if not sid:
            return 0

        def apply(state: dict) -> tuple[bool, int]:
            try:
                with pinned_skill_folder(sid) as pinned:
                    if read_pinned_skill_bytes(pinned, "SKILL.md") is None:
                        return False, 0
                    pinned.verify_identity()
                    row = _complete_state_row(state.get(sid))
                    current = row.get("invocation_count", 0)
                    if not isinstance(current, int) or isinstance(current, bool) or current < 0:
                        current = 0
                    row["invocation_count"] = current + 1
                    state[sid] = row
                    return True, row["invocation_count"]
            except FileNotFoundError:
                return False, 0

        return _mutate_state(apply)

    # ── Delete ────────────────────────────────────────────────────────────────

    def delete(self, sid: str) -> bool:
        sid = validate_skill_id(sid)
        with skill_store_lock(exclusive=True):
            state = _load_state()
            try:
                with pinned_skill_folder(sid) as pinned:
                    if read_pinned_skill_bytes(pinned, "SKILL.md") is None:
                        return False
                    state_row = _complete_state_row(
                        state.get(sid), enabled=False, updated_at=_today()
                    )
                    state[sid] = state_row
                    _save_state(state)
                    pinned.verify_identity()
                    tombstone = f".deleting-{sid}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
                    renamed = False
                    parent = pinned.parent

                    def restore_tombstone() -> None:
                        """Put any wrongly renamed or partially removed entry back if possible."""
                        try:
                            parent.stat(tombstone)
                        except FileNotFoundError:
                            return
                        try:
                            parent.stat(sid)
                        except FileNotFoundError:
                            parent.rename(tombstone, sid)
                            parent.fsync()

                    try:
                        parent.rename(sid, tombstone)
                        renamed = True
                        moved = parent.stat(tombstone)
                        if (moved.st_dev, moved.st_ino) != pinned.identity:
                            raise ValidationError(
                                f"skill '{sid}' folder identity changed during delete",
                                skill_id=sid,
                            )
                        with parent.open_child(tombstone) as moved_folder:
                            opened = moved_folder.fstat()
                            if (opened.st_dev, opened.st_ino) != pinned.identity:
                                raise ValidationError(
                                    f"skill '{sid}' folder identity changed during delete",
                                    skill_id=sid,
                                )
                            _remove_pinned_tree(moved_folder)
                        current = parent.stat(tombstone)
                        if (current.st_dev, current.st_ino) != pinned.identity:
                            raise ValidationError(
                                f"skill '{sid}' folder identity changed during delete",
                                skill_id=sid,
                            )
                        parent.rmdir(tombstone)
                        renamed = False
                        parent.fsync()
                    except FileNotFoundError as exc:
                        if renamed:
                            restore_tombstone()
                        raise ValidationError(
                            f"skill '{sid}' folder identity changed during delete",
                            skill_id=sid,
                        ) from exc
                    except Exception:
                        if renamed:
                            restore_tombstone()
                        raise
                state.pop(sid, None)
                _save_state(state)
                return True
            except FileNotFoundError:
                return False

    # ── JsonStore back-compat passthrough ─────────────────────────────────────

    def insert(self, record: dict) -> dict:
        """Back-compat alias used by some call sites; routes to create()."""
        return self.create(record)

    # ── Starter seeding ───────────────────────────────────────────────────────

    def seed_starters(self) -> int:
        """Insert built-in starter skills not already present (by id). Idempotent."""
        existing = (
            {f.name for f in SKILLS_DIR.iterdir() if f.is_dir()} if SKILLS_DIR.exists() else set()
        )
        inserted = 0
        for starter in STARTER_SKILLS:
            if starter["id"] in existing:
                continue
            self.create({**starter, "author": "builtin"})
            inserted += 1
        return inserted

    def restore_starters(self) -> None:
        """Return every built-in starter to its shipped state (author=builtin, enabled) after
        ``/api/reset`` wiped the state table but left the skill folders: a folder with no state
        row reads as a disabled user skill (``_complete_state_row`` fails closed), which broke
        the onboarding handoff. Seeds a missing folder like a fresh boot; a starter that still
        has a state row is untouched."""
        self.seed_starters()

        def apply(state: dict) -> tuple[bool, None]:
            changed = False
            for starter in STARTER_SKILLS:
                sid = starter["id"]
                if sid in state or not (SKILLS_DIR / sid).is_dir():
                    continue
                state[sid] = _complete_state_row(enabled=True, author="builtin")
                changed = True
            return changed, None

        _mutate_state(apply)

    def refresh_superseded_starters(self) -> list[str]:
        """Replace each installed starter whose body is a superseded built-in body its owner
        never edited (``_SUPERSEDED_STARTER_BODIES``) with the current starter — body,
        description, and no ``allowed-tools`` (old starters listed tools, which a model read as
        its whole toolset). Returns the refreshed ids. Idempotent: a refreshed body no longer
        matches, and an edited or deleted skill is never touched."""
        current = {s["id"]: s for s in STARTER_SKILLS}
        refreshed: list[str] = []
        for sid, stale in _SUPERSEDED_STARTER_BODIES.items():
            existing = self.find_by_id(sid)
            if existing is None or sid not in current:
                continue
            if _digest(existing.get("body")) not in stale:
                continue
            starter = current[sid]
            self.update_skill(
                sid,
                {
                    "body": starter["body"],
                    "description": starter["description"],
                    "allowed_tools": [],
                },
            )
            refreshed.append(sid)
        for sid, stale in _SUPERSEDED_STARTER_DESCRIPTIONS.items():
            existing = self.find_by_id(sid)
            if existing is None or sid not in current or sid in refreshed:
                continue
            if _digest(existing.get("description")) not in stale:
                continue
            self.update_skill(sid, {"description": current[sid]["description"]})
            refreshed.append(sid)
        return refreshed

    def retire_dropped_starters(self, keep: frozenset[str] = frozenset()) -> list[str]:
        """Delete each installed starter that no longer ships (``_RETIRED_STARTER_BODIES``)
        while its body is still a shipped one — its owner never edited it, so it is clutter that
        duplicates a current starter. Its replacement (``_RETIRED_STARTER_REPLACEMENTS``) is
        installed in its place when absent — boot seeds only an empty library, so an old install
        would otherwise lose the skill without gaining its successor. An edited copy, or an id
        in ``keep`` (one an Automation's text still names), stays. Returns the deleted ids;
        idempotent."""
        starters = {s["id"]: s for s in STARTER_SKILLS}
        retired: list[str] = []
        for sid, shipped in _RETIRED_STARTER_BODIES.items():
            if sid in keep or sid in starters:
                continue
            existing = self.find_by_id(sid)
            if existing is None or _digest(existing.get("body")) not in shipped:
                continue
            if not self.delete(sid):
                continue
            retired.append(sid)
            successor = starters.get(_RETIRED_STARTER_REPLACEMENTS.get(sid, ""))
            if successor is not None and self.find_by_id(successor["id"]) is None:
                self.create({**successor, "author": "builtin"})
        return retired

    def install_added_starters(self, offers=None) -> list[str]:
        """Install each starter shipped after this install was seeded (``_ADDED_STARTERS``)
        exactly once. The first call on an install records every other current starter as
        already offered, so a starter its owner deleted before this existed is not brought
        back. Returns the installed ids; idempotent."""
        from backend.db.starter_offer_store import StarterOfferStore

        offers = offers or StarterOfferStore()
        starters = {s["id"]: s for s in STARTER_SKILLS}
        offered = offers.offered() or set(starters) - set(_ADDED_STARTERS)
        installed: list[str] = []
        for sid in _ADDED_STARTERS:
            if sid in offered or sid not in starters:
                continue
            if self.find_by_id(sid) is None:
                self.create({**starters[sid], "author": "builtin"})
                installed.append(sid)
            offered.add(sid)
        offers.record(offered)
        return installed


def _digest(text: object) -> str:
    return hashlib.sha256(str(text or "").strip().encode()).hexdigest()


# ── Superseded starter bodies ──────────────────────────────────────────────────
# sha256 of each built-in body a starter has SHIPPED and since replaced (``body.strip()``).
# ``seed_starters`` only inserts a missing id, so without this an install keeps the body it
# was first seeded with forever. An installed copy whose body still hashes to one of these was
# never edited by its owner, so it is replaced by the current starter; an edited one is left
# alone. Add the OLD body's hash here whenever you rewrite a starter.
_SUPERSEDED_STARTER_BODIES: dict[str, frozenset[str]] = {
    # 2026-09-28: create owned destinations first, then verify that imported rows are
    # inside each account's tracking window and affect its displayed balance.
    "onboarding-new-user": frozenset(
        {
            "2ac4222e3f4d8b98f0e336d4adbbcf624a30f363554ed0015c75a54dd40227fe",
            "e2218e1f63a06f287ab3d3c1162f63b3bbfdcf8735870658f7cbe9fa5c4aaef9",
            # 2026-09-28: the first greeting listed too many capabilities before asking a name.
            "3631732f452e3ca425d1581674d3c2c248287bb0cc0d87c8ec945238a06a3510",
            # 2026-10-01 (aa2e23dd1): the body as the starters moved to folders.
            "39353a2e0ac30da2825305c2960ec6111a21ef4a41368b0d429cafff06dcbb06",
            # 2026-10-09 (b4a313ecf): shorter onboarding, before screenshots-first.
            "f59edc759fe7900a9f946c6479f58168cf93ed5c225e204e04b89476953cc426",
            # 2026-10-10 (11fcceefc, 540ad033d, c829c02d5): screenshots first, in-app links,
            # connector status — each superseded the same day.
            "7788a7d8761b6305421b6d938b43342b02fce9a84138b6d76668dae2ef9d51da",
            "575fd304252d18a4383e6199858e78b5c5e5ca259144e3ab0555dd673d9abb20",
            "83635cbf119538eb856d2bb50c229b1d98d085897a3840bc37ca99b4c6f551ad",
            # 2026-10-11: asked five personal questions before any account; now name, then
            # what they want first, value before questions, procedures in references/.
            "802d130af62ecb4d6811208016d7e80145d91f95346ecbc3bc4ba1efa3d6baa6",
            # 2026-10-11 (b660d3729): led with a statement; now today's balances by default.
            "e67cce2de93f1c551792e15fd19b0133241af91cb5cecd8aa0569fcdce563de9",
            # 2026-10-11 (386d3faf6): before the Gotchas section and the trigger description.
            "bed09dd7c7e5af84c6b294a539e0af103656c97ddacf7f8e56b6de960f56c8e0",
        }
    ),
    # 2026-09-26: read the non-existent profile.monthly_budgets and wrote through
    # set_spending_limits (retired — no screen reads those limits). A live chat followed it,
    # never loaded a budget tool, and paired Zero-Based target amounts with envelope names by
    # guesswork.
    "budget-review": frozenset(
        {
            "5fca77e7c02b9dfe7a63f0600fbe3bd8af5bffb644f954749bc336915d106961",
            # 2026-09-26 (0e6746480): named apply_budget_verb, renamed apply_budget_operation.
            "170b13c8285236650e1a85922c2d7bb3d6e2c9475a2de9d32b084f2fb26a9e9b",
            # 2026-09-26 (a1b5d0e23): read 50/30/20 buckets, replaced by group targets.
            "bd72652390cd3e5350990a5d950c9f56ea44b25919d4d37514e032e7903262ed",
            # 2026-10-11: before the Gotchas section and the trigger description.
            "b3971ad74c22e75439d99922fd605c838bc46877b98eb2a75fafcdf2deb0a018",
            # 2026-10-11: the load step spelled as a find_tools(names=[...]) call.
            "b71fdcd00eb8063c2350d4ab8dd1dea4a28a171cb5de43ebe165955d5b86de15",
            # 2026-10-15: promised "a confirm card" on writes; whether one shows is the user's
            # permission setting, not the skill's. The skill now asks before writing.
            "fe63ffabfe0eaf972c062bfb92ddc44d5fa2f96cbb5b4c42fe2a1e859b4710fe",
        }
    ),
    # 2026-09-26: step 3 read get_target_allocation (savings goals, now get_goal_allocation)
    # for investment weights; the target mix is get_rebalance_plan's.
    "investment-checkup": frozenset(
        {
            "2f15b99a014257a0deb5d8c7c8cf061cba52dc0091ceb2c639df9fbbb9a5f9f4",
            # 2026-10-05: fee-analyzer folded in (fee drag ranked per holding).
            "e7ff67ea7a26e8fce514d246c2cb1372e2a47be80f402f685f3a118e78f3fcd0",
        }
    ),
    # 2026-10-05: clustered get_transactions by hand; now quotes get_recurring_streams. It
    # shipped from June until the starters moved to folders, then sat orphaned on installs.
    "subscription-audit": frozenset(
        {
            "f0094149fadd45bf799690c3717e9291ce747f6e294b3b64e4ead7484e72668b",
            # 2026-10-11: the load step spelled as a find_tools(names=[...]) call.
            "b79e3b755647c429588f8a5ea74cf9c6682405f5575b2ed32360734cd2e2386a",
            # 2026-10-15: promised "a confirm card" on writes; whether one shows is the user's
            # permission setting, not the skill's. The skill now asks before writing.
            "e87817dd01bce7de1ee452e9bff6de973420fc2cabb535b1fd803badd4ed8677",
        }
    ),
    # 2026-10-01: wrote the report HTML from scratch each run; now fills assets/report.html.
    # Then (same day): one-fact verdict, savings rate from the ledger summary, row caps; then
    # an investments section (return vs benchmark, realized, income, allocation, holdings P&L);
    # then personalised (name) and table-based so the same page renders as the delivered PDF;
    # then charts drawn by code, less text, nickname, and a human Telegram note.
    "weekly-report": frozenset(
        {
            # 2026-10-05: one-fact verdict headline (a fixed bill as "most of the week"), spending vs
            # the previous week only; now bills vs day-to-day against the usual week.
            "b4b05698e5a10f54c8c60eb0e5a637135e316ff3abfd1806d6ed16bc897f91a2",
            "664af7ae5ce523cd10b2fe57868db610714372a8f04d8cf5a6fd564b1cb77a31",
            "38eb4f9e0876a73416be338daf7514f8a6ad61b44a4d59b42479a05046565ea8",
            "5e0470e60ae720ef454644fb9981b0579086c2ad7ca0ba95a24b729372708d3e",
            "e3021525aee108766380885d3f6186a6142f54693dc282557e885c078b1ca367",
            "a9eb9cc5d19559cc256a6f943d7fee813cdffda07f73d37a69d4054d8f7066d0",
            "ba55ef86bdaca061e56e9b920c33dad2dddaa9f041fee709d4889bd8672f5039",
            "1e75c2a6c8eed65d60d6412b7cd084990aca4b471e76f8db540310445bd7c620",
            "ee9a3bb1236480824b71436ec3ce408be0392f5a10091e08ee71e2af1ccccf29",
            "e0e82e3ac02c997795729d1a9142928a5ae1434233a822044f6c7508fbcadcd0",
            "700b271a11a61d22e63cc9c4b8243ab33457f2cb1a70595ff363e896e6c3b1d9",
            "1851faf4f5530fbb642b29d54c27a5b6d7c9b0f246c4b7724020d550c6be19f9",
            "17584f8b030468a14f378d39aed1bcc47926c329ffd202cff31a5333399aba1e",
            "108f9ca04bc337f1f799ec7fced9da503a78e97c50af7a0e84f15022b19569c0",
            # 2026-10-06: a fixed slot form (sections, row caps, "never total rows"); now
            # principles + a component kit, and every trade via get_trades.
            "afe2e9196c793530b523bd6419fcffc5ab19b687bd762dff262c8ad85411e1f1",
            # 2026-10-07: live net worth as "current position", method narrated; now the
            # period only (period-end net worth or none) and findings, not method.
            "c469fcd513602d58b5acf0d838bd05d4a0b4cf7770854abd66444c84491f33dc",
            # 2026-10-07: charts optional, so a text-heavy page; now visual first.
            "a25fabe885da93c2e07d81ef731251e4306698bced36ab9331d10daba56b9f6f",
            # 2026-10-07: a free page grew to 1,266 words / 171 numbers / no bars; now the
            # designed page again, with limits the server enforces and room for extra sections.
            "9cca33791743e794c55500ef9535260c63a030a79059994a6fe082327284208f",
            # 2026-10-07: a data note invented how returns treat a broker transfer.
            "6a7f9b0ee43a989cb351a9b6e9eacc328dfb5c2a17b516aa5978b33c209a248f",
            # 2026-10-07: net worth from the month-end history only; now get_net_worth(as_of).
            "9045ad0308f6ff1f52a5fd866518f3d16603d7922b2a4b31d20a98724f2fddb3",
            # 2026-10-07: generic checks lived in the automation; now in the skill.
            "a268465b5a94d5d0b9cf2839ffe84ca942d4f555786f3b257b2a6889bd516379",
            # 2026-10-07: two spending baselines (usual week vs same week last month).
            "6a40249efa3ee007c3f29527940b14718982a6027f870c0640c881b14c97d5a7",
            # 2026-10-07: every recurring payment was "Bills"; now bills vs subscriptions.
            "b5b9b1d99f48911a92484af4e5e43437e90827f684fcbf7bf452de98b75dc9a0",
            # 2026-10-07: bills vs subscriptions by the model's judgement; now the user's own labels first.
            "5ec60ead8e01666fba3d0fece2a75c2a76d84180fedb91767b2574ace25b4664",
            # 2026-10-07: conversion fees listed as bills ("Small fees"); now their own Fees block.
            "ce9005a0181ed64f014189b7cc37248a060566bc7111f66142b545a1a9066721",
            # 2026-10-07: "Checks" (weekly only) + a Fees block; now "Worth a look" in both.
            "5e72af32fffaf21ac8f096835010c30df24a3c5ba455671647ed95f39a4d20c9",
            # 2026-10-06 (38978ddff): written around the week, before get_lots' window mode.
            "695c075c79eb9545d87cf6a19765eeb33f5f57fc7ea11a78adae0fc7cdbd5f7d",
            # 2026-10-07 (a623956ab): "Worth a look", before the 2026-10-09 rewrite.
            "e10f6be9ab6c0f96e720ad9278de6bf7731bd8d99a57923c259fe093ca6ef2a5",
            # 2026-10-11: "Facts about the data" after the framing; now the Gotchas lead.
            "1d2ef5b8e5a6d003ce90c08413a8741efea2636df466c4b801785f1897c0dd07",
        }
    ),
    "monthly-report": frozenset(
        {
            # 2026-10-05: one-fact verdict headline; spending vs the prior month only.
            "c9e080b7997e2739020b1d46924d01e93105cc967f8dff8836489780870ee02b",
            "22237c89b620ef81c2748f209576693bf63a8c4daefc9ae5241a44953c1a3069",
            "55ca60c4fe6ed37c6090c01954d83723797a9ae8920fc730e1344bbc700108a4",
            "82edc834cced3d225572233430a9cee62494c5a841b38b2b3dab6cc25c79d933",
            "df4ac1d0f99d5459b1d5bde9b5d06c144e106005fae5b1eb9806097f06072e26",
            "c893f68eb7f1e67bfb28b63362e6904afd3a5c6a492cfcf3472ad266d664054b",
            "31220a1cb022f3e87234c78361d0e99e64eac9ac5e532687444717ef38e26a41",
            "79ba071938ce21e64c8dd4e9cd9ef778796efd5e1127f2c87790c32201f58016",
            "b3a03b1341d44c8d59a2989cb9a09653e24435e3f47bc5f334a5a41cba490a19",
            "bbd80bee4cca7da613df158482982051408c1e22a98cdc46de64ce94fbdc5aa3",
            "f367a56e66381e72fda76b5c62e327c2501fa97b43041bbb9afbfb12d67df171",
            "c5cd102baaa5ff91f9d9504bac5b0f3eb1626a73968c480748b15651d03a156d",
            "3e8f62836be70f7826ad629829921485eb86fdaadc99ffb400472815f9f0e796",
            "77f616ec0ebccfbd85de99cf729ea84980fc4f322a28beb092c410dd8aa6d4d6",
            # 2026-10-06: a fixed slot form (sections, row caps, "never total rows"); now
            # principles + a component kit, and every trade via get_trades.
            "5f5e84243d3b93c4c67921452b069c48fe6848d95fd84232418a983dc0368314",
            # 2026-10-07: live net worth as "current position", method narrated; now the
            # period only (period-end net worth or none) and findings, not method.
            "55200df758b346cd205a56565d19e9cd5c41c9550f718cd80c283c6fb9d14fe1",
            # 2026-10-07: charts optional, so a text-heavy page; now visual first.
            "d7802967505bc453f84135806b6f140b9100d7cdf3222fbf78e47c4977465185",
            # 2026-10-07: a free page grew to 1,266 words / 171 numbers / no bars; now the
            # designed page again, with limits the server enforces and room for extra sections.
            "0a5ff9917873fb83c584b6f8b59a3c820a3f4e48d6c9a42164143b57ba868147",
            # 2026-10-07: a data note invented how returns treat a broker transfer.
            "a64ff835f67ffbd59518f22c4fbee774c5f2902f96387e8e73b33bab5986bef4",
            # 2026-10-07: net worth from the month-end history only; now get_net_worth(as_of).
            "2d16347df60d190fe21abc98bb9463e39f396cd5696efa05c8ce438b60b67942",
            # 2026-10-07: every recurring payment was "Bills"; now bills vs subscriptions.
            "67a715789b555eac49e1f930c03092a8254f220776c5a51233d87ba90a03cdea",
            # 2026-10-07: bills vs subscriptions by the model's judgement; now the user's own labels first.
            "dbb94b1df7b01f352040c719618b81b136fa41edef090ba61de2aaf22d38f927",
            # 2026-10-07: bills section optional and capped; now always present and complete.
            "01536dd682f2684a03b18735da6ce839668b6f9cb77cc260d843430d4fb20e4d",
            # 2026-10-07: conversion fees listed as bills ("Small fees"); now their own Fees block.
            "444defbd0243a335c4b7abc5a823e5eca24f622508c9e90010cc5eb895bc79e8",
            # 2026-10-07: "Checks" (weekly only) + a Fees block; now "Worth a look" in both.
            "1cb5a7c8a8bbec6f27103390e0b1ece4c4866d84955314870b5ad88d1c1a2be7",
            # 2026-10-06 (38978ddff): written around the month, before get_lots' window mode.
            "ece97ef1d8b67c8be3e515378dca3306818aeb8a6b439cdfb117a804a97416c8",
            # 2026-10-07 (a623956ab): "Worth a look", before the 2026-10-09 rewrite.
            "681cf9c68183db5c22b1ec23617bc7f8f7cb12abe4772d590de1bee0fbe726a1",
            # 2026-10-11: "Facts about the data" after the framing; now the Gotchas lead.
            "62cf2698658914932ef97443ddab33dd72fd5b4e01d5fd7695a981bef988563c",
        }
    ),
    # 2026-10-11: before the Gotchas section; the trade rules lived in the body (now
    # references/trades.md, read before logging a trade).
    "log-transactions-from-email": frozenset(
        {
            "23029f81e7d4ff685dcba55c25fdd84ae15cc3774e46e9beeef8a49af9aa8ba4",
            # 2026-10-11 (a607394d3): a fixed 1-day fetch window, sized for a 12-hour
            # schedule — a weekly one missed six days of mail. Now since the last completed run.
            "2fc64121dc4aa07ceb1b347830e04c0184116e90f88bc9b3c5e311b7f5479b53",
            # 2026-10-25: memo invited "anything worth seeing", so runs retold each email
            # (alert name, time, card, payee, fare) in memo. Now empty unless it adds something.
            "c5646e9cf177bf8db1c26e09c6e8ea185d31b5f1c9bf62e7363175c4ded8ee43",
            # 2026-10-27: memo banned the amount and fare breakdown, so a receipt's breakdown
            # never reached memo even when the user asked for it. Now the model decides.
            "d877696c3bac8ae098ca3f70043b79046ed02f766cd8168b9c240159b7494a35",
        }
    ),
    # 2026-10-11 (e98c3e2a5): the load step spelled as a find_tools(names=[...]) call.
    "can-i-afford-it": frozenset(
        {"ef68daa96434d875063bbc6b9676fa62a470e67d2ae56e7ae13da6189cadc163"}
    ),
    "on-track": frozenset(
        {
            "920d98d7edc9f6ec8ef9ae1d070073f8afbdd2b87ea211979ddb8b5b8a529884",
            # 2026-10-15: promised "a confirm card" on writes; whether one shows is the user's
            # permission setting, not the skill's. The skill now asks before writing.
            "d6d659e926fbdeabd7fc8fbe85b69aaa8e6f3e8cc917c0ea2a7fe15cab601eab",
        }
    ),
    # 2026-10-15 (0f5f06530): told the model trace_transactions "doesn't read balances", so a
    # real turn blamed the import for a row edited after it, without reading its history.
    "balance-mismatch-tracer": frozenset(
        {
            "e06909c03d23f542a49e56d397ac24af9a53c594aabf00ec6618b7ba2c7fb4e1",
            # 2026-10-15: promised "a confirm card" on writes; whether one shows is the user's
            # permission setting, not the skill's. The skill now asks before writing.
            "166bcbd6a67409bb0aa382cdf172f569f3cc5618bd0630250dcfab51a6e0cb80",
        }
    ),
    # 2026-10-05 (e98c3e2a5): the first catch-up body.
    "catch-up": frozenset(
        {
            "b63fecdca6dbca8e0b2a5497549ea6ffe82d8ddfddf0feb2214cc92d184490d7",
            # 2026-10-11: the load step spelled as a find_tools(names=[...]) call.
            "68d0f2cfcf794e26dce8cc129f40f2dcdbd239f515875191a24bcda15270f566",
            # 2026-10-15: promised "a confirm card" on writes; whether one shows is the user's
            # permission setting, not the skill's. The skill now asks before writing.
            "1053f6b4f3c5834cc3c6dc6f8e9874529d0423e5e0d48281feab17f844ec5fde",
        }
    ),
    # 2026-10-01 (aa2e23dd1): the body as the starters moved to folders.
    "skill-creator": frozenset(
        {"99d24932783240d2ede20ba09f34f117cbb1abb01f0c91d935ec3d557df00250"}
    ),
    # 2026-10-09 (75a0c8e09): the first daily-briefing body.
    "daily-briefing": frozenset(
        {
            "3f3def0712e78b029e6c7b81cbecb2ff76bb366f2cca010fd3fb40e88cd84518",
            # 2026-10-11: before the Gotchas section led the body.
            "528520ce70242fc9b4a5bc5fac3c52869c468a187652911f14981052aad8f372",
        }
    ),
}


# ── Superseded starter descriptions ────────────────────────────────────────────
# Same contract as the bodies above, for a starter whose DESCRIPTION changed while its body did
# not (a body refresh already carries the current description). Add the OLD description's hash.
_SUPERSEDED_STARTER_DESCRIPTIONS: dict[str, frozenset[str]] = {
    # 2026-10-05: routing notes (Telegram delivery, "Use this skill to…") read as the skill's
    # subtitle in the Skills tab; now what it is and when to use it.
    "monthly-report": frozenset(
        {
            "8e991070ad999579934b04eac1cbf2ff0528d9c407b70d9aa94c9378d28b0ece",
            "1e3f56402acf5b2e6e1785adcf43d469ead726bf8ff8b927ca3132b7443a3e70",
        }
    ),
    "weekly-report": frozenset(
        {
            "19d0957793284ac46b7df5839231b9fde661886f7c74a6c319a1fb7be7d2ca49",
            "19bcca476864af322bca0fe5f495bd7cce10927b59724a9319bc65811924dfbd",
        }
    ),
}


# ── Retired starters ───────────────────────────────────────────────────────────
# sha256 of EVERY body a starter that no longer ships ever shipped. An installed copy still
# carrying one is unedited and is deleted at boot (``retire_dropped_starters``); without this a
# dropped starter sits on every old install forever beside the one that replaced it.
_RETIRED_STARTER_BODIES: dict[str, frozenset[str]] = {
    # 2026-10-05: a thin draft of monthly-report plus housekeeping; replaced by catch-up.
    "monthly-close": frozenset(
        {
            "3371bee4a48845633f7039f54c6f3bed29e720796c9fcf0e3f6d0bfbf4e30a04",
            "71f2a03ac31cc3cf24d119c1d28d6e869ceb74aab530c456ce7fd916132a9a13",
        }
    ),
    # 2026-10-05: one step of investment-checkup, which now ranks fee drag itself.
    "fee-analyzer": frozenset({"130ff397b5004452a4e7defb04cbb655814375de17e1c4a55c5fab29f9632e64"}),
    # 2026-10-05: the weekly/monthly reports' unusual-spending sections cover it.
    "spending-anomaly": frozenset(
        {
            "d53394e2e689cd4feb082d30d285db624995e025991e604d181f07916135489b",
            "41b9622b8227415b97ae32464076cbd6e0d6b22212676c026d45a4898065df6f",
        }
    ),
    # 2026-10-05: divided cash by burn; can-i-afford-it answers runway from simulate_cashflow.
    "runway": frozenset(
        {
            "e56d365e2435c11eb2e47fdb6b4c6129c61e9c5adfa654017c4728d77ed1f439",
            "df6d0cebd7b12375b74bf837e8aa1f6349927d047d08b26bf61ea2cc6a7eede4",
        }
    ),
}

# The current starter that does a retired one's job, installed when the retired copy goes.
_RETIRED_STARTER_REPLACEMENTS: dict[str, str] = {
    "monthly-close": "catch-up",
    "runway": "can-i-afford-it",
}

# ── Starters added after installs already existed ─────────────────────────────
# Boot seeds only an EMPTY library, so a starter shipped later never reaches an existing
# install. List it here and ``install_added_starters`` installs it once per install
# (``StarterOfferStore`` remembers the offer, so a deleted copy stays deleted).
_ADDED_STARTERS: tuple[str, ...] = (
    # 2026-10-09: the Daily briefing template runs it.
    "daily-briefing",
    # 2026-10-09: the email-logging instructions moved out of the template into a skill.
    "log-transactions-from-email",
    # 2026-10-15: why an account's balance differs from the bank's, traced row by row.
    "balance-mismatch-tracer",
)

# ── Built-in starter skills ────────────────────────────────────────────────────
# Every ``backend/starter_skills/<id>/SKILL.md`` is a starter — a real Agent Skills folder, so
# a starter is added or edited as markdown, never as Python. The bodies name existing ordinary
# tools and contain NO money math / NO hardcoded currency — figures come from the tools.
STARTER_SKILLS_DIR = Path(__file__).resolve().parent.parent / "starter_skills"


def _folder_starters() -> list[dict]:
    starters = []
    for skill_md in sorted(STARTER_SKILLS_DIR.glob("*/SKILL.md")):
        meta, body = parse_skill_md(skill_md.read_text("utf-8"))
        sid = skill_md.parent.name
        starters.append(
            {
                "id": sid,
                "name": str(meta.get("name") or sid),
                "description": str(meta.get("description") or ""),
                "body": body.strip(),
                # Everything else in the folder (assets/, references/) ships with the skill.
                "files": {
                    path.relative_to(skill_md.parent).as_posix(): path.read_text("utf-8")
                    for path in sorted(skill_md.parent.rglob("*"))
                    if path.is_file() and path.name != "SKILL.md"
                },
            }
        )
    return starters


STARTER_SKILLS: list[dict] = _folder_starters()


def retired_starter_ids() -> frozenset[str]:
    """Ids of starters that no longer ship (see ``_RETIRED_STARTER_BODIES``)."""
    return frozenset(_RETIRED_STARTER_BODIES)
