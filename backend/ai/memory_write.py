"""The memory write choke: the ONLY place a fact is created, rewritten, retired, touched or removed.

Extraction, the dialog's edit and "Tell Lucius", ``remember_fact`` / ``forget_fact`` and the
passes that sort and tidy all hand it the same ``ops`` shape. It enforces what a prompt cannot:
a known section, one topic spelling per name, the 280-character limit, a valid expiry, no
full card / account / ID numbers or passwords whatever the source, sensitive topics only when
the user allows them, and — for the background pass — no ledger figures.

``strict=True`` is a user-facing write (refused whole with a sentence); ``strict=False`` is a
model-shaped one (the bad items are dropped and reported, the rest applies). ADR
``2026-10-25-memory-keeps-only-what-has-no-other-home``.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from backend.ai import memory_structure as ms
from backend.core import data_events
from backend.core.errors import ValidationError
from backend.core.ids import new_id
from backend.core.user_time import user_today
from backend.db import MemoryStore
from backend.db.memory_forgotten_store import MemoryForgottenStore
from backend.db.memory_settings_store import MemorySettingsStore
from backend.db.memory_store import is_expired

logger = logging.getLogger(__name__)

# The choke is synchronous (it serves sync routes and async tools alike), so it cannot hold an
# asyncio lock across a writer's model call; this serialises the read-modify-write between
# writers instead.
_WRITE_LOCK = threading.RLock()

_MESSAGES = {
    "empty": "A memory can't be empty — use Forget to remove it.",
    "bad_section": "Pick a section: " + ", ".join(ms.SECTIONS.values()) + ".",
    "bad_expiry": "Use a date like 2026-12-31 that hasn't passed.",
    "ledger_figure": "Balances, prices and share counts are read live, so they aren't remembered.",
    "identifier": (
        "Full card, account, ID or passport numbers and passwords are never remembered — "
        "the last 4 digits are fine."
    ),
    "sensitive_off": (
        "Sensitive topics aren't remembered. Turn on “Include sensitive topics in memory” "
        "in Settings to allow it."
    ),
    "unknown_id": "That memory no longer exists.",
    "forgotten": (
        "The user previously asked you to forget this, so it was not saved. Ask them whether "
        "to remember it again; only if they say so, call remember_fact with user_asked=true."
    ),
}


# ``_Plan._unfit_to_file``'s answer when a fact must stay unsorted (its reason is in ``rejected``).
_REFUSED = "refused"


def _too_long(length: int) -> str:
    return f"Keep a memory under {ms.FACT_MAX_CHARS} characters (this one is {length})."


# ── content guards ──────────────────────────────────────────────────────────────

_ISO_DATE = re.compile(r"(?<!\d-)(?<!\d)\d{4}-\d{2}-\d{2}(?!-?\d)")
_DIGIT_RUN = re.compile(r"(?<!\d)\d(?:[ -]?\d){11,}(?!\d)")
_ID_KEYWORDS = (
    r"(?:passport|nric|ssn|social security|national id|id card|identity card|tax id"
    r"|driver'?s? licen[cs]e|id(?=\s*(?:no\b|no\.|number|num\b|#)))"
)
_ID_SHAPES = (
    re.compile(r"\b[STFGM]\d{7}[A-Z]\b", re.I),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    re.compile(
        rf"\b{_ID_KEYWORDS}\b(?:\s*(?:no\.?|number|num|#))?(?:\s*(?:is|was)\b)?[\s:#=-]{{0,4}}"
        r"(?:(?=[A-Z0-9-]{6,}\b)(?=[A-Z-]*\d)[A-Z0-9-]{6,}|\d{3}[ -]\d{2}[ -]\d{4}\b)",
        re.I,
    ),
)
_SECRET = re.compile(r"\b(?:password|passcode|pin)\b\s*(?:is|=|:)\s*(\S+)", re.I)


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def _long_number(text: str) -> bool:
    """A full account number (12+ unbroken digits) or a card number (13-19 digits, Luhn-valid).

    Spaced or dashed runs are what phone numbers, repeated amounts and date ranges look like,
    so they are refused only when they pass the Luhn check (or run past 19 digits)."""
    for run in _DIGIT_RUN.finditer(_ISO_DATE.sub(" ", text)):
        digits = re.sub(r"\D", "", run.group())
        if len(digits) > 19:
            return True
        if len(digits) == len(run.group()):
            return True
        if len(digits) >= 13 and _luhn(digits):
            return True
    return False


def _secret_given(text: str) -> bool:
    """ "password is hunter22" — the word must be followed by a value that looks like one
    (a digit or a symbol), not by ordinary prose ("Pin is optional")."""
    return any(re.search(r"[\d\W_]", m.group(1).strip(".,;")) for m in _SECRET.finditer(text))


_MONEY_FIGURE = r"(?:[$€£¥]\s*\d[\d,]*(?:\.\d+)?|\d[\d,]*\.\d+|\d{1,3}(?:,\d{3})+)"
_PERISHABLE = (
    re.compile(r"\b\d[\d,]*(?:\.\d+)?\s*(?:shares?|units?|coins?|contracts?)\b", re.I),
    re.compile(rf"(?:trading at|priced? at|price of|@|at)\s*[~≈]?\s*{_MONEY_FIGURE}", re.I),
    re.compile(rf"\b(?:balance|worth)\b[^.\n]{{0,20}}{_MONEY_FIGURE}", re.I),
)


def has_identifier(text: str) -> bool:
    """A full card / account number (12+ digits) or an ID, passport or password shape."""
    return _long_number(text) or _secret_given(text) or any(p.search(text) for p in _ID_SHAPES)


def is_perishable_figure(text: str) -> bool:
    """True if the text pins a live-readable figure (share count / price / balance)."""
    return any(p.search(text or "") for p in _PERISHABLE)


def _truthy(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")
    return value is True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _expiry(section: str, raw, today: date) -> tuple[str | None, bool]:
    """(expires_at, valid). Open items default to 14 days and are capped at 30."""
    if raw is None or str(raw).strip().lower() in ("", "null", "none"):
        if section == ms.OPEN_ITEMS:
            return (today + timedelta(days=ms.OPEN_ITEM_DEFAULT_DAYS)).isoformat(), True
        return None, True
    try:
        due = date.fromisoformat(str(raw).strip())
    except ValueError:
        return None, False
    if due < today:
        return None, False
    if section == ms.OPEN_ITEMS:
        due = min(due, today + timedelta(days=ms.OPEN_ITEM_MAX_DAYS))
    return due.isoformat(), True


class _Plan:
    """One call's working copy: validates every op against the facts as they stand, then
    ``commit`` writes what survived."""

    def __init__(
        self,
        facts: list[dict],
        source: str,
        today: str,
        sensitive_ok: bool,
        forgotten: set[str] = frozenset(),
        guard_forgotten: bool = False,
    ):
        self.source = source
        self.forgotten = forgotten
        # An explicit save the user did not ask for in so many words (Lucius's own
        # remember_fact) is checked against the fingerprints like a background one (R31).
        self.guard_forgotten = guard_forgotten
        # Fingerprints a user forget records, and those an explicit save of the text clears.
        self.forget: set[str] = set()
        self.unforget: set[str] = set()
        self.today = date.fromisoformat(today)
        self.sensitive_ok = sensitive_ok
        self.now = _now()
        self.by_id = {f["id"]: f for f in facts if isinstance(f.get("id"), str)}
        self.patches: dict[str, dict] = {}
        self.inserts: list[dict] = []
        self.deleted: list[str] = []
        self.rejected: list[dict] = []
        self.messages: list[str] = []
        self.counts = {"added": 0, "updated": 0, "removed": 0, "retired": 0}
        self.touched: set[str] = set()
        self._topics: dict[tuple[str, str], str] = {}
        for f in sorted(facts, key=lambda f: (f.get("created_at", ""), f.get("id", ""))):
            if f.get("state") == "live" and f.get("section") and f.get("topic"):
                self._topics.setdefault((f["section"], ms.topic_key(f["topic"])), f["topic"])

    # ── helpers ──────────────────────────────────────────────────────────────────

    def view(self, fid) -> dict | None:
        if not isinstance(fid, str) or fid not in self.by_id or fid in self.deleted:
            return None
        return {**self.by_id[fid], **self.patches.get(fid, {})}

    def reject(self, reason: str, content: str, section, topic, message: str | None = None):
        self.rejected.append(
            {"content": content, "section": section, "topic": topic, "reason": reason}
        )
        self.messages.append(message or _MESSAGES[reason])
        logger.info(
            "memory write rejected: reason=%s source=%s section=%s", reason, self.source, section
        )

    def canonical_topic(self, section: str, raw) -> str | None:
        if section == ms.OPEN_ITEMS:
            return None
        name = ms.current_topic(section, ms.normalise_topic(raw))
        if not name:
            return None
        return self._topics.setdefault((section, ms.topic_key(name)), name)

    def vet_text(self, raw) -> tuple[str, str | None, str | None]:
        """(cleaned text, reason, message) — the checks every text-bearing op shares."""
        text = " ".join(raw.split()) if isinstance(raw, str) else ""
        if not text:
            return text, "empty", None
        if len(text) > ms.FACT_MAX_CHARS:
            return text, "too_long", _too_long(len(text))
        if has_identifier(text):
            return text, "identifier", None
        if self.source == "implicit" and is_perishable_figure(text):
            return text, "ledger_figure", None
        return text, None, None

    def saves_forgotten(self, text: str) -> bool:
        """A save of text the user told Lucius to forget that the user did not ask for. The
        user's own save of it is allowed, and takes it off the forgotten list."""
        if self.source == "explicit" and not self.guard_forgotten:
            self.unforget.add(ms.fingerprint(text))
            return False
        return ms.fingerprint(text) in self.forgotten

    def note_forgotten(self, fact: dict) -> None:
        """A user forget records the fact's fingerprint — except for text carrying a full card,
        account or ID number, which is never saved anyway and should not leave a hash."""
        content = fact.get("content")
        if not has_identifier(str(content or "")):
            self.forget.add(ms.fingerprint(content))

    # ── ops ──────────────────────────────────────────────────────────────────────

    def add(self, raw) -> None:
        if not isinstance(raw, dict):
            return
        section = ms.normalise_section(raw.get("section"))
        topic = ms.normalise_topic(raw.get("topic")) or None
        text, reason, message = self.vet_text(raw.get("content"))
        label = section or (raw.get("section") if isinstance(raw.get("section"), str) else None)
        if reason is None and section is None:
            reason = "bad_section"
        sensitive = _truthy(raw.get("sensitive"))
        if reason is None and sensitive and not self.sensitive_ok:
            reason = "sensitive_off"
        expires, valid = _expiry(section or "", raw.get("expires_at"), self.today)
        if reason is None and not valid:
            reason = "bad_expiry"
        if reason is None and self.saves_forgotten(text):
            reason = "forgotten"
        if reason:
            return self.reject(reason, text, label, topic, message)
        row = {
            "id": new_id("mem"),
            "content": text,
            "section": section,
            "topic": self.canonical_topic(section, topic),
            "source": self.source,
            "state": "live",
            "expires_at": expires,
            "created_at": self.now,
            "updated_at": self.now,
            "mentioned_at": self.now,
            "last_used_at": None,
        }
        self.inserts.append(row)
        self.counts["added"] += 1

    def update(self, raw) -> None:
        if not isinstance(raw, dict):
            return
        fid = raw.get("id")
        current = self.view(fid)
        if current is None:
            return self.reject("unknown_id", "", None, None)
        if raw.get("section") is not None and current.get("state") not in ("live", "retired"):
            unfit = self._unfit_to_file(raw, current)
            if unfit == _REFUSED:
                return None
            if unfit:
                return self.retire({"id": fid, "reason": unfit})
        changes = self._changes(raw, current)
        if changes is not None and len(changes) > 1:
            self.patches.setdefault(fid, {}).update(changes)
            self.counts["updated"] += 1

    def _unfit_to_file(self, raw: dict, current: dict) -> str | None:
        """Filing a fact that is not yet live runs the guards a new fact meets, on the text it
        will be filed with. Returns the reason to retire it instead (``identifier`` for a full
        card, account or ID number; ``ledger`` for a figure the ledger reads live), ``_REFUSED``
        when it must stay unsorted (too long, with no shorter wording given), or None when it
        may be filed. A sensitive topic does not stop it: the fact was already saved, and the
        sensitive-topics setting only governs saving new ones."""
        given = raw.get("content")
        text, reason, message = self.vet_text(
            given if given is not None else current.get("content")
        )
        if reason == "identifier":
            return "identifier"
        if reason == "ledger_figure":
            # The user's own words, and a goal filed under Plans ("a net worth of $1M by 40"),
            # are not balances — only an extracted figure goes back to the ledger unasked.
            if (
                current.get("source") == "explicit"
                or ms.normalise_section(raw["section"]) == "plans"
            ):
                reason = None
            else:
                return "ledger"
        if reason:
            self.reject(reason, text, raw.get("section"), current.get("topic"), message)
            return _REFUSED
        return None

    def _changes(self, raw: dict, current: dict) -> dict | None:
        changes: dict = {"updated_at": self.now}
        section = current.get("section")
        if raw.get("content") is not None:
            text, reason, message = self.vet_text(raw["content"])
            if reason:
                self.reject(reason, text, section, current.get("topic"), message)
                return None
            changes["content"] = text
            changes["mentioned_at"] = self.now
        filing = current.get("state") not in ("live", "retired")
        if raw.get("section") is not None:
            section = ms.normalise_section(raw["section"])
            if section is None:
                self.reject("bad_section", "", str(raw["section"]), current.get("topic"))
                return None
            changes["section"] = section
            if filing:
                changes["state"] = "live"
                changes["sorted_at"] = self.now
        # New sensitive wording is a new save; filing an already-saved fact is not.
        if _truthy(raw.get("sensitive")) and not filing and not self.sensitive_ok:
            self.reject("sensitive_off", "", section, current.get("topic"))
            return None
        if not self._shape_update(raw, current, section, changes):
            return None
        if "content" in changes and not filing and self.saves_forgotten(changes["content"]):
            self.reject("forgotten", changes["content"], section, current.get("topic"))
            return None
        if len(changes) > 1 and self.source == "explicit":
            changes["source"] = "explicit"
            changes["edited_at"] = self.now
        return changes

    def _shape_update(self, raw: dict, current: dict, section, changes: dict) -> bool:
        """Topic and expiry follow the section: open items carry no topic and always expire. A
        null topic or expiry means "unchanged" (the reply shapes show `...|null`); an empty
        string clears one."""
        if section is None:
            return True
        given_topic = raw.get("topic") is not None
        given_expiry = raw.get("expires_at") is not None
        if given_topic or "section" in changes:
            changes["topic"] = self.canonical_topic(
                section, raw["topic"] if given_topic else current.get("topic")
            )
        if given_expiry or "section" in changes:
            expires, valid = _expiry(
                section,
                raw["expires_at"] if given_expiry else self._carried_expiry(current, section),
                self.today,
            )
            if not valid and not given_expiry:
                expires, valid = _expiry(section, None, self.today)
            if not valid:
                self.reject("bad_expiry", "", section, current.get("topic"))
                return False
            changes["expires_at"] = expires
        elif section == ms.OPEN_ITEMS and not current.get("expires_at"):
            changes["expires_at"], _ = _expiry(section, None, self.today)
        return True

    @staticmethod
    def _carried_expiry(current: dict, section: str):
        """The expiry an update keeps when it names none. The 14-day clock belongs to Open
        items: a fact filed out of them stops expiring, one filed into them from another
        section starts the clock. A legacy fact with no section keeps its date into Open items,
        and into any other section only if it was ``time_bound`` — a ``durable`` legacy fact's
        stray date never meant an expiry."""
        was = current.get("section")
        if was == ms.OPEN_ITEMS and section != ms.OPEN_ITEMS:
            return None
        if section == ms.OPEN_ITEMS and was not in (None, ms.OPEN_ITEMS):
            return None
        if was is None and section != ms.OPEN_ITEMS:
            if current.get("tier", "time_bound") != "time_bound":
                return None
        return current.get("expires_at")

    def remove(self, fid) -> None:
        """A user's forget deletes and leaves the fact's fingerprint. A background pass only
        deletes what is already retired or expired; any other fact it removes is retired."""
        if not isinstance(fid, str):
            return
        current = self.view(fid)
        if current is None:
            return self.reject("unknown_id", "", None, None)
        if self.source == "implicit":
            if current.get("state") != "retired" and not is_expired(current, str(self.today)):
                return self.retire({"id": fid, "reason": "stale"})
        else:
            self.note_forgotten(current)
        self.deleted.append(fid)
        self.counts["removed"] += 1

    def retire(self, raw) -> None:
        if not isinstance(raw, dict):
            return
        current = self.view(raw.get("id"))
        if current is None:
            return self.reject("unknown_id", "", None, None)
        if current.get("state") == "retired":
            return
        reason = raw.get("reason")
        self.patches.setdefault(current["id"], {}).update(
            state="retired",
            retired_reason=reason if reason in ms.RETIRE_REASONS else "stale",
            retired_at=self.now,
        )
        self.counts["retired"] += 1

    def delete_topic(self, raw) -> None:
        if not isinstance(raw, dict):
            return
        section = ms.normalise_section(raw.get("section"))
        key = ms.topic_key(raw.get("topic"))
        if section is None or section == ms.OPEN_ITEMS:
            return self.reject("bad_section", "", raw.get("section"), raw.get("topic"))
        if not key:
            return self.reject("empty", "", section, None)
        for fid in list(self.by_id):
            fact = self.view(fid)
            if (
                fact
                and fact.get("section") == section
                and ms.topic_key(fact.get("topic")) == key
                and fact.get("state") != "retired"
            ):
                if self.source == "explicit":
                    self.note_forgotten(fact)
                self.deleted.append(fid)
                self.counts["removed"] += 1

    def touch(self, raw) -> None:
        if not isinstance(raw, dict):
            return
        for key, field in (("mentioned", "mentioned_at"), ("used", "last_used_at")):
            for fid in raw.get(key) or []:
                if not isinstance(fid, str):
                    continue
                if self.view(fid) is None:
                    self.reject("unknown_id", "", None, None)
                    continue
                self.patches.setdefault(fid, {})[field] = self.now
                self.touched.add(fid)

    def run(self, ops: dict) -> None:
        for raw in ops.get("updates") or []:
            self.update(raw)
        for raw in ops.get("retire") or []:
            self.retire(raw)
        for fid in ops.get("remove") or []:
            self.remove(fid)
        for raw in ops.get("delete_topic") or []:
            self.delete_topic(raw)
        for raw in ops.get("new") or []:
            self.add(raw)
        self.touch(ops.get("touch"))

    def commit(self, store: MemoryStore) -> None:
        live = {fid: p for fid, p in self.patches.items() if fid not in self.deleted}
        if live:
            store.bulk_patch(live)
        for fid in self.deleted:
            store.remove_by_id(fid)
        store.bulk_insert(self.inserts)
        forgotten = MemoryForgottenStore()
        forgotten.clear(self.unforget)
        forgotten.record(self.forget - self.unforget)

    def result(self) -> dict:
        return {
            **self.counts,
            "touched": len(self.touched),
            "rejected": self.rejected,
            "created": self.inserts,
        }


def apply_memory_change(
    ops: dict,
    *,
    source: Literal["implicit", "explicit"],
    strict: bool,
    today: str | None = None,
    guard_forgotten: bool = False,
) -> dict:
    """Apply one ops dict — ``new``, ``updates``, ``remove``, ``retire``, ``delete_topic``,
    ``touch`` — and return the counts, ``rejected`` and ``created`` (the new rows).

    ``strict`` raises ``ValidationError`` on the first refusal, before anything is written;
    otherwise the refused items are reported in ``rejected`` and the rest applies.
    ``guard_forgotten`` makes an explicit save refuse a forgotten fact like an implicit one
    does — for a save Lucius chose to make, not one the user asked for.
    """
    ops = ops if isinstance(ops, dict) else {}
    with _WRITE_LOCK:
        store = MemoryStore()
        plan = _Plan(
            store.all_facts(),
            source,
            today or user_today().isoformat(),
            MemorySettingsStore().sensitive_topics(),
            MemoryForgottenStore().all_hashes()
            if source == "implicit" or guard_forgotten
            else frozenset(),
            guard_forgotten,
        )
        plan.run(ops)
        if strict and plan.rejected:
            raise ValidationError(plan.messages[0], reason=plan.rejected[0]["reason"])
        plan.commit(store)
    if source == "implicit" and any(plan.counts.values()):
        _announce()
    return plan.result()


def _announce() -> None:
    """Tell every open screen that memory changed. A background pass (extraction, the sort,
    the tidy) has no tool call to broadcast for it, so an open memory list would otherwise keep
    showing what was there before; a touch alone changes nothing the list shows. ADR
    ``2026-09-29-every-write-refreshes-every-open-screen``."""
    try:
        data_events.publish(("memory",))
    except Exception:  # noqa: BLE001 — a broadcast fault never fails the write
        logger.debug("memory change broadcast failed", exc_info=True)
