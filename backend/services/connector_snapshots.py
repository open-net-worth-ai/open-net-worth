"""Connector (bank/broker) provenance snapshots — RFC connectors-as-data-sources §C.

Persists the RAW row a provider fetch returned, keyed by ``source:ref``, so a booked
transaction can be cross-checked against its source (mirrors ``services/email_snapshots.py``,
which this module is a deliberate SIBLING of, not a shared store — email keeps its own
legacy-key/first-seen-wins quirks; owner decision, RFC "Alternatives considered").

The one load-bearing difference from email: this store is NOT first-seen-wins. A fetch
whose row content_hash disagrees with what is already stored is a real signal — the bank
corrected or re-described a transaction after the fact — and ``stash_rows`` reports it as a
``modified`` change instead of silently keeping the first-seen copy.

Every ``modified``/``vanished`` change also carries ``current`` (the booked ledger row's live
``{date, amount, currency, description}``, read fresh via ``services.ledger.
booked_row_snapshot`` — never the stashed snapshot) and ``user_edited`` (true when ``current``
has moved away from the snapshot's own ``before``/last-known values — ADR 2026-09-07, CDS-6),
so the sync automation can apply a provider correction without ever overriding an edit the
user made by hand.

Four write paths, all called from ``ai/tools/<provider>_fetch_transactions`` (L2):
  - ``stash_rows`` — every fetched row, one call per fetch. Diffs against what is already
    stored and returns the changes a caller should surface to the model.
  - ``note_missing`` — called once per COMPLETE window fetch (``window.complete`` — never a
    limit-truncated one) to age out REFERENCED rows the window should have re-seen but didn't;
    reports a `vanished` change once a row has been missed twice in a row.
  - ``promote`` — called from the ledger write choke (``services.ledger._promote_source_
    snapshots``) right after a row books successfully, mirroring
    ``email_snapshots.promote_for_email``.
  - ``prune_unreferenced`` — bounds UNREFERENCED (never-booked) rows per source; called
    automatically at the end of ``stash_rows``, exactly like ``email_snapshots.stash_snapshots``
    calls its own prune.

``emitter_for``/``snapshots_for_transaction`` are the read side: the former feeds the dedup
identity veto (``services.ledger._stamp_ref_emitters``), the latter feeds the transaction
drawer (``api/ledger.py``'s source-snapshots endpoint).

Row shape (stored verbatim, see ``db/connector_snapshot_store.py``):
``{key, source, ref, provider_account_id, raw, normalized, content_hash, first_seen_at,
last_seen_at, changed_at, missed_fetches, last_missed_at, referenced}`` where ``normalized``
is ``{date, amount, currency, description, account}`` — the fields ``content_hash`` covers.

Input row shape (``stash_rows``' ``rows`` argument, one per fetched item): ``{ref,
provider_account_id, raw, normalized}`` — the provider-agnostic shape every
``<provider>_fetch_transactions`` tool builds before stashing (RFC §A step 3/5). A row with a
falsy ``ref`` is skipped (defensive — the fetch tool is expected to always derive one).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from backend.db.connector_snapshot_store import ConnectorSnapshotStore, snapshot_key

logger = logging.getLogger(__name__)

_snapshots = ConnectorSnapshotStore()

# Fields the content hash covers — the diff surface a provider correction is judged against.
_HASH_FIELDS = ("date", "amount", "currency", "description", "account")

# Bound on UNREFERENCED (never-booked) snapshots kept PER SOURCE at stash time — mirrors
# email_snapshots' _UNREFERENCED_KEEP, scaled up: a bank/broker window is fetched far more
# often than email is searched, so the default lookback alone can return thousands of rows
# per run.
_UNREFERENCED_KEEP = 2000

# A row is only judged "should have been re-seen" (note_missing) when its normalized date
# falls at least this many days inside the fetch window's edges — a row dated right at
# `since`/`before` may be genuinely outside what the PROVIDER'S OWN pagination returned for
# boundary reasons, not because it vanished.
_EDGE_MARGIN_DAYS = 2

# missed_fetches at which an absence becomes a reported `vanished` change — two consecutive
# complete misses, never one (a single missed run is well within normal provider jitter).
_VANISH_THRESHOLD = 2

# Amount tolerance for the `user_edited` annotation (ADR 2026-09-07, CDS-6) — a hair below
# cent-rounding noise, matching every other "did the money actually move" tolerance in the
# ledger (e.g. services.ledger._MERGE_AMOUNT_UNCHANGED_TOL).
_USER_EDITED_AMOUNT_TOL = 0.005


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _content_hash(normalized: dict) -> str:
    subset = {k: (normalized or {}).get(k) for k in _HASH_FIELDS}
    return hashlib.sha256(json.dumps(subset, sort_keys=True, default=str).encode()).hexdigest()


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _lazy_known_refs(source: str) -> Callable[[], dict]:
    """A one-shot memoized ``ledger.known_external_refs(source)`` accessor — most stashed or
    aged-out rows never need the booked txn id, so this avoids paying for the ledger scan on
    every row when only some (referenced + changed, or vanished) actually ask for it."""
    cache: dict[str, dict] = {}

    def get() -> dict:
        if "v" not in cache:
            from backend.services.ledger import known_external_refs

            cache["v"] = known_external_refs(source)
        return cache["v"]

    return get


def _new_snapshot_row(
    source: str, key: str, ref: str, row: dict, content_hash: str, now: str
) -> dict:
    return {
        "key": key,
        "source": source,
        "ref": ref,
        "provider_account_id": row.get("provider_account_id"),
        "raw": row.get("raw"),
        "normalized": row.get("normalized") or {},
        "content_hash": content_hash,
        "first_seen_at": now,
        "last_seen_at": now,
        "changed_at": now,
        "missed_fetches": 0,
        "last_missed_at": None,
        "referenced": False,
    }


def _stripped(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


def _differs(current: dict, before: dict) -> bool:
    """Whether ``current`` (the booked ledger row's live values) has moved away from
    ``before`` (the snapshot's last-known values) beyond the tolerances a real edit would
    exceed — the ``user_edited`` annotation's own comparison (ADR 2026-09-07, CDS-6)."""
    cur_amt, bef_amt = current.get("amount"), before.get("amount")
    if cur_amt is None or bef_amt is None:
        if cur_amt != bef_amt:
            return True
    else:
        try:
            if abs(float(cur_amt) - float(bef_amt)) > _USER_EDITED_AMOUNT_TOL:
                return True
        except (TypeError, ValueError):
            if cur_amt != bef_amt:
                return True
    return any(
        _stripped(current.get(k)) != _stripped(before.get(k))
        for k in ("date", "currency", "description")
    )


def _lazy_linked_account_ids(source: str) -> Callable[[], dict[str, str]]:
    """One-shot memoized ``{provider_account_id: linked ledger account id}`` — mirrors
    ``_lazy_known_refs``: most changes never need it (only a two-leg TRANSFER row does, to
    pick the leg matching the fetched account — security review R2)."""
    cache: dict[str, dict] = {}

    def get() -> dict:
        if "v" not in cache:
            from backend.services import accounts as accounts_service

            cache["v"] = {
                a["provider_account_id"]: a["linked_account_id"]
                for a in accounts_service.linked_accounts_for_source(source)
                if a.get("provider_account_id")
            }
        return cache["v"]

    return get


def _change_annotation(
    source: str, ref: str, existing_row: dict, get_known, get_linked_accounts
) -> dict:
    """``{booked_txn_id, current, user_edited}`` for one ``modified``/``vanished`` change
    (ADR 2026-09-07, CDS-6) — ``current`` is read FRESH off the booked ledger row (never the
    stashed snapshot), so it reflects any edit the user made since booking; ``user_edited`` is
    true only when that live row has actually moved away from ``before`` (the snapshot's own
    last-known values), never merely because the PROVIDER re-described the row.

    Security review: best-effort (a bad booked amount/leg must not fail the whole fetch after
    rows were already stashed — L5), and ``current.description`` is sanitized (the one
    unsanitized provider-controlled string in a ``changes[]`` entry — L5). R2: resolves the
    fetched row's OWN linked account and passes it to ``booked_row_snapshot`` as
    ``prefer_account_id``, so a ref attached to the DESTINATION leg of a transfer compares
    against that leg, never unconditionally the origin leg."""
    try:
        from backend.services.ledger import booked_row_snapshot

        booked_txn_id = get_known().get((source, ref))
        provider_account_id = existing_row.get("provider_account_id")
        prefer_account_id = (
            get_linked_accounts().get(provider_account_id) if provider_account_id else None
        )
        current = booked_row_snapshot(booked_txn_id, prefer_account_id=prefer_account_id)
        if current and current.get("description"):
            from backend.core.untrusted import sanitize_untrusted

            current = {
                **current,
                "description": sanitize_untrusted(str(current["description"]), max_len=500),
            }
        before = existing_row.get("normalized")
        user_edited = bool(current and before and _differs(current, before))
        return {"booked_txn_id": booked_txn_id, "current": current, "user_edited": user_edited}
    except Exception:
        logger.warning(
            "connector_snapshots: change annotation failed for %s:%s", source, ref, exc_info=True
        )
        return {"booked_txn_id": None, "current": None, "user_edited": False}


def _prepare_stash(
    source: str,
    rows: list[dict],
    existing_by_ref: dict[str, dict],
    now: str,
    get_known,
    get_linked_accounts,
) -> tuple[list[dict], dict[str, dict], list[dict]]:
    """Classify every fetched row into a to-INSERT list / a key -> patch dict for
    ``bulk_patch`` / the ``modified`` changes to report — no DB write here (S-F6b: the
    caller commits both bulk calls in ONE transaction). A bad row is logged and skipped,
    never raised; a duplicate ref within the SAME batch keeps only its first occurrence,
    matching the old per-row loop's implicit last-write-wins-via-two-separate-calls
    behavior closely enough that a provider returning a real duplicate never double-writes."""
    to_insert: list[dict] = []
    to_patch: dict[str, dict] = {}
    changes: list[dict] = []
    seen_refs: set[str] = set()
    for row in rows:
        try:
            ref = str(row.get("ref") or "").strip()
            if not ref or ref in seen_refs:
                continue
            seen_refs.add(ref)
            normalized = row.get("normalized") or {}
            content_hash = _content_hash(normalized)
            key = snapshot_key(source, ref)
            existing = existing_by_ref.get(ref)
            if existing is None:
                to_insert.append(_new_snapshot_row(source, key, ref, row, content_hash, now))
                continue
            patch = {"last_seen_at": now, "missed_fetches": 0}
            if existing.get("content_hash") != content_hash:
                patch.update(
                    {
                        "raw": row.get("raw"),
                        "normalized": normalized,
                        "content_hash": content_hash,
                        "changed_at": now,
                    }
                )
                if existing.get("referenced"):
                    changes.append(
                        {
                            "kind": "modified",
                            "ref": ref,
                            "before": existing.get("normalized"),
                            "after": normalized,
                            **_change_annotation(
                                source, ref, existing, get_known, get_linked_accounts
                            ),
                        }
                    )
            to_patch[key] = patch
        except Exception:
            logger.warning("connector_snapshots: failed to prepare row for stash", exc_info=True)
    return to_insert, to_patch, changes


def stash_rows(source: str, rows: list[dict]) -> list[dict]:
    """Stash every fetched row for ``source`` in ONE transaction (S-F6b — was one INSERT/
    UPDATE, hence one checkpoint hook, per row), diffing against what is already stored,
    then prune unreferenced rows down to ``_UNREFERENCED_KEEP``. Returns the ordered list
    of ``modified`` changes a caller should surface to the model."""
    if not rows:
        return []
    now = _now()
    get_known = _lazy_known_refs(source)
    get_linked_accounts = _lazy_linked_account_ids(source)
    existing_by_ref = {r["ref"]: r for r in _snapshots.by_source(source)}
    to_insert, to_patch, changes = _prepare_stash(
        source, rows, existing_by_ref, now, get_known, get_linked_accounts
    )

    from backend.db.engine import locked_unit_of_work
    from backend.db.tables import connector_snapshots as connector_snapshots_table

    with locked_unit_of_work(connector_snapshots_table) as conn:
        _snapshots.bulk_insert(to_insert, conn=conn)
        _snapshots.bulk_patch(to_patch, conn=conn)
    prune_unreferenced(source, keep=_UNREFERENCED_KEEP)
    return changes


def _missing_candidate(
    existing: dict, lo: date, hi: date, seen_keys: set[str], account: str | None
) -> bool:
    """Whether a stashed row is IN SCOPE for ``note_missing`` — referenced, absent from this
    fetch, dated inside the window's inner margin, and (when given) matching ``account``."""
    if not existing.get("referenced") or existing.get("ref") in seen_keys:
        return False
    normalized = existing.get("normalized") or {}
    row_date = _parse_date(normalized.get("date"))
    if row_date is None or not (lo <= row_date <= hi):
        return False
    return account is None or normalized.get("account") == account


def note_missing(
    source: str,
    since: str,
    before: str,
    seen_keys: set[str],
    account: str | None = None,
) -> list[dict]:
    """Age out REFERENCED rows a COMPLETE window fetch should have re-seen but didn't — see
    ``_missing_candidate`` for the exact scope. Each in-scope row absent from ``seen_keys``
    gets ``missed_fetches += 1``; at exactly ``_VANISH_THRESHOLD`` (2) it yields a
    ``{"kind": "vanished", "ref", "booked_txn_id", "last_seen", "missed_fetches", "current",
    "user_edited"}`` change —
    reported once, at the crossing, not on every later miss. Never call this against a
    limit-truncated fetch — it would manufacture false vanish reports for rows the truncation,
    not the provider, left out."""
    since_dt, before_dt = _parse_date(since), _parse_date(before)
    if since_dt is None or before_dt is None:
        return []
    lo = since_dt + timedelta(days=_EDGE_MARGIN_DAYS)
    hi = before_dt - timedelta(days=_EDGE_MARGIN_DAYS)
    if lo > hi:
        return []
    get_known = _lazy_known_refs(source)
    get_linked_accounts = _lazy_linked_account_ids(source)
    changes: list[dict] = []
    for existing in _snapshots.by_source(source):
        if not _missing_candidate(existing, lo, hi, seen_keys, account):
            continue
        missed = int(existing.get("missed_fetches") or 0) + 1
        _snapshots.update_by_id(
            existing["key"], {"missed_fetches": missed, "last_missed_at": _now()}
        )
        if missed == _VANISH_THRESHOLD:
            changes.append(
                {
                    "kind": "vanished",
                    "ref": existing.get("ref"),
                    "last_seen": existing.get("last_seen_at"),
                    "missed_fetches": missed,
                    **_change_annotation(
                        source, existing.get("ref"), existing, get_known, get_linked_accounts
                    ),
                }
            )
    return changes


def promote(source: str, ref: str) -> bool:
    """Flip a stashed snapshot to ``referenced=True``. Idempotent — a second promote of the
    same pair is a harmless no-op. Returns False when the pair has no stashed row (a fetch
    that never stashed it, or one since pruned)."""
    if not source or not ref:
        return False
    return _snapshots.update_by_id(snapshot_key(source, ref), {"referenced": True}) is not None


def prune_unreferenced(source: str, keep: int = _UNREFERENCED_KEEP) -> int:
    """Delete UNREFERENCED rows for ``source`` beyond the most recent ``keep`` (by
    ``last_seen_at``, newest first) — mirrors ``EmailSnapshotStore.prune_unreferenced``,
    scoped per source since a fetch tool only ever touches its own provider's rows.
    ``referenced=True`` rows are never touched. Returns the count removed."""
    rows = _snapshots.by_source(source)
    unreferenced = sorted(
        (r for r in rows if not r.get("referenced")),
        key=lambda r: r.get("last_seen_at") or "",
        reverse=True,
    )
    stale = unreferenced[keep:]
    for row in stale:
        _snapshots.delete(row["key"])
    return len(stale)


def emitter_for(source: str | None, ref: str | None) -> str | None:
    """The provider account id behind one booked ``{"source", "ref"}`` pair, or None when it
    cannot be established — the connector counterpart of
    ``email_snapshots.emitter_for``, called from the SAME ledger dispatch
    (``services.ledger._stamp_ref_emitters``) for every non-email pair.

    Best-effort by contract: a lookup failure degrades to "unresolved" (the veto's safe
    direction), never fails the write."""
    if not source or not ref:
        return None
    try:
        row = _snapshots.get(source, ref)
    except Exception:
        logger.warning("connector_snapshots.emitter_for: lookup failed for %r/%r", source, ref)
        return None
    if row is None:
        return None
    return (row.get("provider_account_id") or "").strip() or None


def snapshots_for_transaction(txn_id: str) -> list[dict] | None:
    """Every connector snapshot behind a booked transaction — one entry per non-email
    ``external_refs`` pair, or None when the transaction doesn't exist or carries no connector
    provenance at all (mirrors ``email_snapshots.snapshots_for_transaction``'s one-404-covers-
    both contract; ``api/ledger.py``'s endpoint unions this with the email list).

    Each entry is ``{"kind": "connector", "source", "ref", "provider", "provider_account_id",
    "fetched_at", "raw", "unavailable"}`` — a snapshot never stashed / since pruned still
    returns as an explicit ``unavailable: True`` marker rather than being silently dropped
    (same missing-evidence-stays-visible rule as the email drawer). ``source``/``ref`` (ADR
    2026-09-07) are the pair's own identity, verbatim — what the drawer's "Unlink source"
    action posts back to ``POST .../refs/detach``."""
    from backend.services.ledger import transactions as _txns

    txn = _txns.find_by_id(txn_id)
    if txn is None:
        return None
    pairs = _connector_pairs(txn)
    if not pairs:
        return None
    return [_snapshot_entry(source, ref) for source, ref in pairs]


def _connector_pairs(txn: dict) -> list[tuple[str, str]]:
    # Only a connector-fetch provider ever stashes a snapshot (``connector_fetch``). Every other
    # namespace — statement fingerprints, the iOS shortcuts' idempotency keys, vest ids, a ref
    # Lucius coined — is an identity with no evidence behind it, so listing it would only ever
    # say "Snapshot unavailable" (owner report 2026-10-19: every iOS-captured row did).
    from backend.services.aggregation.providers import PROVIDERS

    pairs: list[tuple[str, str]] = []
    for entry in txn.get("external_refs") or []:
        if not isinstance(entry, dict):
            continue
        source, ref = entry.get("source"), str(entry.get("ref") or "").strip()
        if not ref or source not in PROVIDERS:
            continue
        pair = (source, ref)
        if pair not in pairs:
            pairs.append(pair)
    return pairs


def _snapshot_entry(source: str, ref: str) -> dict:
    # `source`/`ref` (ADR 2026-09-07, CDS-5): explicit top-level identity fields — mirrors
    # `provider`+`ref`, but under the SAME names the email entry and the detach route body
    # use, so the drawer's "Unlink source" action posts {source, ref} unchanged regardless
    # of which kind of entry it came from.
    from backend.services.accounts import provider_label

    label = provider_label(source)
    if (row := _snapshots.get(source, ref)) is None:
        return {
            "kind": "connector",
            "source": source,
            "ref": ref,
            "provider": source,
            "provider_label": label,
            "provider_account_id": None,
            "fetched_at": None,
            "raw": None,
            "unavailable": True,
        }
    return {
        "kind": "connector",
        "source": source,
        "ref": ref,
        "provider": source,
        "provider_label": label,
        "provider_account_id": row.get("provider_account_id"),
        "fetched_at": row.get("last_seen_at"),
        "raw": row.get("raw"),
        "unavailable": False,
    }
