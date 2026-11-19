"""Discovery documents (RFC 9728 protected resource, RFC 8414 authorization server) and the
``WWW-Authenticate`` challenge. Every URL is built from ``settings.public_url()``."""

from __future__ import annotations

from backend.services.mcp_oauth import settings
from backend.services.mcp_oauth.common import SCOPES

PRM_PATH = "/.well-known/oauth-protected-resource"


def protected_resource() -> dict:
    return {
        "resource": settings.resource_url(),
        "authorization_servers": [settings.public_url()],
        "scopes_supported": list(SCOPES),
        "bearer_methods_supported": ["header"],
        "resource_name": "Open Net Worth",
    }


def authorization_server() -> dict:
    base = settings.public_url()
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "revocation_endpoint": f"{base}/oauth/revoke",
        "response_types_supported": ["code"],
        "response_modes_supported": ["query"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": [
            "none",
            "client_secret_post",
            "client_secret_basic",
        ],
        "revocation_endpoint_auth_methods_supported": [
            "none",
            "client_secret_post",
            "client_secret_basic",
        ],
        "client_id_metadata_document_supported": True,
        "authorization_response_iss_parameter_supported": True,
        "scopes_supported": list(SCOPES),
    }


def www_authenticate() -> str:
    url = f"{settings.public_url()}{PRM_PATH}{settings.MCP_PATH}"
    return f'Bearer resource_metadata="{url}", scope="{" ".join(SCOPES)}"'
