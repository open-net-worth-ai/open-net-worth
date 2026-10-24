"""Files AND tables (since the JSON→DB storage migration moved some datasets off disk)
that must never leave the instance, and must never be rolled back by a restore.

Two questions come up wherever whole-``DATA_DIR`` snapshots are moved around, and both used
to be answered only inside ``ai/checkpoint.py``:

  1. **May this file be handed to a caller?** Credentials may not. ``GET /api/data/export`` answers
     this with an allowlist (``core.portable.PORTABLE_FILES``). Anything that streams a raw
     disaster-recovery archive has to answer it too, or one authenticated GET hands over the
     session-signing key, the API token and every bank/broker/LLM credential on the box.
     ``SECRET_FILES`` (below) answers this for a credential still living in its own JSON file;
     ``SECRET_TABLES`` answers the SAME question for a credential whose storage has since moved
     into a table inside ``onw.db`` (Wave 5D1) — see that frozenset's own docstring for why a
     table-level answer needs a different mechanism than a file-level skip.

  2. **May this file be reverted to an older copy?** A credential may not — restoring one
     resurrects a key the owner rotated. Neither may a single-use queue (the nonce it already
     popped becomes replayable), an approval grant (revoke → restore → the grant is back), or
     an audit trail (a data restore must not erase how the data got here).

``NON_RESTORABLE_TABLES``, below, answers question 2 for the same class of dataset after its
storage moved from a JSON file into a table inside ``onw.db`` — the reasoning is IDENTICAL,
only where the bytes live changed. ``backend/services/backup.py`` is the only reader: its
whole-file restore can express "keep this file's current contents" for an ordinary file, but
``onw.db`` is ONE file holding both restorable and non-restorable data, so a file-level skip
would have to refuse the entire database to protect the handful of tables in it that need it.
The table list is what lets it protect exactly those instead.

``ai/checkpoint`` answers both file-level questions with its own ``_EXCLUDED_PATHS`` /
``_EXCLUDED_TABLES``. This module is the same knowledge at a layer ``services`` can reach —
``services`` must not import ``ai``, and the backup service needs exactly these answers.
``tests/test_sensitive_paths.py`` fails if the file lists drift apart, so a newly-added
connector secret cannot be excluded from checkpoints while silently remaining downloadable
and restorable.
"""

from __future__ import annotations

from pathlib import Path

from backend.core.paths import (
    ALPACA_CONFIG_JSON,
    AUTH_JSON,
    AUTOMATION_RUNTIME_JSON,
    CHANNELS_CONFIG_JSON,
    CHAT_PAIRING_JSON,
    CONNECTOR_SESSIONS_JSON,
    DISCORD_CONFIG_JSON,
    EMAIL_INGEST_JSON,
    ENABLE_BANKING_CONFIG_JSON,
    GOCARDLESS_CONFIG_JSON,
    IBKR_CONFIG_JSON,
    MCP_CLIENTS_JSON,
    MCP_PENDING_CALLS_JSON,
    MCP_WRITE_LOG_JSON,
    MODEL_CONFIG_JSON,
    PENDING_PLANS_JSON,
    PLAID_CONFIG_JSON,
    PLUGGY_CONFIG_JSON,
    SEARCH_CONFIG_JSON,
    SIMPLEFIN_CONFIG_JSON,
    SLACK_CONFIG_JSON,
    TELEGRAM_CONFIG_JSON,
    TXN_AUDIT_JSON,
    WHATSAPP_INBOUND_CONFIG_JSON,
    YODLEE_CONFIG_JSON,
)

# ── 1. Credential-bearing files ──────────────────────────────────────────────
# Every one of these holds a write-only secret: an API key, a bearer token, a signing key or
# a private key. None may ever be streamed to a caller, however well authenticated — an
# archive download is an exfiltration primitive, and a stolen session cookie or a
# loopback-trusted request should not be able to turn one GET into every credential you own.
SECRET_FILES: frozenset[Path] = frozenset(
    p.resolve()
    for p in (
        AUTH_JSON,  # password hash + session-signing HMAC key + API bearer token
        MODEL_CONFIG_JSON,  # per-provider LLM API keys
        SEARCH_CONFIG_JSON,  # search-provider API keys
        CHANNELS_CONFIG_JSON,  # SMTP password / webhook secret
        MCP_CLIENTS_JSON,  # external-MCP connection secrets
        # EMAIL_INGEST_JSON holds a bare IMAP account password (``imap_password``) — a
        # PRE-EXISTING gap fixed alongside the Wave 5D2a migration: this file was never
        # actually a SECRET_FILES member despite storing a real credential, so a sanitised
        # backup download shipped it in the clear. Found while classifying email_ingest's
        # new `secret_config` row for that migration; fixed here at the file level too so
        # the `.migrated-<ts>` rename this cutover produces inherits the correct secrecy
        # (see `_norms()` below).
        EMAIL_INGEST_JSON,
        TELEGRAM_CONFIG_JSON,
        SLACK_CONFIG_JSON,  # bot token + signing secret
        DISCORD_CONFIG_JSON,
        WHATSAPP_INBOUND_CONFIG_JSON,  # app secret + verify token
        SIMPLEFIN_CONFIG_JSON,  # access URL is itself a bearer credential
        ALPACA_CONFIG_JSON,
        IBKR_CONFIG_JSON,  # Flex token
        GOCARDLESS_CONFIG_JSON,
        PLAID_CONFIG_JSON,
        PLUGGY_CONFIG_JSON,
        ENABLE_BANKING_CONFIG_JSON,  # RSA private key + cached JWT
        YODLEE_CONFIG_JSON,
        CONNECTOR_SESSIONS_JSON,
    )
)

# ── 2. Files a restore must not revert ───────────────────────────────────────
# The secrets above, plus state where rolling backwards is itself the hazard.
NON_RESTORABLE_FILES: frozenset[Path] = SECRET_FILES | frozenset(
    p.resolve()
    for p in (
        # Single-use nonce queues. Popping is what makes a confirmed call unrepeatable;
        # restoring un-pops it, and approvals never expire — so an already-applied external
        # money movement would become applicable a second time with no new confirm.
        MCP_PENDING_CALLS_JSON,
        PENDING_PLANS_JSON,
        # Forensic records. A data restore must never erase how the data got here.
        TXN_AUDIT_JSON,
        MCP_WRITE_LOG_JSON,
        # Durable scheduler queue/outbox — reverting it replays or loses delivered events.
        AUTOMATION_RUNTIME_JSON,
        # Pending pairing requests: a runtime queue, and re-authorising a sender the owner
        # removed is the same class of mistake as restoring a connector allowlist.
        CHAT_PAIRING_JSON,
    )
)

# ── 3. Tables a credential lives in ──────────────────────────────────────────
# The Wave 5D1 table-level counterpart of SECRET_FILES (section 1) — for a credential whose
# storage has moved from its own JSON file into a table inside `onw.db`. `write_sanitised_copy`
# (services/backup.py) ships `onw.db` WHOLE to a caller (it is one zip member, stripped or kept
# as a unit by `is_secret()`'s file-path test) — that is correct for a database with nothing
# secret inside it, but once a credential lives in a TABLE, `onw.db` is no longer uniformly
# safe to hand over verbatim. A table in this set gets a DIFFERENT treatment than an ordinary
# secret file: `write_sanitised_copy` writes a working COPY of the database with every row of
# every SECRET_TABLES table DELETEd (`_sanitise_db_bytes`), rather than skip the whole `onw.db`
# member — the database still ships (every OTHER table's data is exactly what a disaster-
# recovery download exists for), only the credential-bearing rows are stripped, mirroring
# `is_secret()`'s per-file granularity at the per-table level instead.
#
# `secret_config` (db/tables.py) holds `auth` and `model_config` as namespace rows in ONE table
# (deliberately NOT a `runtime_state` namespace — see that table's own docstring — so this set
# can name it without also catching harness_state/app_config, `runtime_state`'s non-secret
# occupants). The table-level membership covers every namespace row inside it, so a future
# secret singleton needs no new entry here — move a table name here ONLY if it can't share
# `secret_config`'s namespace-row shape.
#
# `connector_secrets` (connectors-table RFC) is the connectors' credential table — one row per
# connector, FK to `connectors`. Only IT is secret: `connectors` itself (settings + app-written
# state, redacted `last_error`) stays in a sanitised backup (it is SQL-denied to Lucius all the
# same, see `db/sql_console`), but is NON_RESTORABLE below so a restore never pairs old settings
# with the current secrets.
SECRET_TABLES: frozenset[str] = frozenset(
    {
        "secret_config",
        "connector_secrets",
    }
)

# Tables that no longer exist in this schema but whose rows an OLDER database (a backup taken
# before the upgrade) still carries — credentials included. A sanitised backup download and a QA
# clone strip them too, by name, when present. Names never leave this set: an old backup can
# outlive any release.
RETIRED_SECRET_TABLES: frozenset[str] = frozenset(
    {
        # The external-MCP client registry (url/command/env/auth_header/OAuth tokens), dropped
        # by the connectors-table migration in favour of `connectors` + `connector_secrets`.
        "mcp_clients",
    }
)

# ── 4. Tables a restore must not revert ──────────────────────────────────────
# The DB counterpart of section 2, above — SAME reasoning, for the datasets whose storage has
# since moved from a JSON file into a table inside `onw.db`. `restore_backup` swaps `onw.db`
# wholesale (nothing above has visibility inside it), so these tables need their own guard:
# `services/backup.py` captures their current rows before the swap and re-applies them right
# after. One place a future migrated dataset gets added — move an entry here the moment its
# store becomes a DB table.
#
# Every SECRET_TABLES table is unioned in here too — a credential must never come back from an
# old backup any more than it may leave in a download (secret ⊆ non-restorable, the same
# relationship SECRET_FILES has to NON_RESTORABLE_FILES above).
NON_RESTORABLE_TABLES: frozenset[str] = (
    frozenset(
        {
            "desktop_access_grants",
            "desktop_ingest_jobs",
            # The report of what an import booked and what verification found — a restore of the
            # backup the import itself took must not erase the record of that import.
            "import_runs",
            # Forensic record — mirrors TXN_AUDIT_JSON above. A data restore must never erase how
            # the data got here.
            "txn_audit",
            # Forensic record — mirrors MCP_WRITE_LOG_JSON above.
            "mcp_write_log",
            # Durable scheduler queue/outbox — mirrors AUTOMATION_RUNTIME_JSON above. Reverting it
            # replays or loses delivered events.
            "automation_runtime",
            # WAVE 5D2b — the confirm-gate's single-use token queue, now DB-backed. Mirrors
            # PENDING_PLANS_JSON above: popping a token is what makes a confirmed write
            # unrepeatable, and an awaiting-approval row never expires (ADR
            # 2026-07-30-approvals-never-expire) — so restoring an older snapshot would un-pop
            # an already-applied write and make it applicable a second time with no new
            # confirm. External-MCP's own former nonce queue, `mcp_pending_calls`, was
            # ABSORBED into this same table (revision 0046, 2026-08-22) and no longer exists
            # as a separate table — see `backend/db/tables.py`'s note.
            "pending_plans",
            # WAVE 5D2b — pending chat-channel pairing requests, now DB-backed. Mirrors
            # CHAT_PAIRING_JSON above: re-authorising a sender the operator already
            # approved/rejected past is the same class of mistake as restoring a revoked grant.
            "chat_pairing",
            # THE permission table — one level per (owner, tool), for every tool in the app.
            # The approval model: set a tool back to "needs_approval" (or "blocked") ->
            # restore a backup taken before that -> the tool is auto-firing again. DB-native
            # from revision 0039, so it has no NON_RESTORABLE_FILES twin. NOT secret: it
            # holds decisions, not credentials.
            "tool_permissions",
            # The connectors' non-secret half (settings, sync cursors, OAuth metadata). Not a
            # secret — but a restore must keep the CURRENT rows paired with the current
            # `connector_secrets`, and an old copy would rewind a mailbox cursor.
            "connectors",
        }
    )
    | SECRET_TABLES
)


# A cutover (`db/migrate._finish_cutovers`) renames each absorbed JSON source to
# `<name>.migrated-<ts>` and leaves it in DATA_DIR PERMANENTLY as the manual-restore path. That
# renamed file holds the SAME plaintext as the original, so a secret source's `.migrated-<ts>`
# sibling MUST inherit the original's secrecy — otherwise it ships raw in a sanitised backup
# download (`is_secret`) or is reverted by a restore (`is_non_restorable`). Found by the Wave 5D1
# security review: without this, every absorbed `*_config.json.migrated-<ts>` credential leaked.
#
# Generalized (data-hygiene sweep): a HAND-MADE copy carries the identical hazard and is not
# limited to the `.migrated-<ts>` shape a cutover mints — an operator's own
# `auth.json.pre-reset-20260723-185559` (a manual safety copy made before editing the file by
# hand) is every bit as much the live credential's plaintext, and shipped raw in a "sanitised"
# backup download until this fix. Rather than enumerate every rename convention this repo (or
# an operator) might ever use, `_is_sibling_of` matches by PREFIX: any path whose resolved,
# case-folded form is exactly a known path, or is that known path plus a literal "." plus
# anything, inherits that path's classification. A `<secret>.<anything>` sibling can only ever
# be a copy or rename OF `<secret>` — nothing else produces that name — so this cannot
# over-match an unrelated file (`notauth.json.migrated-x` does not start with `auth.json.`).
def _norm(path: Path) -> str:
    return str(path.resolve()).casefold()


def _is_sibling_of(candidate_norm: str, known_norms: frozenset[str]) -> bool:
    if candidate_norm in known_norms:
        return True
    return any(candidate_norm.startswith(f"{known}.") for known in known_norms)


def is_secret(path: Path) -> bool:
    """True when `path` holds credentials and must not be handed to a caller.

    Compared case-INSENSITIVELY. `Path.resolve()` does not case-fold, and macOS/APFS and
    Docker Desktop volumes are case-insensitive by default — so a plain set membership test
    lets `Auth.json` through while it still writes over `auth.json` on disk. Any
    `<secret-file-name>.<anything>` sibling matches too — a cutover's `.migrated-<ts>` rename,
    or a hand-made copy like `auth.json.pre-reset-20260723-185559` (see `_is_sibling_of`).
    """
    return _is_sibling_of(_norm(path), _SECRET_NORM)


def is_secret_table(name: str) -> bool:
    """True when `name` is a DB table holding credentials that must not be handed to a
    caller — the table-level counterpart of `is_secret()` for a dataset whose storage has
    moved off a JSON file and into `onw.db` (see SECRET_TABLES's own docstring)."""
    return name in SECRET_TABLES


def is_non_restorable(path: Path) -> bool:
    """True when `path` must keep its CURRENT contents through a restore (a secret source's
    `.migrated-<ts>` rename, or any other `<name>.<anything>` sibling, included — see
    `_is_sibling_of`)."""
    return _is_sibling_of(_norm(path), _NON_RESTORABLE_NORM)


_SECRET_NORM = {_norm(p) for p in SECRET_FILES}
_NON_RESTORABLE_NORM = {_norm(p) for p in NON_RESTORABLE_FILES}
