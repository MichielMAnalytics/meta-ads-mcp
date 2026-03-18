"""
FastMCP HTTP Authentication Integration for Meta Ads MCP

This module provides direct integration with FastMCP to inject authentication
from HTTP headers into the tool execution context.

Auth flow (Rule1):
1. Extract Bearer token (Clerk JWT or pgs_xxx) from Authorization header
2. Validate it against the Rule1 API, store org context
3. When a tool needs a Meta token, fetch it via Rule1 API using the
   Bearer token + account_id
"""

import contextvars
from typing import Optional
from .utils import logger
from . import rule1_auth

# ---------------------------------------------------------------------------
# Context variables
# ---------------------------------------------------------------------------

# The Rule1 Bearer token (Clerk JWT or pgs_xxx system token)
_auth_token: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "auth_token", default=None
)

# Direct Meta access token passed via X-META-ACCESS-TOKEN header (escape hatch)
_direct_meta_token: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "direct_meta_token", default=None
)

# Validated org context from Rule1 API
_org_context: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "org_context", default=None
)

# The account_id currently being operated on (set by meta_api_tool decorator)
_current_account_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_account_id", default=None
)


class FastMCPAuthIntegration:
    """Direct integration with FastMCP for HTTP authentication"""

    # -- Bearer / Rule1 token ------------------------------------------------

    @staticmethod
    def set_auth_token(token: str) -> None:
        """Set the Rule1 Bearer token for the current context."""
        _auth_token.set(token)

    @staticmethod
    def get_auth_token() -> Optional[str]:
        """Get the Rule1 Bearer token for the current context."""
        return _auth_token.get(None)

    @staticmethod
    def clear_auth_token() -> None:
        """Clear the Rule1 Bearer token for the current context."""
        _auth_token.set(None)

    # -- Direct Meta token (X-META-ACCESS-TOKEN header) ----------------------

    @staticmethod
    def set_direct_meta_token(token: str) -> None:
        """Set a direct Meta access token for the current context."""
        _direct_meta_token.set(token)

    @staticmethod
    def get_direct_meta_token() -> Optional[str]:
        """Get the direct Meta access token for the current context."""
        return _direct_meta_token.get(None)

    @staticmethod
    def clear_direct_meta_token() -> None:
        """Clear the direct Meta access token for the current context."""
        _direct_meta_token.set(None)

    # -- Org context ---------------------------------------------------------

    @staticmethod
    def set_org_context(ctx: dict) -> None:
        _org_context.set(ctx)

    @staticmethod
    def get_org_context() -> Optional[dict]:
        return _org_context.get(None)

    @staticmethod
    def clear_org_context() -> None:
        _org_context.set(None)

    # -- Current account id --------------------------------------------------

    @staticmethod
    def set_current_account_id(account_id: str) -> None:
        _current_account_id.set(account_id)

    @staticmethod
    def get_current_account_id() -> Optional[str]:
        return _current_account_id.get(None)

    @staticmethod
    def clear_current_account_id() -> None:
        _current_account_id.set(None)

    # -- Header extraction ---------------------------------------------------

    @staticmethod
    def extract_token_from_headers(headers: dict) -> Optional[str]:
        """Extract Bearer token from Authorization header.

        Returns the Bearer token if present, None otherwise.
        """
        auth_header = headers.get("Authorization") or headers.get("authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
            logger.debug("Found Bearer token in Authorization header")
            return token
        return None

    @staticmethod
    def extract_direct_meta_token_from_headers(headers: dict) -> Optional[str]:
        """Extract direct Meta access token from X-META-ACCESS-TOKEN header."""
        meta_token = (
            headers.get("X-META-ACCESS-TOKEN") or headers.get("x-meta-access-token")
        )
        if meta_token:
            logger.debug("Found direct Meta token in X-META-ACCESS-TOKEN header")
        return meta_token


# ---------------------------------------------------------------------------
# FastMCP server patching
# ---------------------------------------------------------------------------

def patch_fastmcp_server(mcp_server):
    """Patch FastMCP server to inject authentication from HTTP headers."""
    logger.info("Patching FastMCP server for HTTP authentication")

    original_run = mcp_server.run

    def patched_run(transport="stdio", **kwargs):
        logger.debug("Starting FastMCP with transport: %s", transport)
        if transport == "streamable-http":
            logger.debug("Setting up HTTP authentication for streamable-http transport")
            setup_http_auth_patching()
        return original_run(transport=transport, **kwargs)

    mcp_server.run = patched_run
    logger.info("FastMCP server patching complete")


def setup_http_auth_patching():
    """Patch ``get_current_access_token`` so it resolves tokens via Rule1."""
    logger.info("Setting up HTTP authentication patching (Rule1)")

    from . import auth
    from . import api
    from . import authentication

    original_get_current_access_token = auth.get_current_access_token

    async def get_current_access_token_with_http_support() -> Optional[str]:
        """Enhanced get_current_access_token that checks HTTP context first."""

        # 1. Direct Meta token (X-META-ACCESS-TOKEN header) -- highest priority
        direct_token = FastMCPAuthIntegration.get_direct_meta_token()
        if direct_token:
            logger.debug("Using direct Meta token from X-META-ACCESS-TOKEN header")
            return direct_token

        # 2. Rule1 Bearer token -> fetch Meta token for the current account
        bearer = FastMCPAuthIntegration.get_auth_token()
        if bearer:
            account_id = FastMCPAuthIntegration.get_current_account_id()
            if account_id:
                try:
                    meta_token = await rule1_auth.get_meta_token(bearer, account_id)
                    return meta_token
                except Exception as exc:
                    logger.error("Failed to get Meta token via Rule1: %s", exc)
                    return None
            else:
                logger.warning(
                    "Bearer token present but no account_id set -- "
                    "cannot resolve Meta token"
                )
                return None

        # 3. Fall back to original implementation (env var, etc.)
        return await original_get_current_access_token()

    # Replace the function in all modules that imported it
    auth.get_current_access_token = get_current_access_token_with_http_support
    api.get_current_access_token = get_current_access_token_with_http_support
    authentication.get_current_access_token = get_current_access_token_with_http_support

    logger.info("Auth system patching complete - Rule1 integration active")


# Global instance for easy access
fastmcp_auth = FastMCPAuthIntegration()


def setup_fastmcp_http_auth(mcp_server):
    """Setup HTTP authentication integration with FastMCP."""
    logger.info("Setting up FastMCP HTTP authentication integration (Rule1)")

    # 1. Patch FastMCP's run method
    patch_fastmcp_server(mcp_server)

    # 2. Patch the methods that provide the Starlette app instance
    app_provider_methods = []
    if mcp_server.settings.json_response:
        if hasattr(mcp_server, "streamable_http_app") and callable(
            mcp_server.streamable_http_app
        ):
            app_provider_methods.append("streamable_http_app")
        else:
            logger.warning(
                "mcp_server.streamable_http_app not found, cannot patch for JSON responses."
            )
    else:
        if hasattr(mcp_server, "sse_app") and callable(mcp_server.sse_app):
            app_provider_methods.append("sse_app")
        else:
            logger.warning(
                "mcp_server.sse_app not found, cannot patch for SSE responses."
            )

    if not app_provider_methods:
        logger.error(
            "No suitable app provider method found on mcp_server. "
            "Cannot add HTTP Auth middleware."
        )

    for method_name in app_provider_methods:
        original_app_provider_method = getattr(mcp_server, method_name)

        def new_patched_app_provider_method(*args, _orig=original_app_provider_method, _name=method_name, **kwargs):
            app = _orig(*args, **kwargs)
            if app:
                logger.debug(
                    "Original %s returned app: %s. Adding AuthInjectionMiddleware.",
                    _name,
                    type(app),
                )
                setup_starlette_middleware(app)
            else:
                logger.error("Original %s returned None.", _name)
            return app

        setattr(mcp_server, method_name, new_patched_app_provider_method)
        logger.debug("Patched mcp_server.%s to inject AuthInjectionMiddleware.", method_name)

    logger.info("FastMCP HTTP authentication integration setup complete.")


# ---------------------------------------------------------------------------
# Raw ASGI middleware
# ---------------------------------------------------------------------------
#
# IMPORTANT: We use a raw ASGI middleware instead of Starlette's
# BaseHTTPMiddleware because the latter runs ``call_next`` in a separate
# thread/task, which breaks ContextVar propagation. With a raw ASGI
# middleware the downstream app runs in the SAME task, so ContextVars
# set here are visible inside FastMCP tool handlers.
# ---------------------------------------------------------------------------

from starlette.requests import Request
from starlette.responses import Response, JSONResponse

# Paths handled directly (OAuth 2.1 discovery)
_OAUTH_PATHS = {
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-protected-resource/mcp",
    "/mcp/.well-known/oauth-protected-resource",
    "/.well-known/oauth-authorization-server",
    "/.well-known/oauth-authorization-server/mcp",
    "/.well-known/openid-configuration",
    "/.well-known/openid-configuration/mcp",
    "/mcp/.well-known/openid-configuration",
}


class AuthInjectionMiddleware:
    """Raw ASGI middleware that extracts auth tokens from HTTP headers,
    stores them in ContextVars, and handles OAuth discovery endpoints.

    Unlike Starlette's BaseHTTPMiddleware this does NOT create a
    separate task for the downstream app, so ContextVars propagate
    correctly into FastMCP tool handlers.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Build a Starlette Request for convenience (read-only, no cost)
        request = Request(scope, receive)
        path = request.url.path.rstrip("/")
        method = request.method

        # --- OAuth discovery endpoints (handled directly) ----------------
        if path in _OAUTH_PATHS:
            from .oauth_metadata import (
                protected_resource_metadata,
                auth_server_metadata,
                cors_preflight,
            )
            if method == "OPTIONS":
                resp = await cors_preflight(request)
            elif "oauth-protected-resource" in path:
                resp = await protected_resource_metadata(request)
            else:
                resp = await auth_server_metadata(request)
            await resp(scope, receive, send)
            return

        if path == "/register":
            from .oauth_metadata import register_client, cors_preflight
            if method == "OPTIONS":
                resp = await cors_preflight(request)
            else:
                resp = await register_client(request)
            await resp(scope, receive, send)
            return

        # --- Extract auth tokens from headers ----------------------------
        headers = {
            k.decode("latin-1"): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }

        bearer_token = FastMCPAuthIntegration.extract_token_from_headers(headers)
        direct_meta = FastMCPAuthIntegration.extract_direct_meta_token_from_headers(headers)

        if bearer_token:
            logger.debug("ASGI Auth: Bearer token: %s...", bearer_token[:10])
            FastMCPAuthIntegration.set_auth_token(bearer_token)
            try:
                org_ctx = await rule1_auth.validate_token(bearer_token)
                FastMCPAuthIntegration.set_org_context(org_ctx)
            except Exception as exc:
                logger.warning("ASGI Auth: Token validation failed: %s", exc)

        if direct_meta:
            logger.debug("ASGI Auth: Direct Meta token: %s...", direct_meta[:10])
            FastMCPAuthIntegration.set_direct_meta_token(direct_meta)

        # --- 401 challenge for unauthenticated POST /mcp -----------------
        if not bearer_token and not direct_meta:
            if path in ("/mcp", "/mcp/") and method == "POST":
                proto = headers.get("x-forwarded-proto", request.url.scheme)
                host = (
                    headers.get("x-forwarded-host")
                    or headers.get("host")
                    or request.url.hostname
                )
                resource_metadata_url = (
                    f"{proto}://{host}/.well-known/oauth-protected-resource"
                )
                resp = Response(
                    content="Authentication required",
                    status_code=401,
                    headers={
                        "WWW-Authenticate": f'Bearer resource_metadata="{resource_metadata_url}"',
                        "Access-Control-Allow-Origin": "*",
                        "Access-Control-Expose-Headers": "WWW-Authenticate",
                    },
                )
                await resp(scope, receive, send)
                return

        # --- Call downstream (same task → ContextVars propagate) ---------
        try:
            await self.app(scope, receive, send)
        finally:
            if bearer_token:
                FastMCPAuthIntegration.clear_auth_token()
                FastMCPAuthIntegration.clear_org_context()
            if direct_meta:
                FastMCPAuthIntegration.clear_direct_meta_token()
            FastMCPAuthIntegration.clear_current_account_id()


def setup_starlette_middleware(app):
    """Wrap the Starlette app with AuthInjectionMiddleware."""
    if not app:
        logger.error("Cannot setup middleware, app is None.")
        return

    # Use add_middleware with the raw ASGI class. Starlette accepts any
    # callable(app) -> ASGI-app via add_middleware, not just
    # BaseHTTPMiddleware subclasses.
    try:
        app.add_middleware(AuthInjectionMiddleware)
        logger.info("AuthInjectionMiddleware (raw ASGI) installed successfully.")
    except Exception as e:
        logger.error("Failed to add AuthInjectionMiddleware: %s", e, exc_info=True)
