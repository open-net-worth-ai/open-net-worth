"""Recognise a mail server refusing the sign-in, and say why it most often happens.

Gmail, Outlook, iCloud and Fastmail all refuse the account's normal password over IMAP/SMTP;
they need an app password. A user who typed their normal password sees only the server's
terse refusal ("Invalid credentials", "535 5.7.8 ..."), so every email error path routes its
text through ``explain_mail_error`` — the form's "Last error", Lucius's tool result and the
test endpoints all carry the same explanation. Leaf module: stdlib only.
"""

from __future__ import annotations

import re

_AUTH_FAILURE = re.compile(
    r"authenticationfailed|authentication failed|authenticate failed|invalid credentials"
    r"|login failed|logon failure|username and password not accepted|badcredentials"
    r"|incorrect username|\b535\b",
    re.IGNORECASE,
)

APP_PASSWORD_HINT = (
    "Your email provider refused the sign-in. This usually means your normal email password "
    "was entered instead of an app password — Gmail, Outlook, iCloud and Fastmail all refuse "
    "the normal password here. Create an app password (Connectors → Email shows the steps and "
    "links for your provider) and paste it into App password."
)


def is_mail_auth_failure(text: str | None) -> bool:
    return bool(text) and _AUTH_FAILURE.search(text) is not None


def explain_mail_error(text: str) -> str:
    """``text`` unchanged, or — for a sign-in refusal — the server's words plus the hint."""
    if not is_mail_auth_failure(text) or APP_PASSWORD_HINT in text:
        return text
    return f"{text.rstrip('. ')}. {APP_PASSWORD_HINT}"
