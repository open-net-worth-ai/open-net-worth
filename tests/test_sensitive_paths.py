"""core.sensitive — the "never hand out" / "never roll back" sets, and their drift guard.

`ai/checkpoint` has kept its own `_EXCLUDED_PATHS` since long before this module existed. The
two lists answer overlapping questions, so the real risk is DRIFT: someone adds a connector
secret, excludes it from checkpoints (because that is the obvious place), and it silently
stays downloadable and restorable. That is what `test_no_drift_from_checkpoint_exclusions`
is for.
"""

from __future__ import annotations

from pathlib import Path

from backend.core.paths import (
    ACCOUNTS_JSON,
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
    TRANSACTIONS_JSON,
    TXN_AUDIT_JSON,
    WHATSAPP_INBOUND_CONFIG_JSON,
    YODLEE_CONFIG_JSON,
)
from backend.core.sensitive import (
    NON_RESTORABLE_FILES,
    NON_RESTORABLE_TABLES,
    SECRET_FILES,
    SECRET_TABLES,
    is_non_restorable,
    is_secret,
)

# ── Secrets ───────────────────────────────────────────────────────────────────


def test_credential_files_are_secret():
    # EMAIL_INGEST_JSON (a bare IMAP account password) was a PRE-EXISTING gap fixed
    # alongside the Wave 5D2a migration — never actually a SECRET_FILES member despite
    # storing a real credential (core.sensitive.SECRET_FILES's own comment). Pinned here
    # so it never regresses.
    for p in (AUTH_JSON, MODEL_CONFIG_JSON, PLAID_CONFIG_JSON, EMAIL_INGEST_JSON):
        assert is_secret(p), f"{p.name} holds credentials and must never be served"


def test_user_data_is_not_secret():
    """The whole point of a backup download is to get your DATA out."""
    for p in (ACCOUNTS_JSON, TRANSACTIONS_JSON):
        assert not is_secret(p)


def test_secret_matching_is_case_insensitive():
    """`Path.resolve()` does not case-fold, but macOS/APFS and Docker Desktop volumes are
    case-INsensitive — so `Auth.json` names the same file on disk while comparing unequal."""
    assert is_secret(AUTH_JSON.parent / AUTH_JSON.name.upper())
    assert is_secret(AUTH_JSON.parent / "Auth.json")


def test_secret_matching_is_path_based_not_name_based():
    """A file called auth.json somewhere else entirely is not THE auth.json."""
    assert not is_secret(Path("/tmp/some-other-place/auth.json"))


def test_traversal_forms_normalise_onto_the_same_file():
    assert is_secret(AUTH_JSON.parent / "." / AUTH_JSON.name)
    assert is_secret(AUTH_JSON.parent / "sub" / ".." / AUTH_JSON.name)


# ── Non-restorable ────────────────────────────────────────────────────────────


def test_every_secret_is_also_non_restorable():
    """Restoring a credential resurrects one the owner rotated."""
    assert SECRET_FILES <= NON_RESTORABLE_FILES


def test_grants_and_nonces_and_audit_are_non_restorable():
    # The file-era `tool_grants.json` entry is GONE (2026-08-14): its store was absorbed
    # into `tool_permissions` and deleted, and no migration reads the file any more, so the
    # path constant went with it rather than lingering as archaeology. The property it
    # asserted — revoke → restore → the permission is back would be privilege escalation
    # via an unrelated feature — now lives on the TABLE, in
    # `test_permission_table_is_non_restorable` below.
    # Popping a nonce is what makes a confirmed money movement unrepeatable.
    assert is_non_restorable(MCP_PENDING_CALLS_JSON)
    # A data restore must not erase how the data got here.
    assert is_non_restorable(TXN_AUDIT_JSON)


def test_user_data_is_restorable():
    """Otherwise a restore would restore nothing anyone wanted restored."""
    assert not is_non_restorable(ACCOUNTS_JSON)
    assert not is_non_restorable(TRANSACTIONS_JSON)


# ── The drift guard ───────────────────────────────────────────────────────────


def test_no_drift_from_checkpoint_exclusions():
    """Every checkpoint-excluded file that is a SECRET or an approval/nonce/audit record must
    also be non-restorable here.

    Deliberately not a blanket equality: `_EXCLUDED_PATHS` also holds derived caches
    (fx_cache, names_cache, price history) and behavioural logs (cost_log, model_telemetry).
    Those are fine to restore — reverting a cache costs a refetch, not a security property.
    The overlap that MATTERS is the credential/approval/forensic set, and it is pinned by
    name so adding a connector secret to one list and not the other fails here.
    """
    from backend.ai.checkpoint import _EXCLUDED_PATHS

    must_also_be_non_restorable = {
        "auth.json",
        "model_config.json",
        "search_config.json",
        "channels_config.json",
        # A bare IMAP account password — a PRE-EXISTING gap fixed alongside the Wave
        # 5D2a migration (core.sensitive.SECRET_FILES's own comment on EMAIL_INGEST_JSON).
        "email_ingest.json",
        "mcp_clients.json",
        "telegram_config.json",
        "slack_config.json",
        "discord_config.json",
        "whatsapp_inbound_config.json",
        "simplefin_config.json",
        "alpaca_config.json",
        "ibkr_config.json",
        "gocardless_config.json",
        "plaid_config.json",
        "pluggy_config.json",
        "enable_banking_config.json",
        "yodlee_config.json",
        "connector_sessions.json",
        "mcp_pending_calls.json",
        "pending_plans.json",
        "txn_audit.json",
        "mcp_write_log.json",
        "automation_runtime.json",
        "chat_pairing.json",
    }
    checkpoint_names = {p.name for p in _EXCLUDED_PATHS}
    ours = {p.name for p in NON_RESTORABLE_FILES}

    # 1. Everything on the sensitive list above is excluded from checkpoints too.
    missing_from_checkpoint = must_also_be_non_restorable - checkpoint_names
    assert not missing_from_checkpoint, (
        f"checkpoint no longer excludes {sorted(missing_from_checkpoint)} — "
        "if that was deliberate, core.sensitive needs the same decision"
    )
    # 2. …and every one of them is non-restorable here.
    missing_from_ours = must_also_be_non_restorable - ours
    assert not missing_from_ours, (
        f"core.sensitive does not protect {sorted(missing_from_ours)} — "
        "a restore would roll it back"
    )


def test_non_restorable_tables_matches_db_resident_non_restorable_files():
    """The DB-table mirror of ``test_no_drift_from_checkpoint_exclusions``: every
    ``NON_RESTORABLE_FILES`` dataset that has since moved from a JSON file into a table
    inside ``onw.db`` — the file paths stay listed (defense in depth for the now-dead
    path), but ``services/backup.py`` actually protects them at the TABLE level, via
    ``NON_RESTORABLE_TABLES``. A future dataset that migrates from a file to a table
    without also gaining a ``NON_RESTORABLE_TABLES`` entry would silently lose restore
    protection — a live data-loss bug, not a cosmetic gap — so this is pinned by name,
    both directions, exactly like the checkpoint drift guard above.

    Reshaped from a 1:1 file->table map (the original four: tool_grants/txn_audit/
    mcp_write_log/automation_runtime) to a MANY-FILES-TO-ONE-TABLE map: ``secret_config``
    (Wave 5D1/5D2a) *aggregates* every credential — auth, model config, email ingest, and
    every connector config — into ONE namespace-keyed table (core.sensitive.SECRET_TABLES's
    own docstring), so eighteen distinct ``NON_RESTORABLE_FILES`` entries collapse onto
    that single table name. ``mcp_clients.json`` (one token PER configured MCP server) now
    lives in the connectors tables. The seven non-secret non-restorable datasets
    (approval grants, nonce/token queues, audit/write logs, the scheduler outbox, pending
    pairing requests) still map 1:1, unchanged from the original four plus three more of the
    same shape (``pending_plans``/``mcp_pending_calls``/``chat_pairing``, WAVE 5D2b).

    ``DB_NATIVE_NON_RESTORABLE`` is the third category, added by the Connectors redesign: a
    non-restorable dataset that NEVER had a JSON file to migrate from. The file->table map
    cannot express it (there is no file), but leaving it out of check 3 would either fail
    this test or force the check to be weakened into uselessness — so it is named
    explicitly, which keeps check 3 exhaustive in both directions. An entry belongs here
    only if the table is genuinely DB-native; a dataset that DID have a file goes in the
    map above, where check 1 also pins the legacy path."""
    file_to_table = {
        # Aggregated into ONE secret_config namespace row apiece — Wave 5D1 (connector
        # configs + connector_sessions) and Wave 5D2a (auth/model_config/email_ingest).
        AUTH_JSON: "secret_config",
        MODEL_CONFIG_JSON: "secret_config",
        SEARCH_CONFIG_JSON: "secret_config",
        CHANNELS_CONFIG_JSON: "secret_config",
        EMAIL_INGEST_JSON: "secret_config",
        TELEGRAM_CONFIG_JSON: "secret_config",
        SLACK_CONFIG_JSON: "secret_config",
        DISCORD_CONFIG_JSON: "secret_config",
        WHATSAPP_INBOUND_CONFIG_JSON: "secret_config",
        SIMPLEFIN_CONFIG_JSON: "secret_config",
        ALPACA_CONFIG_JSON: "secret_config",
        IBKR_CONFIG_JSON: "secret_config",
        GOCARDLESS_CONFIG_JSON: "secret_config",
        PLAID_CONFIG_JSON: "secret_config",
        PLUGGY_CONFIG_JSON: "secret_config",
        ENABLE_BANKING_CONFIG_JSON: "secret_config",
        YODLEE_CONFIG_JSON: "secret_config",
        CONNECTOR_SESSIONS_JSON: "secret_config",
        # Each configured server's token moved into the connectors tables (RFC connectors-table).
        MCP_CLIENTS_JSON: "connector_secrets",
        # Non-secret non-restorable datasets — each still 1:1 with its own table, EXCEPT
        # MCP_PENDING_CALLS_JSON: its OWN table (`mcp_pending_calls`) was dropped at revision
        # 0046 (2026-08-22, external-MCP's confirm→apply migrated onto the generic
        # tool_use_id-keyed mechanism — ADR
        # `2026-08-22-external-mcp-confirm-migrates-onto-generic-resolve`), so its dataset's
        # CURRENT table is `pending_plans` — the same one `PENDING_PLANS_JSON` already maps
        # to. A dict value can be shared by two keys; this correctly asserts both source files
        # protect the table their data actually lives in today.
        MCP_PENDING_CALLS_JSON: "pending_plans",
        PENDING_PLANS_JSON: "pending_plans",
        TXN_AUDIT_JSON: "txn_audit",
        MCP_WRITE_LOG_JSON: "mcp_write_log",
        AUTOMATION_RUNTIME_JSON: "automation_runtime",
        CHAT_PAIRING_JSON: "chat_pairing",
    }

    # DB-native from their first revision — no JSON file ever existed, so there is no
    # NON_RESTORABLE_FILES entry to pair them with and nothing for check 1 to assert.
    db_native_non_restorable = {
        "desktop_access_grants",
        "desktop_ingest_jobs",
        "import_runs",
        # THE permission table — one level per (owner, tool), for every tool in the app
        # (revision 0039, renamed from `connector_tool_permissions` and given the absorbed
        # `tool_grants` rows at 0040). Set a tool back to "needs_approval" (or "blocked")
        # -> restore a backup taken before that -> the tool is auto-firing again. The
        # absorbed half's file-era twin (`tool_grants.json`) is gone with its store, so
        # this dataset is now DB-native on both halves.
        "tool_permissions",
        # The connectors table pair (connectors-table RFC): DB-native, no JSON file ever held
        # them. Their fields were migrated OUT of `secret_config`/`mcp_clients` rows, which is
        # why those files keep their entries above.
        "connectors",
        "connector_secrets",
    }

    # 1. Each file is still (deliberately) present in NON_RESTORABLE_FILES — core.
    #    sensitive's own comment: kept there for the legacy/manual-restore path, defense
    #    in depth. `is_non_restorable` (not a raw set-difference) resolves/casefolds
    #    internally, so this cannot false-positive on an unresolved-vs-resolved path
    #    mismatch (e.g. macOS's /var/folders -> /private/var/folders symlink) the way a
    #    bare set operation would.
    missing_from_files = {p.name for p in file_to_table if not is_non_restorable(p)}
    assert not missing_from_files, (
        f"{sorted(missing_from_files)} dropped from NON_RESTORABLE_FILES — "
        "core.sensitive keeps the legacy path there deliberately"
    )

    # 2. Every table these files migrated onto is itself protected — no orphan on the
    #    FILE side (a migrated dataset silently unprotected at the table level, the
    #    CRITICAL-class data-loss bug this guard exists to catch).
    expected_tables = set(file_to_table.values())
    missing_tables = expected_tables - NON_RESTORABLE_TABLES
    assert not missing_tables, (
        f"{sorted(missing_tables)} not in NON_RESTORABLE_TABLES — a migrated dataset "
        "silently lost its restore protection when it moved off a file"
    )

    # 2b. Every DB-native entry is protected too (the same guarantee, asserted from the
    #     other direction: this set is the test's own claim about what exists, so a table
    #     dropped from NON_RESTORABLE_TABLES fails here rather than silently passing).
    missing_db_native = db_native_non_restorable - NON_RESTORABLE_TABLES
    assert not missing_db_native, (
        f"{sorted(missing_db_native)} dropped from NON_RESTORABLE_TABLES — a DB-native "
        "non-restorable dataset silently lost its restore protection"
    )

    # 3. …and nothing else is in there either — no orphan on the TABLE side (a table
    #    protected "by mistake" that is neither one of the migrated datasets nor a
    #    declared DB-native one, which would mean this test's own map is stale and no
    #    longer proves what it claims to).
    extra_tables = NON_RESTORABLE_TABLES - expected_tables - db_native_non_restorable
    assert not extra_tables, (
        f"NON_RESTORABLE_TABLES also protects {sorted(extra_tables)}, which no file in "
        "this test's file_to_table map migrated onto and which is not declared DB-native "
        "— update the map (or db_native_non_restorable) if that table is genuine"
    )


def test_secret_tables_are_a_subset_of_non_restorable_tables():
    """The DB-table mirror of ``test_every_secret_is_also_non_restorable``: a credential
    whose storage moved into a table must never come back from an old backup any more
    than it may leave in a download — same relationship SECRET_FILES has to
    NON_RESTORABLE_FILES, one layer down."""
    assert "secret_config" in SECRET_TABLES
    assert "connector_secrets" in SECRET_TABLES
    assert SECRET_TABLES <= NON_RESTORABLE_TABLES


# ── Regression: a cutover's `.migrated-<ts>` rename inherits the original's secrecy ────


def test_migrated_suffix_rename_of_a_secret_file_is_still_protected():
    """A cutover (``db.migrate._finish_cutovers``) renames an absorbed JSON source to
    ``<name>.migrated-<ts>`` and leaves it in DATA_DIR PERMANENTLY as the manual-restore
    path — holding the SAME plaintext as the original. Regression for the Wave 5D1
    security-review finding this fixed (``core.sensitive._is_sibling_of``'s own docstring):
    without the suffix-strip, every absorbed ``*_config.json.migrated-<ts>`` credential
    would ship raw in a sanitised backup download and be reverted by a restore."""
    migrated = AUTH_JSON.parent / f"{AUTH_JSON.name}.migrated-20260810T083821Z"
    assert is_secret(migrated), "a secret source's .migrated-<ts> rename must stay secret"
    assert is_non_restorable(migrated), (
        "a secret source's .migrated-<ts> rename must stay non-restorable"
    )


def test_migrated_suffix_rename_of_a_non_secret_file_is_not_secret():
    """The suffix-strip must not over-match: an ordinary absorbed dataset's
    ``.migrated-<ts>`` sibling (real user data, not a credential — accounts.json is one
    of the money six, itself long since cutover-absorbed) must NOT become secret or
    non-restorable just because it carries the same rename shape."""
    migrated = ACCOUNTS_JSON.parent / f"{ACCOUNTS_JSON.name}.migrated-20260810T083821Z"
    assert not is_secret(migrated)
    assert not is_non_restorable(migrated)


def test_permission_table_is_non_restorable():
    """The successor to the retired `TOOL_GRANTS_JSON` assertion above.

    `tool_permissions` is the ONE permission store since 2026-08-14 (it absorbed
    `tool_grants`), and it is DB-native — so the guarantee that used to be a file-path
    membership is now a TABLE membership, and this is where it gets pinned. Revoke an
    `always_allow` → restore a backup taken before the revocation → the tool is auto-firing
    again: exactly the privilege escalation the file-era entry existed to prevent.
    """
    from backend.core.sensitive import NON_RESTORABLE_TABLES

    assert "tool_permissions" in NON_RESTORABLE_TABLES
    # And the retired half is really gone — not merely unreferenced.
    assert "tool_grants" not in NON_RESTORABLE_TABLES


# ── Regression: ANY hand-made copy of a secret file, not just a cutover's own rename ────


def test_hand_made_copy_of_a_secret_file_is_still_secret():
    """A hand-made safety copy an operator makes before editing a secret file by hand (e.g.
    ``cp auth.json auth.json.pre-reset-20260723-185559``) carries the SAME plaintext as the
    original — nothing renamed it via a cutover, so the old `.migrated-<ts>`-only match
    missed it and it shipped raw in a "sanitised" backup download."""
    copy = AUTH_JSON.parent / f"{AUTH_JSON.name}.pre-reset-20260723-185559"
    assert is_secret(copy)
    assert is_non_restorable(copy)


def test_arbitrary_suffix_sibling_of_a_non_secret_file_stays_non_secret():
    """The generalized match must not over-match: a sibling of ordinary user data (real
    money, itself long since cutover-absorbed) stays non-secret whatever suffix it carries."""
    copy = ACCOUNTS_JSON.parent / f"{ACCOUNTS_JSON.name}.pre-reset-20260723-185559"
    assert not is_secret(copy)
    assert not is_non_restorable(copy)


def test_name_merely_prefixed_by_a_secret_name_is_not_a_sibling():
    """`auth.json2` is NOT `auth.json.<anything>` — the match requires the literal "."
    boundary right after the known name, or it would swallow any unrelated file that
    happens to share a prefix."""
    assert not is_secret(AUTH_JSON.parent / f"{AUTH_JSON.name}2")
