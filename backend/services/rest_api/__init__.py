"""The generic REST connector: any HTTP API the owner points it at.

  * ``apis``    — the configured APIs: validation, the operator's view and Lucius's view.
  * ``openapi`` — turn an OpenAPI / Swagger document into a draft API (optional at setup).
  * ``client``  — send ONE request to a configured API: host pinned, credential added here.

ADR ``2026-11-10-generic-rest-connector-is-pinned-to-the-owners-host``.
"""
