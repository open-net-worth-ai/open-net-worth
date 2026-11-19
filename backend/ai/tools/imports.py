"""Transaction file/URL/attachment import tool (CSV/OFX/QFX/QIF/MT940/CAMT/XLSX/PDF).

UI↔Lucius parity for the Settings → Data "Import transactions" panel: the SAME
``services.import_*`` core (one draft shape → ``ledger.bulk_create_transactions``, the one
write choke point — so spend-rules + categorization + base math apply) is reached here so
the user can ask Lucius to import a statement they ATTACHED in chat (``attachment_ref`` — a
file cached by ``/api/upload``, including binary XLSX/PDF/CAMT), pasted as text
(``content``), or a hosted-file / Google-Sheet URL (``source_url``). Exactly one source.

EVERY format rides the ONE decision pipeline (``statements.decisions.decision_preview`` →
``decision_commit``), the same engine the UI's review modal drives — so a chat import gets
the same statement record, import batch (hence rollback), match bands and per-row review the
UI does. The legacy per-format ``commit_import`` wrappers are no longer reachable from here.

Confirm-gated WRITE, TWO CALLS: ``commit=false`` (the default) previews and returns a
``statement_id`` (writes NOTHING); ``commit=true`` with that same ``statement_id`` books it
against the CACHED parse — with or without per-row ``decisions``. A commit never re-parses,
so the preview's per-row verdicts are applied rather than discarded and a PDF is never billed
to the model twice. A one-shot ``commit=true`` on raw bytes still parses exactly once (it
previews internally, then commits that cache). The tool is deliberately OFF the headless
ceiling (``core/headless_scope.py`` / ``core/agent_caps.py``) — no unattended import.

Idempotency is content-addressed (an ``external_refs`` pair with source ``csv``/``ofx``/``qif``),
so a re-import (same text or same URL) dedups to zero. Money/base figures are never fabricated —
the ledger recomputes the base from the native leg.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Literal, Optional

from pydantic import Field

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import (
    NestedInput,
    ToolInput,
    bare_object_schema,
    schema_of,
)

logger = logging.getLogger(__name__)


def _resolve_account(account_id: str) -> str:
    """Resolve an account REF — an id or a NAME — to its id, or raise ImportError_.

    Delegates to ``services.accounts.resolve_account_ref`` (the resolver the ledger create
    choke uses) rather than testing existence: an import that only accepted an opaque id
    forced the model to call list_accounts purely to translate the name the user just said."""
    from backend.core.errors import ValidationError, recovery_hint
    from backend.services import accounts as accounts_service
    from backend.services import ledger as ledger_service
    from backend.services.import_common import ImportError_

    if ledger_service.accounts.find_by_id(account_id):  # id fast path, shared store handle
        return account_id
    try:
        resolved = accounts_service.resolve_account_ref(
            account_id, rows=ledger_service.accounts.all()
        )
    except ValidationError as exc:
        raise ImportError_(str(exc)) from exc
    if resolved is None:
        names = [(a.get("name") or "").strip() for a in ledger_service.accounts.all()]
        raise ImportError_(
            f"No account matching '{account_id}' by id or name."
            + recovery_hint(account_id, names, tool_hint="list_accounts")
        )
    return resolved


def _unimportable_attachment(ref: str) -> str:
    """Why an attachment ref can't be imported, told truthfully. Attachments are durable (kept
    until the user deletes them); the old "the upload cache expires after 30 minutes" text
    outlived that cache by months and sent Lucius off to hand-enter a statement he could
    have imported."""
    from backend.services.attachments import get_attachment_meta

    meta = get_attachment_meta(ref)
    if meta is None:
        return (
            "No attachment with that ref — it was deleted, or the ref is wrong. If it was "
            "already previewed or imported, call list_statements and use its statement_id."
        )
    name = meta.get("filename") or "That file"
    return (
        f"{name} isn't a statement file the importer reads (CSV, OFX/QFX, QIF, MT940, CAMT, "
        "XLSX or PDF) — a photo or an image, say. Read the rows from it yourself and confirm "
        "them with the user before saving anything."
    )


def _resolve_bytes(
    content: str | None,
    source_url: str | None,
    attachment_ref: str | None,
    statement_id: str | None = None,
) -> tuple[bytes, str | None, str | None]:
    """Resolve the import bytes from one of four sources (in priority order):
    1. ``attachment_ref`` — a chat attachment (kept until the user deletes it)
    2. ``statement_id``  — the statement store (a statement already previewed or imported)
    3. ``source_url``    — fetch from a URL
    4. ``content``       — pasted text

    Returns ``(content_bytes, filename_hint, cached_format)``.
    Raises ImportError_ for missing/multiple/oversized/unreachable/non-statement sources."""
    from backend.services.import_common import ImportError_, fetch_url_bytes

    has_text = bool((content or "").strip())
    url = (source_url or "").strip()
    ref = (attachment_ref or "").strip()
    sid = (statement_id or "").strip()
    if sum([has_text, bool(url), bool(ref), bool(sid)]) > 1:
        raise ImportError_(
            "Provide exactly ONE of attachment_ref, statement_id, content, or source_url."
        )
    if ref:
        from backend.services.attachments import get_attachment_import_source, private_refusal

        refusal = private_refusal(ref)
        if refusal:
            raise ImportError_(refusal)
        src = get_attachment_import_source(ref)
        if src is None:
            raise ImportError_(_unimportable_attachment(ref))
        cached_bytes, cached_fmt = src
        return cached_bytes, None, cached_fmt
    if sid:
        from backend.db.statement_store import StatementStore

        record = StatementStore().find_by_id(sid)
        if record is None:
            raise ImportError_(
                f"Statement {sid!r} not found. Call list_statements to see available statements."
            )
        file_path = record.get("file_path", "")
        cached_fmt = record.get("upload_entry", {}).get("format") or None
        filename_hint = record.get("filename") or None
        # Binary formats (xlsx, pdf, camt) always need the raw file bytes from disk.
        # Text-kind formats (csv, ofx, qif, mt940) can be served from the stored text view.
        _binary_fmts = frozenset({"xlsx", "pdf", "camt"})
        use_text = record.get("kind") == "text" and cached_fmt not in _binary_fmts
        if use_text:
            upload_text = record.get("upload_entry", {}).get("text") or ""
            if upload_text:
                return upload_text.encode("utf-8"), filename_hint, cached_fmt
        # Fall through to disk read for binary formats or when upload_entry.text is missing.
        if not file_path:
            raise ImportError_(
                f"Statement {sid!r} has no stored file. Re-upload the file and try again."
            )
        from backend.db.statement_store import resolve_statement_file

        # Containment, not just existence: `file_path` is verbatim from a portable bundle on
        # the import path, and this read feeds the parse pipeline the MODEL sees — so an
        # escaping path would pull an arbitrary file into a chat context. Same guard the REST
        # download uses; see `resolve_statement_file`.
        disk = resolve_statement_file(file_path, statement_id=sid)
        if disk is None or not disk.exists():
            raise ImportError_(
                f"Statement {sid!r} file is missing from disk. Re-upload the file and try again."
            )
        return disk.read_bytes(), filename_hint, cached_fmt
    if url:
        body, hint = fetch_url_bytes(url)  # may raise ImportError_/ImportTooLarge
        return body, hint, None
    if has_text:
        return content.encode("utf-8"), None, None
    raise ImportError_(
        "Provide content (pasted statement text), a source_url, attachment_ref "
        "(a statement file attached in chat), or statement_id (from list_statements)."
    )


_TOOL_PREVIEW_ROW_CAP = 80  # keep the tool reply model-sized; decisions reference row indexes


def _has_cached_parse(statement_id: str) -> bool:
    """True when this statement already carries a cached parse a commit can read."""
    from backend.db.statement_store import StatementStore

    record = StatementStore().find_by_id(statement_id)
    return bool(record and record.get("import_rows"))


def _commit_cached(
    statement_id: str, account_id: str, decisions: list | None, default_action: str, fmt: str = ""
) -> dict:
    """Apply the review against the CACHED parse — never re-parses, never re-bills.

    ``decisions=None`` is the plain "the user said yes to the preview" commit: every row
    follows ``default_action``, and ``decision_commit`` still honours the review it computed
    (needs-eyes and low-confidence rows default to skip, exact duplicates skip, an
    already-booked transfer pair skips) rather than booking everything blind."""
    from backend.db.statement_store import StatementStore
    from backend.services.import_common import ImportError_
    from backend.services.statements import decisions as decisions_service

    # Preview names the zero-based position `index`; the UI commit service names it `row`.
    # Accept either at this AI boundary, but never silently drop an unrecognized decision:
    # that used to skip an approved needs-eyes row while reporting a successful commit.
    record = StatementStore().find_by_id(statement_id) or {}
    row_count = len(record.get("import_rows") or [])
    normalized: list[dict] = []
    seen: set[int] = set()
    for decision in decisions or []:
        if not isinstance(decision, dict) or ("row" in decision) == ("index" in decision):
            raise ImportError_("Each decision needs exactly one of row or index.")
        raw = decision.get("row", decision.get("index"))
        if isinstance(raw, bool) or not isinstance(raw, int) or not 0 <= raw < row_count:
            raise ImportError_(f"Decision row/index must be an integer from 0 to {row_count - 1}.")
        if raw in seen:
            raise ImportError_(f"Duplicate decision for row {raw}.")
        seen.add(raw)
        normalized.append({**decision, "row": raw})
        normalized[-1].pop("index", None)

    out = {
        "ok": True,
        "committed": True,
        "statement_id": statement_id,
        "result": decisions_service.decision_commit(
            statement_id, account_id, normalized, default_action=default_action
        ),
    }
    if fmt:
        out["format"] = fmt
    return out


def _starting_balance_fact(account_id: str, rows: list[dict]) -> dict | None:
    """The preview's plain statement of what these rows do to the account's balance (see
    ``tracking_start.statement_rows_vs_start``). Never fails a preview over it."""
    from backend.services import ledger as ledger_service
    from backend.services.tracking_start import statement_rows_vs_start

    try:
        account = ledger_service.accounts.find_by_id(account_id)
        if not account:
            return None
        dates = [str(r.get("date") or "") for r in rows if isinstance(r, dict)]
        return statement_rows_vs_start(account, ledger_service.transactions.all(), dates)
    except Exception:  # an informational extra must never cost the user their preview
        logger.debug("import preview: starting-balance fact failed", exc_info=True)
        return None


def _run_import(input_data: dict) -> dict:
    """Synchronous core (runs in a thread): resolve bytes → detect format → preview/commit.
    Returns a JSON-able result dict; raises ImportError_ on any user-correctable problem.

    EVERY format goes through the ONE decision pipeline (``decision_preview`` →
    ``decision_commit``) — the legacy per-format ``commit_import`` wrappers are no longer
    reachable from here. They had no statement record, no import batch (so no rollback), no
    match bands, and imported flagged rows regardless; the structured formats (OFX/QFX/QIF/
    MT940/CAMT) and hand-mapped CSV/XLSX used to fall through to them, which is what made the
    chat path diverge from the UI. This closes the follow-up ADR
    2026-07-17-import-one-pipeline-unification left open."""
    from backend.services import import_common
    from backend.services.import_common import ImportError_
    from backend.services.statements import decisions as decisions_service

    account_id = (input_data.get("account_id") or "").strip()
    if not account_id:
        raise ImportError_(
            "account_id is required — pass the account's name (e.g. 'DBS Savings') or its id."
        )
    # Rebound to the resolved id: everything downstream (draft legs, the external_ref hash,
    # the dedup scope) keys on a real id, never the name the caller happened to type.
    account_id = _resolve_account(account_id)

    commit = bool(input_data.get("commit", False))
    decisions = input_data.get("decisions")
    statement_id = (input_data.get("statement_id") or "").strip()
    default_action = str(input_data.get("default_action") or "create")
    if decisions is not None and not commit:
        raise ImportError_("decisions require commit:true (preview already ran).")
    mapping = input_data.get("mapping") or {}
    if not isinstance(mapping, dict):
        raise ImportError_("mapping must be an object (CSV/XLSX only).")
    # _resolve_bytes enforces source exclusivity, but the cached-commit path below skips it —
    # check here too so a second source is REFUSED rather than silently ignored.
    if statement_id and (
        (input_data.get("content") or "").strip()
        or (input_data.get("source_url") or "").strip()
        or (input_data.get("attachment_ref") or "").strip()
    ):
        raise ImportError_(
            "Provide exactly ONE of attachment_ref, statement_id, content, or source_url."
        )

    # THE TWO-CALL CONTRACT: a commit naming a statement whose parse is already cached reads
    # that cache — with or without per-row decisions. Committing without decisions used to
    # fall through to a blind re-parse, which on a PDF meant a SECOND vision/LLM bill and
    # threw away every per-row verdict the preview had just computed.
    # An explicit `mapping` is the one thing that must NOT reuse the cache: a hand-mapping is
    # the caller overriding how this file was read, and decision_preview's own contract busts
    # the cached parse for it. Fall through so the re-read actually happens.
    if commit and statement_id and not mapping and _has_cached_parse(statement_id):
        return _commit_cached(statement_id, account_id, decisions, default_action)

    attachment_ref = (input_data.get("attachment_ref") or "").strip()
    content_bytes, filename_hint, cached_fmt = _resolve_bytes(
        input_data.get("content"),
        input_data.get("source_url"),
        attachment_ref,
        statement_id or None,
    )
    # An explicit `format` always wins; otherwise an attachment's already-detected format is
    # authoritative (it was sniffed at upload from the real filename + bytes — better than a
    # name-less re-sniff here); else fall back to a content sniff.
    declared = (input_data.get("format") or "").strip().lower()
    if declared in ("", "auto") and cached_fmt:
        declared = cached_fmt
    fmt = import_common.detect_format(declared, filename_hint, content_bytes)

    try:
        # A mapping is the csv/xlsx MANUAL-MAPPING TEACH flow: decision_preview saves it as
        # this source's format profile and parses through it (zero LLM), so a hand-mapped
        # sheet gets the byte-identical review — and the next import needs no mapping at all.
        # It is meaningless for structured/PDF sources, which decision_preview drops itself.
        preview = decisions_service.decision_preview(
            content_bytes,
            fmt,
            account_id,
            filename=filename_hint or "",
            mapping=mapping or None,
            source_attachment_id=attachment_ref or None,
        )
    except ImportError_ as exc:
        raise ImportError_(
            f"{exc} For a CSV/XLSX whose columns could not be read, pass an explicit "
            "`mapping` (it is remembered for this source). For a layout that needs "
            "explaining, teach it once with a `statement-format:` skill. Hand-entering rows "
            "via add_transactions is the last resort, not the next step."
        ) from None

    if not commit:
        # `ledger_check` rides out VERBATIM — including the server-resolved `on_statements` and
        # `near_cutoff` on every uncovered entry, which is the whole point of computing them
        # in `ledger_diff` rather than in the import UI (owner, 2026-07-31: "please handle
        # these on the backend, not client so the ai tool can get the same information").
        # Deliberately NOT enumerated in the tool description: that string is at 899/900 of
        # its hard cap (`test_import_transactions_desc_under_900_chars`), and both fields
        # carry a finished human `label`, so they need no schema gloss to be usable.
        rows = preview.get("rows") or []
        start_fact = _starting_balance_fact(account_id, rows)
        if len(rows) > _TOOL_PREVIEW_ROW_CAP:
            preview["rows"] = rows[:_TOOL_PREVIEW_ROW_CAP]
            preview["rows_truncated"] = len(rows) - _TOOL_PREVIEW_ROW_CAP
        out = {
            "ok": True,
            "format": fmt,
            "committed": False,
            "statement_id": preview.get("statement_id"),
            "result": preview,
        }
        if start_fact:
            out["starting_balance"] = start_fact
        return out

    # A one-shot commit (no prior preview) still parses exactly ONCE: the preview above
    # cached the parse, and the commit reads that cache like any reviewed commit would.
    return _commit_cached(
        str(preview.get("statement_id") or ""), account_id, decisions, default_action, fmt=fmt
    )


class ImportMapping(NestedInput):
    """Schema-of-record for ``import_transactions.mapping`` (see ``schema_from_model``)."""

    date: Optional[str] = Field(
        default=None,
        description=(
            "Header name of the transaction-date column. REQUIRED for a usable mapping — "
            "without it the sheet cannot be mapped at all."
        ),
    )
    description: Optional[str] = Field(
        default=None,
        description=(
            "Header name of the bank narration column. REQUIRED — the import is REFUSED "
            "without it, not degraded. Feeds dedup, payee resolution and spend rules."
        ),
    )
    amount: Optional[str] = Field(
        default=None,
        description=(
            "Header name of a SIGNED single-amount column (positive = money in, negative "
            "= money out). Use this OR inflow/outflow, never both — pass amount and the "
            "sheet is read as signed."
        ),
    )
    inflow: Optional[str] = Field(
        default=None,
        description=(
            "Header name of the money-IN column, for sheets with two UNSIGNED columns. "
            "Pair with outflow; use instead of amount. Mapping only ONE side is accepted "
            "but rarely right: the missing side contributes 0 and each value is read as "
            "an ABSOLUTE amount, so a SIGNED column mapped here books every negative row "
            "as income. For a signed column use `amount`."
        ),
    )
    outflow: Optional[str] = Field(
        default=None,
        description="Header name of the money-OUT column — the unsigned twin of inflow.",
    )
    account: Optional[str] = Field(
        default=None,
        description=(
            "Header name of a per-row account-NAME column, for a multi-account export. "
            "Only consulted when no destination account was chosen for the import; the "
            "name must match an existing account (case-insensitive) or that row errors."
        ),
    )
    category: Optional[str] = Field(
        default=None,
        description=(
            "Header name of a category column, if the sheet carries one. Optional — with "
            "no mapping the rows are categorized by your spend rules and payee defaults."
        ),
    )


class ImportTransactionsInput(ToolInput):
    """Schema-of-record for ``import_transactions`` (see ``schema_from_model``)."""

    attachment_ref: Optional[str] = Field(
        default=None,
        description=(
            "The ref_id of a statement file the user attached in chat (returned by the "
            "upload). Attachments are kept until the user deletes them, so this works for "
            "any file still attached — a scanned PDF included (read through the vision tier). "
            "Mutually exclusive with statement_id, content, and source_url."
        ),
    )
    statement_id: Optional[str] = Field(
        default=None,
        description=(
            "Persistent statement ID (from list_statements or the upload response). "
            "Use this for a statement already previewed or imported (it lives in the "
            "statement store), e.g. to commit a preview. Mutually exclusive with attachment_ref, content, "
            "and source_url."
        ),
    )
    content: Optional[str] = Field(
        default=None,
        description="The pasted statement text (CSV/OFX/QFX/QIF/MT940). Mutually exclusive with attachment_ref, statement_id, and source_url.",
    )
    source_url: Optional[str] = Field(
        default=None,
        description="A hosted file URL (CSV/OFX/QIF/MT940/CAMT/XLSX/PDF) or a Google-Sheet 'Publish to web → CSV' link. Mutually exclusive with attachment_ref, statement_id, and content.",
    )
    format: Optional[
        Literal["auto", "csv", "ofx", "qfx", "qif", "mt940", "camt", "xlsx", "pdf"]
    ] = Field(
        default=None,
        description="Import format. Default 'auto' detects from the content / URL / extension. 'qfx' is Quicken's OFX variant; 'mt940' is SWIFT; 'camt' is ISO 20022 CAMT.053 XML; 'xlsx' is an Excel sheet; 'pdf' is a bank/card PDF statement (LLM-parsed). xlsx/camt/pdf are binary/XML — use source_url.",
    )
    account_id: str = Field(
        description=(
            "Destination ONW account — its NAME ('DBS Savings', case-insensitive) or "
            "its id. Required."
        ),
    )
    mapping: ImportMapping = Field(
        default_factory=ImportMapping,
        description=(
            "CSV/XLSX ONLY — which columns map to which fields: date, description, and "
            "EITHER amount (signed: +in/-out) OR inflow + outflow; optional account, "
            "category. Ignored for OFX/QFX/QIF/MT940/CAMT/PDF (those are structured). "
            "Each value is a column NAME from the header."
        ),
    )
    commit: Optional[bool] = Field(
        default=None,
        description=(
            "false (default) = PREVIEW only, writes nothing, returns statement_id. "
            "true = book it — pass the statement_id the preview returned so the cached "
            "parse is reused instead of re-parsed. Only once the user has reviewed the preview."
        ),
    )
    decisions: List[Dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "OPTIONAL per-row actions from a prior preview (with statement_id + "
            "commit:true) — [{index: <preview index>, action: create|merge|skip, "
            "existing_id?: <merge target from the row's match flag>, edits?: "
            "{category?, payee?, amount?, date?, direction?, account_id?, to_account_id?}}]. "
            "to_account_id (an existing account's name or id) books the row as a TRANSFER to "
            "it in this batch — e.g. 'TRANSFER TO REVOLUT'; the statement amount stays on the "
            "statement account's leg and the other leg is FX-converted. Rows not "
            "listed follow default_action. An approved needs-eyes/low-confidence row "
            "requires an explicit create decision even when the user says 'approve all'; "
            "omitted flagged rows and exact duplicates still skip."
        ),
        json_schema_extra=bare_object_schema,
    )
    default_action: Optional[Literal["create", "skip"]] = Field(
        default=None,
        description="Decision-commit: what happens to rows without a decision.",
    )


@tool(
    "import_transactions",
    description=(
        "Import a bank/card statement (CSV, OFX, QFX, QIF, MT940, CAMT, XLSX, PDF). "
        "Never hand-enter its rows with add_transactions. Provide ONE source "
        "(attachment_ref, statement_id, content, or source_url) and account_id. "
        "Always make TWO calls: (1) commit=false previews without writes, returning "
        "statement_id, row flags, reconcile and ledger_check; report the counts. "
        "(2) commit=true with that SAME statement_id uses the cached parse. For EACH "
        "user-approved needs-eyes/low-confidence row, pass {index: n, action: create} "
        "in decisions. Omission SKIPS it even if the user said 'approve all'. Verify the "
        "booked count; retry skipped approved rows on the SAME statement with decisions, "
        "never via add_transactions. Duplicates/booked transfers skip unless separately "
        "resolved. Re-import deduplicates; each commit has a rollback_import_batch batch."
    ),
    input_schema=schema_of(ImportTransactionsInput, tool_name="import_transactions"),
    mutates=("ledger", "payees"),
)
async def import_transactions(input_data: dict):
    from backend.core.errors import ValidationError
    from backend.services.import_common import ImportError_

    try:
        result = await asyncio.to_thread(_run_import, input_data)
    except ImportError_ as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), None
    except ValidationError as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), None
    return json.dumps(result, ensure_ascii=False), None
