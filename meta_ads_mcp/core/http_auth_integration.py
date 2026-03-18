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
# Starlette middleware
# ---------------------------------------------------------------------------

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class AuthInjectionMiddleware(BaseHTTPMiddleware):
    """Extract auth tokens from HTTP headers and store in ContextVars.

    Also validates the Bearer token against the Rule1 API and stores
    the org context for downstream use.
    """

    async def dispatch(self, request: Request, call_next):
        logger.debug("HTTP Auth Middleware: Processing request to %s", request.url.path)

        headers = dict(request.headers)

        # Extract Bearer token (Rule1 / Clerk JWT / pgs_xxx)
        bearer_token = FastMCPAuthIntegration.extract_token_from_headers(headers)

        # Extract direct Meta token (escape hatch)
        direct_meta = FastMCPAuthIntegration.extract_direct_meta_token_from_headers(headers)

        if bearer_token:
            logger.debug("HTTP Auth Middleware: Extracted Bearer token: %s...", bearer_token[:10])
            FastMCPAuthIntegration.set_auth_token(bearer_token)

            # Validate the Bearer token against Rule1 API
            try:
                org_ctx = await rule1_auth.validate_token(bearer_token)
                FastMCPAuthIntegration.set_org_context(org_ctx)
                logger.debug("HTTP Auth Middleware: Org context validated")
            except Exception as exc:
                logger.warning("HTTP Auth Middleware: Token validation failed: %s", exc)
                # We still allow the request through -- individual tools will
                # fail with a clear error if they need a valid token.

        if direct_meta:
            logger.debug("HTTP Auth Middleware: Extracted direct Meta token: %s...", direct_meta[:10])
            FastMCPAuthIntegration.set_direct_meta_token(direct_meta)

        if not bearer_token and not direct_meta:
            logger.warning("HTTP Auth Middleware: No authentication tokens found in headers")

        try:
            response = await call_next(request)
            return response
        finally:
            # Clear all context-scoped state
            if bearer_token:
                FastMCPAuthIntegration.clear_auth_token()
                FastMCPAuthIntegration.clear_org_context()
            if direct_meta:
                FastMCPAuthIntegration.clear_direct_meta_token()
            FastMCPAuthIntegration.clear_current_account_id()


def setup_starlette_middleware(app):
    """Add AuthInjectionMiddleware to the Starlette app if not already present."""
    if not app:
        logger.error("Cannot setup Starlette middleware, app is None.")
        return

    already_added = any(
        mw.cls == AuthInjectionMiddleware for mw in app.user_middleware
    )

    if not already_added:
        try:
            app.add_middleware(AuthInjectionMiddleware)
            logger.info("AuthInjectionMiddleware added to Starlette app successfully.")
        except Exception as e:
            logger.error(
                "Failed to add AuthInjectionMiddleware to Starlette app: %s", e, exc_info=True
            )
    else:
        logger.debug("AuthInjectionMiddleware already present in Starlette app.")
