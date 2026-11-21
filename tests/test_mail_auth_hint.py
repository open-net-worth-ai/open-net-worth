"""A mail sign-in refusal tells the user the likely cause: the normal password, not an app password."""

import imaplib

import pytest

from backend.core.mail_auth import APP_PASSWORD_HINT, explain_mail_error, is_mail_auth_failure
from backend.ingest import imap_client


@pytest.mark.parametrize(
    "server_text",
    [
        "[AUTHENTICATIONFAILED] Invalid credentials (Failure)",  # Gmail IMAP
        "LOGIN failed.",  # Outlook IMAP
        "AUTHENTICATE failed.",
        "(535, b'5.7.8 Username and Password not accepted. BadCredentials')",  # Gmail SMTP
        "Incorrect username, password or access token.",
    ],
)
def test_a_refused_sign_in_carries_the_app_password_hint(server_text):
    explained = explain_mail_error(server_text)
    assert is_mail_auth_failure(server_text)
    assert explained.startswith(server_text.rstrip(". "))
    assert APP_PASSWORD_HINT in explained


def test_other_errors_are_left_alone_and_the_hint_is_never_doubled():
    assert explain_mail_error("timed out") == "timed out"
    once = explain_mail_error("LOGIN failed.")
    assert explain_mail_error(once) == once


def test_test_connection_decodes_the_server_bytes_and_explains(monkeypatch):
    class _Conn:
        def __init__(self, *a, **k):
            pass

        def login(self, user, password):
            raise imaplib.IMAP4.error(b"[AUTHENTICATIONFAILED] Invalid credentials (Failure)")

        def logout(self):
            pass

    monkeypatch.setattr(imap_client.imaplib, "IMAP4_SSL", _Conn)
    ok, err = imap_client.test_connection(
        {"host": "imap.example.com", "port": 993, "user": "a@example.com", "password": "x"}
    )
    assert not ok
    assert err.startswith("[AUTHENTICATIONFAILED] Invalid credentials")
    assert "b'" not in err
    assert APP_PASSWORD_HINT in err
