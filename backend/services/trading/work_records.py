"""Safe executor work and immutable workspace file versions, scoped to their deployment."""

import hashlib
import json
import re

from backend.core.errors import ConflictError, ValidationError
from backend.core.redact import redact_secrets
from backend.db.trading_store import TradingStore, trading_transaction
from backend.services.trading.common import assert_running, event, require_bound_run

_CREDENTIAL_SHAPES = re.compile(
    r"(?i:bearer)\s+[A-Za-z0-9._~+/=-]{8,}"
    r"|\bsk-[A-Za-z0-9_-]{16,}"
    r"|\bgh[pousr]_[A-Za-z0-9]{20,}"
    r"|\bgithub_pat_[A-Za-z0-9_]{20,}"
    r"|\bxox[abposr]-[A-Za-z0-9-]{10,}"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|\bAIza[A-Za-z0-9_-]{35}\b"
    r"|\bya29\.[A-Za-z0-9._~-]{16,}"
    r"|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r"|\b[0-9]{8,12}:[A-Za-z0-9_-]{30,}\b"
)
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    re.DOTALL,
)
_USAGE_COUNTERS = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "reasoning_tokens",
        "cached_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    }
)
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)(api[_-]?key|client_secret|access_token|refresh_token|password|passwd|authorization)"
    r"([\"']?\s*[:=]\s*[\"']?)([^\s\"',;&]{6,})"
)
_URL_USERINFO = re.compile(r"(https?://)[^\s/@]+(?::[^\s/@]*)?@", re.IGNORECASE)
_URL_CREDENTIAL_QUERY = re.compile(
    r"(?i)([?&](?:token|auth|key|signature|sig|access_token|api_key)=)[^&#\s\"']+"
)


def redact_work_text(value):
    """Credential-only masking preserves financial values, internal IDs and executable code."""
    value = _PRIVATE_KEY_BLOCK.sub("[redacted credential]", value)
    value = _CREDENTIAL_SHAPES.sub("[redacted credential]", value)
    value = _CREDENTIAL_ASSIGNMENT.sub(r"\1\2[redacted credential]", value)
    value = _URL_USERINFO.sub(r"\1[redacted credential]@", value)
    return _URL_CREDENTIAL_QUERY.sub(r"\1[redacted credential]", value)


def safe_work_payload(value):
    if isinstance(value, dict):
        return {
            key: item
            if key in _USAGE_COUNTERS and type(item) is int and item >= 0
            else safe_work_payload(redact_secrets({key: item})[key])
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [safe_work_payload(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return redact_work_text(value)
        if isinstance(parsed, (dict, list)):
            safe = safe_work_payload(parsed)
            return (
                redact_work_text(value) if safe == parsed else json.dumps(safe, ensure_ascii=False)
            )
        return redact_work_text(value)
    return value


def record_work(run_id, kind, payload, *, source="executor_reported"):
    if kind not in {
        "input",
        "output",
        "analysis",
        "reasoning",
        "tool_call",
        "tool_result",
        "error",
        "wait",
        "actor",
        "native_event",
    }:
        raise ValidationError("Unsupported executor work event kind")
    safe = safe_work_payload(payload)
    redacted = safe != payload
    encoded = json.dumps(safe, ensure_ascii=False, default=str)
    if len(encoded.encode()) > 4 * 1024 * 1024:
        raise ValidationError("A work record may contain at most 4 MiB; split larger records")
    with trading_transaction() as conn:
        require_bound_run(conn, run_id)
        run = TradingStore("runs").required(run_id, conn=conn)
        if len(encoded.encode()) > 262144:
            pieces = [encoded[index : index + 32000] for index in range(0, len(encoded), 32000)]
            records = [
                event(conn, run, "agent_record_part", {"content": piece})["id"] for piece in pieces
            ]
            safe = {"availability": "chunked", "record_ids": records, "format": "json"}
        return event(
            conn,
            run,
            f"agent_{kind}",
            {
                **safe,
                "source": source,
                "redacted": redacted,
            },
        )


def get_work_record(run_id, record_id):
    row = TradingStore("events").required(record_id)
    if row["run_id"] != run_id:
        raise ValidationError("Work record does not belong to this workspace deployment")
    payload = row["payload"]
    if payload.get("availability") != "chunked":
        return row
    parts = [TradingStore("events").required(key) for key in payload["record_ids"]]
    if any(part["run_id"] != run_id or part["kind"] != "agent_record_part" for part in parts):
        raise ValidationError("Invalid work record parts")
    return {
        **row,
        "payload": {
            **json.loads("".join(part["payload"]["content"] for part in parts)),
            "source": payload["source"],
            "redacted": payload["redacted"],
        },
    }


def write_workspace_file(run_id, name, content):
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./-]{0,159}", name) or any(
        part in {"", ".", ".."} for part in name.split("/")
    ):
        raise ValidationError("Use a relative workspace filename without traversal")
    if len(content.encode()) > 64000:
        raise ValidationError("Workspace file exceeds 64000 UTF-8 bytes")
    safe = safe_work_payload(content)
    with trading_transaction() as conn:
        require_bound_run(conn, run_id)
        run = TradingStore("runs").required(run_id, conn=conn)
        assert_running(run)
        versions = [
            e
            for e in TradingStore("events").list_scope(run_id, conn=conn)
            if e["kind"] == "workspace_file" and e["payload"].get("name") == name
        ]
        if versions and versions[-1]["payload"]["content"] == safe:
            return versions[-1]
        if len(versions) >= 200:
            raise ConflictError("Workspace file version limit reached")
        return event(
            conn,
            run,
            "workspace_file",
            {
                "name": name,
                "content": safe,
                "version": len(versions) + 1,
                "sha256": hashlib.sha256(safe.encode()).hexdigest(),
                "redacted": safe != content,
                "storage": "versioned_workspace_record",
            },
        )


def workspace_files(run_id, *, name=None, version=None):
    with trading_transaction() as conn:
        require_bound_run(conn, run_id)
        TradingStore("runs").required(run_id, conn=conn)
        rows = [
            e
            for e in TradingStore("events").list_scope(run_id, conn=conn)
            if e["kind"] == "workspace_file" and (name is None or e["payload"].get("name") == name)
        ]
    if version is not None:
        rows = [e for e in rows if e["payload"]["version"] == version]
    if name is None:
        latest = {e["payload"]["name"]: e for e in rows}
        return {
            "items": [
                {**e["payload"], "content": None, "event_id": e["id"]} for e in latest.values()
            ],
            "total": len(latest),
        }
    if not rows:
        raise ValidationError("Workspace file version not found")
    row = rows[-1]
    return {**row["payload"], "event_id": row["id"]}
