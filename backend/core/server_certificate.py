"""A server certificate the person chose to trust for one connection: a self-hosted server with a
self-signed certificate, or one from a private certificate authority. Only certificates (no private
key), at most a few, read as PEM. Trusting one adds it to the normal checks for that connection
only; the host name is still checked, and nothing ever turns verification off."""

from __future__ import annotations

import hashlib
import re
import ssl

from backend.core.errors import ValidationError

MAX_CERTIFICATES = 3
MAX_CHARS = 20_000
# One way to match only: the body's class excludes "-", so nothing can backtrack across a long
# run of whitespace (the earlier pattern took minutes on a crafted paste).
_BLOCK = re.compile(r"-----BEGIN CERTIFICATE-----([A-Za-z0-9+/=\s]*)-----END CERTIFICATE-----")


def parse(text: str) -> str:
    """The certificates in ``text`` as clean PEM; refused when there are none, too many, anything
    else in the text (such as a private key), or one that doesn't read as a certificate."""
    text = (text or "").strip()
    if len(text) > MAX_CHARS:
        raise ValidationError("The certificate is too long.")
    blocks = [b for b in _BLOCK.findall(text) if b.strip()]
    if not blocks:
        raise ValidationError(
            "Paste the certificate as text, from -----BEGIN CERTIFICATE----- to the END line."
        )
    if len(blocks) > MAX_CERTIFICATES:
        raise ValidationError(f"At most {MAX_CERTIFICATES} certificates can be trusted here.")
    if _BLOCK.sub("", text).strip():
        raise ValidationError("Paste certificates only: nothing else, and never a private key.")
    pem = "\n".join(
        f"-----BEGIN CERTIFICATE-----\n{''.join(b.split())}\n-----END CERTIFICATE-----"
        for b in blocks
    )
    try:
        ssl.create_default_context().load_verify_locations(cadata=pem)
    except (ssl.SSLError, ValueError) as exc:
        raise ValidationError("That doesn't read as a certificate.") from exc
    return pem + "\n"


def fingerprints(pem: str) -> list[str]:
    """Each certificate's SHA-256 fingerprint, as a server's own settings show it."""
    out = []
    for block in _BLOCK.findall(pem or ""):
        der = ssl.PEM_cert_to_DER_cert(
            f"-----BEGIN CERTIFICATE-----\n{''.join(block.split())}\n-----END CERTIFICATE-----\n"
        )
        digest = hashlib.sha256(der).hexdigest().upper()
        out.append(":".join(digest[i : i + 2] for i in range(0, len(digest), 2)))
    return out


def trusting(context: ssl.SSLContext, pem: str) -> ssl.SSLContext:
    """``context`` with ``pem`` added to what it trusts (nothing else about it changes)."""
    if pem:
        context.load_verify_locations(cadata=pem)
    return context
