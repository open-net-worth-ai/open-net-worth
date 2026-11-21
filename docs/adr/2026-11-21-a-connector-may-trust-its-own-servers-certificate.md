---
title: A connector may trust its own server's certificate, on top of the boot trust store
status: Accepted
date: 2026-11-21
deciders: repo owner + orchestrating agent (Claude)
---

# 2026-11-21. A connector may trust its own server's certificate, on top of the boot trust store

## Context

Outbound HTTPS trusts one store, decided once at boot (ADR
`2026-09-21-outbound-https-trust-store-is-one-boot-decision`), and nothing turns verification off.
A self-hosted Actual Budget server often runs on a self-signed certificate, or one from a private
authority (a home lab CA, a reverse proxy's own CA). Such a server could not be reached at all:
the only way round was setting `SSL_CERT_FILE` for the whole process, which widens trust for every
outbound call. No comparable Actual tool offers better than a library flag.

## Decision

The Actual connector takes an optional **Server certificate**: up to three PEM certificates (never
a private key), parsed by `core/server_certificate.py` with a pattern that can only match one way.
It is stored with the connector's write-only secrets, shown back only as SHA-256 fingerprints, and
dropped when the server's origin changes, like the password and the proxy headers.

At connect time it is added to a FRESH context from `https_context()` (`trusting()`), used for that
one client only. Verification and host-name checks stay on; the boot store still applies; nothing
shared changes. The Actual client talks only to the saved base address and follows no redirect, so
a pasted authority can vouch for nothing but the server the person named.

## Consequences

- A self-signed server works without touching the process trust store.
- Trusting an authority rather than a leaf is accepted: the context serves one host, whose name is
  still checked. The remaining risk is the person being talked into pasting an attacker's
  certificate, the same decision as accepting any self-signed certificate.
- Any other connector that wants this reuses `core/server_certificate.py` the same way: a fresh
  context per client, never the shared one.
