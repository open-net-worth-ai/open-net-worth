"""Email-out notification channel (generic, vendor-neutral push).

Registers an async ``_send`` on the ``core.channels`` seam so ``services.notify`` can
fan a message out by **email** WITHOUT ``services`` importing ``connectors`` — the same
inversion the Telegram channel uses (``connectors → core`` is a downward edge, so
import-linter Contract 1/4 stays green). The send uses stdlib ``smtplib`` + ``email``
(no new dependency).

Config lives on the Email connector (``EmailConfigStore.smtp_credentials()``, ``db/``): the
SMTP server plus the connector's ONE address as login, From and alert recipient, and its
single password. The channel is registered only when the connector is **enabled AND
configured**, so an unconfigured or paused instance simply has no email channel.

Error isolation: the blocking SMTP call runs in a thread and any failure is **caught**
and returned as ``{"error": ...}`` — a channel ``_send`` must never raise into the
Notifier (which also isolates per-channel, but we don't rely on that). Secrets are never
logged.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from backend.core.channels import register_channel
from backend.core.mail_auth import explain_mail_error

logger = logging.getLogger(__name__)

CHANNEL_NAME = "email"


def _smtp_send(
    cfg: dict,
    *,
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
) -> dict:
    """Blocking SMTP send of one plaintext email to an arbitrary recipient (run in a thread).

    Shared by the notification channel (``_send_sync``, fixed recipient/subject) AND the
    ``send_email`` Lucius tool (arbitrary ``to``/``subject``/``cc``). Uses the connector's
    SMTP credentials in ``cfg`` (``EmailConfigStore.smtp_credentials()``). Returns ``{sent: 1}``.
    """
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["from_addr"]
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    # multipart/alternative: plain text (verbatim markdown — the text-client fallback) + an HTML
    # part rendered from the same markdown, so a modern client shows real bold/links/code instead
    # of literal `**` and raw URLs. Rendering never fails the send (a formatter error → plain only).
    msg.set_content(body)
    try:
        from backend.connectors.msgfmt import render

        msg.add_alternative(render(body, "email"), subtype="html")
    except Exception:  # noqa: BLE001 — HTML is a nicety; never lose the email over it
        pass

    host = cfg["host"]
    port = int(cfg.get("port") or 587)
    user = (cfg.get("user") or "").strip()
    password = cfg.get("password") or ""
    use_tls = bool(cfg.get("use_tls", True))

    # Port 465 → implicit TLS (SMTP_SSL); otherwise plain connect + optional STARTTLS.
    with (
        smtplib.SMTP_SSL(host, port, timeout=15)
        if port == 465
        else smtplib.SMTP(host, port, timeout=15)
    ) as server:
        if use_tls and port != 465:
            server.starttls()
        if user and password:
            server.login(user, password)
        server.send_message(msg)
    return {"sent": 1}


def _send_sync(cfg: dict, message: str) -> dict:
    """Blocking notification send (run in a thread by ``_send``). A notification is a short
    body with a fixed subject + the verbatim message body to the configured recipient
    (no money math, no formatting — mirrors the in-app / Telegram channels)."""
    return _smtp_send(
        cfg,
        to=cfg["to_addr"],
        subject="Open Net Worth notification",
        body=message,
    )


async def _send(message: str) -> dict:
    """Send ``message`` as an email to the configured recipient. Returns ``{sent: 1}``.

    Best-effort: on a config / SMTP error this returns ``{"error": "..."}`` rather than
    raising, so the Notifier's other channels still deliver. Secrets are never logged.
    """
    import asyncio

    from backend.db import EmailConfigStore

    # Gate the send on *configured* (has the fields it needs), not *enabled* — so the
    # test-send endpoint can verify a saved-but-paused connector. Registration on the
    # seam is still gated on enabled+configured via register_if_active().
    cfg = EmailConfigStore().smtp_credentials(require_enabled=False)
    if cfg is None:
        return {"error": "email channel not configured"}
    try:
        return await asyncio.to_thread(_send_sync, cfg, message)
    except Exception as exc:  # never propagate — log without the secret
        logger.warning("email-out channel send failed: %s", exc)
        return {"error": explain_mail_error(str(exc))}


def send_email(to: str, subject: str, body: str, cc: str | None = None) -> dict:
    """Send one plaintext email to an arbitrary recipient via the email connector's SMTP
    config (``EmailConfigStore.smtp_credentials()``). The SEND block behind the ``send_email`` Lucius
    tool — distinct from the notification channel (which posts a fixed-recipient alert).

    Best-effort: returns ``{ok: True, sent: 1}`` on success, ``{ok: False, error: ...}`` on a
    config / disabled / SMTP failure (never raises into the caller). Secrets are never logged.

    Gated on BOTH *configured* (the SMTP fields are present) AND *enabled* (the connector is
    not Paused). ``tool_permissions`` decides whether Lucius is ALLOWED to attempt this call
    at all; this is the tool's own underlying "can it actually run" contract underneath that,
    same as every other connector's sync tool reporting its own disabled/unconfigured state —
    without it, Pausing Email would stop the *interactive* forms of sending (the notify
    channel unregisters via ``register_if_active``) while leaving this on-demand path live,
    so an already-approved/`always_allow` ``send_email`` could still deliver mail through a
    connector the user believed was fully off. Unlike ``_send()``'s notify path (which is
    deliberately configured-only, so the ``/test`` endpoint can probe a not-yet-enabled
    channel) this function is reachable directly by Lucius at any time, so it cannot rely on
    "only registered when active" to make enabled implicit.
    """
    from backend.db import EmailConfigStore

    store = EmailConfigStore()
    cfg = store.smtp_credentials(require_enabled=False)
    if cfg is None:
        return {"ok": False, "error": "email send (SMTP) is not configured"}
    if not store.get()["enabled"]:
        return {
            "ok": False,
            "error": (
                "the Email connector is paused — resume it in Connectors before sending mail"
            ),
        }
    to = (to or "").strip()
    if not to:
        return {"ok": False, "error": "recipient (to) is required"}
    try:
        _smtp_send(cfg, to=to, subject=subject or "(no subject)", body=body or "", cc=cc)
        return {"ok": True, "sent": 1, "to": to}
    except Exception as exc:  # never propagate — log without the secret
        logger.warning("send_email failed: %s", exc)
        return {"ok": False, "error": explain_mail_error(str(exc))}


def is_active() -> bool:
    """Whether alerts can actually be delivered by email: the connector is enabled and its SMTP
    side is complete. The ONE predicate for both registering the channel and reporting it as
    available — an IMAP-only account is "set up" but cannot send an alert."""
    from backend.db import EmailConfigStore

    return EmailConfigStore().smtp_credentials() is not None


def register_if_active() -> bool:
    """Register the email channel on the seam iff it is enabled + configured.

    Called from ``main.py``'s lifespan (and by ``services.connectors`` after the Email
    connector changes) so the Notifier reaches email only when the user has set it up.
    Returns whether it was registered. Idempotent — last registration under ``"email"`` wins.
    """
    if is_active():
        register_channel(CHANNEL_NAME, _send)
        return True
    return False
