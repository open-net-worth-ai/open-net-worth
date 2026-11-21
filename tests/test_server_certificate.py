"""A certificate trusted for one Actual server: parsed strictly, shown by fingerprint, dropped with
the address, and added to the checks rather than replacing them. Every certificate is generated
here; nothing real."""

from __future__ import annotations

import datetime as dt
import ssl

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from backend.core import server_certificate
from backend.core.errors import ValidationError
from backend.db.actual_budget_config_store import ActualBudgetConfigStore


def _made(name: str = "actual.example.test") -> tuple[str, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    secret = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return pem, secret


def test_a_certificate_is_kept_as_clean_pem_and_shown_by_fingerprint():
    pem, _ = _made()
    kept = server_certificate.parse(f"\n  {pem}  \n")
    assert kept.startswith("-----BEGIN CERTIFICATE-----") and kept.endswith("-----\n")
    (fingerprint,) = server_certificate.fingerprints(kept)
    assert len(fingerprint.split(":")) == 32


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not a certificate",
        "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----",
    ],
)
def test_what_isnt_a_certificate_is_refused(text):
    with pytest.raises(ValidationError):
        server_certificate.parse(text)


def test_a_private_key_is_refused_even_beside_a_certificate():
    pem, secret = _made()
    with pytest.raises(ValidationError, match="never a private key"):
        server_certificate.parse(pem + secret)


def test_at_most_three():
    with pytest.raises(ValidationError, match="At most 3"):
        server_certificate.parse("".join(_made()[0] for _ in range(4)))


def test_trusting_adds_to_the_checks_and_turns_none_off():
    pem, _ = _made()
    context = server_certificate.trusting(ssl.create_default_context(), pem)
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname is True
    assert any(c.get("subject") for c in context.get_ca_certs())


def test_the_store_keeps_it_and_a_new_address_drops_it():
    store = ActualBudgetConfigStore()
    pem, _ = _made()
    store.update(
        {"server_url": "https://actual.example.test", "password": "pw", "server_certificate": pem}
    )
    assert store.public()["server_certificate_fingerprints"]
    assert store.credentials()["server_certificate"].startswith("-----BEGIN CERTIFICATE-----")
    store.update({"server_url": "https://other.example.test"})
    assert store.public()["server_certificate_fingerprints"] == []


def test_it_can_be_removed():
    store = ActualBudgetConfigStore()
    store.update(
        {
            "server_url": "https://actual.example.test",
            "password": "pw",
            "server_certificate": _made()[0],
        }
    )
    store.clear_server_certificate()
    assert store.credentials()["server_certificate"] == ""


def test_a_crafted_paste_is_refused_quickly():
    """A BEGIN line and a long run of whitespace with no END once took minutes to refuse."""
    import time

    started = time.perf_counter()
    with pytest.raises(ValidationError):
        server_certificate.parse("-----BEGIN CERTIFICATE-----" + " " * 19_000 + "A")
    assert time.perf_counter() - started < 1.0
