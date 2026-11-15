"""The inbound MCP OAuth server (flag ``mcp_oauth``): the app as its own authorization server for
``<PUBLIC_URL>/mcp``, so hosted apps (claude.ai, ChatGPT) can connect without the static API
token. Design: ``.claude/rfcs/remote-mcp-oauth.md``; decision: ADR
``2026-11-16-hosted-ai-apps-connect-to-mcp-through-the-apps-own-oauth-server``.

Modules: ``settings`` (availability, the public address), ``clients`` (CIMD allowlist + owner-made
credentials), ``authorize`` (authorize + consent), ``tokens`` (token, refresh, revoke),
``grants`` (connected apps, recognising a token at /mcp), ``metadata`` (discovery documents).
"""
