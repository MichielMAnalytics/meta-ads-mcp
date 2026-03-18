"""
OAuth 2.1 Metadata Endpoints for MCP Spec Compliance

Implements RFC 9728 (Protected Resource Metadata) and RFC 8414
(Authorization Server Metadata) so that MCP clients like Claude Code can
discover the Clerk OAuth authorization server and authenticate users.

Requires CLERK_PUBLISHABLE_KEY environment variable.
"""

import base64
import os
import re
from typing import Optional

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse

from .utils import logger


def _derive_clerk_fapi_url(publishable_key: str) -> str:
    """Derive the Clerk FAPI URL from the publishable key.

    Same logic as @clerk/mcp-tools:
      pk_live_BASE64... -> strip prefix -> base64 decode -> remove trailing $
    """
    key = re.sub(r"^pk_(test|live)_", "", publishable_key)
    decoded = base64.b64decode(key).decode("utf-8")
    return f"https://{decoded.rstrip('$')}"


def _get_clerk_publishable_key() -> Optional[str]:
    return os.environ.get("CLERK_PUBLISHABLE_KEY")


# ---------------------------------------------------------------------------
# Route handlers (Starlette)
# ---------------------------------------------------------------------------

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "*",
    "Access-Control-Max-Age": "86400",
}


async def protected_resource_metadata(request: Request) -> JSONResponse:
    """GET /.well-known/oauth-protected-resource

    RFC 9728 – tells MCP clients which authorization server to use.
    """
    pk = _get_clerk_publishable_key()
    if not pk:
        logger.error("CLERK_PUBLISHABLE_KEY not set – cannot serve OAuth metadata")
        return JSONResponse(
            {"error": "OAuth not configured"}, status_code=500, headers=CORS_HEADERS
        )

    fapi_url = _derive_clerk_fapi_url(pk)

    # Build the resource URL from the incoming request.
    # Behind a load balancer / reverse proxy, X-Forwarded-Proto is the real scheme.
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.hostname
    resource_url = f"{proto}://{host}/mcp"

    metadata = {
        "resource": resource_url,
        "authorization_servers": [fapi_url],
        "token_types_supported": ["urn:ietf:params:oauth:token-type:access_token"],
        "token_introspection_endpoint": f"{fapi_url}/oauth/token",
        "token_introspection_endpoint_auth_methods_supported": [
            "client_secret_post",
            "client_secret_basic",
        ],
        "jwks_uri": f"{fapi_url}/.well-known/jwks.json",
        "authorization_data_types_supported": ["oauth_scope"],
        "authorization_data_locations_supported": ["header", "body"],
        "key_challenges_supported": [
            {
                "challenge_type": "urn:ietf:params:oauth:pkce:code_challenge",
                "challenge_algs": ["S256"],
            }
        ],
        "service_documentation": "https://clerk.com/docs",
    }

    logger.debug("Serving protected resource metadata (auth server: %s)", fapi_url)
    return JSONResponse(metadata, headers=CORS_HEADERS)


async def auth_server_metadata(request: Request) -> JSONResponse:
    """GET /.well-known/oauth-authorization-server

    RFC 8414 – proxies Clerk's authorization server metadata so MCP
    clients can discover OAuth endpoints (authorize, token, register).
    """
    pk = _get_clerk_publishable_key()
    if not pk:
        logger.error("CLERK_PUBLISHABLE_KEY not set – cannot serve auth server metadata")
        return JSONResponse(
            {"error": "OAuth not configured"}, status_code=500, headers=CORS_HEADERS
        )

    fapi_url = _derive_clerk_fapi_url(pk)
    upstream = f"{fapi_url}/.well-known/oauth-authorization-server"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(upstream)
        metadata = resp.json()
        logger.debug("Proxied auth server metadata from %s", upstream)
        return JSONResponse(metadata, headers=CORS_HEADERS)
    except Exception as exc:
        logger.error("Failed to fetch auth server metadata from %s: %s", upstream, exc)
        return JSONResponse(
            {"error": "Failed to fetch authorization server metadata"},
            status_code=502,
            headers=CORS_HEADERS,
        )


async def register_client(request: Request) -> JSONResponse:
    """POST /register

    RFC 7591 – Dynamic Client Registration.
    Proxies the request to Clerk's /oauth/register endpoint.
    """
    pk = _get_clerk_publishable_key()
    if not pk:
        return JSONResponse({"error": "OAuth not configured"}, status_code=500)

    fapi_url = _derive_clerk_fapi_url(pk)
    clerk_register_url = f"{fapi_url}/oauth/register"

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                clerk_register_url,
                json=body,
                headers={"Content-Type": "application/json"},
            )
        data = resp.json()
        logger.debug("Proxied client registration to %s (status %s)", clerk_register_url, resp.status_code)
        return JSONResponse(data, status_code=resp.status_code, headers=CORS_HEADERS)
    except Exception as exc:
        logger.error("Failed to proxy client registration: %s", exc)
        return JSONResponse(
            {"error": "Failed to register OAuth client"}, status_code=502
        )


async def cors_preflight(request: Request) -> JSONResponse:
    """OPTIONS handler for metadata endpoints."""
    return JSONResponse(None, status_code=204, headers=CORS_HEADERS)


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------

def add_oauth_routes(app) -> None:
    """Add OAuth 2.1 discovery routes to a Starlette app.

    Call this after the app is created but before it starts serving.
    """
    from starlette.routing import Route

    oauth_routes = [
        Route("/.well-known/oauth-protected-resource", protected_resource_metadata, methods=["GET", "OPTIONS"]),
        Route("/.well-known/oauth-protected-resource/mcp", protected_resource_metadata, methods=["GET", "OPTIONS"]),
        Route("/mcp/.well-known/oauth-protected-resource", protected_resource_metadata, methods=["GET", "OPTIONS"]),
        Route("/.well-known/oauth-authorization-server", auth_server_metadata, methods=["GET", "OPTIONS"]),
        Route("/register", register_client, methods=["POST", "OPTIONS"]),
    ]

    # Prepend routes so they take priority over the catch-all MCP handler
    app.routes = oauth_routes + list(app.routes)
    logger.info("OAuth 2.1 discovery routes added (%d routes)", len(oauth_routes))
