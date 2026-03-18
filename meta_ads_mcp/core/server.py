"""MCP server configuration for Meta Ads API."""

from mcp.server.fastmcp import FastMCP
import argparse
import os
import sys
from typing import Dict, Any, Optional
from .resources import list_resources, get_resource
from .utils import logger

# Initialize FastMCP server
mcp_server = FastMCP("meta-ads")

# Register resource URIs
mcp_server.resource(uri="meta-ads://resources")(list_resources)
mcp_server.resource(uri="meta-ads://images/{resource_id}")(get_resource)


class StreamableHTTPHandler:
    """Handles stateless Streamable HTTP requests for Meta Ads MCP"""

    def __init__(self):
        logger.debug("StreamableHTTPHandler initialized for stateless operation")

    def handle_request(
        self, request_headers: Dict[str, str], request_body: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle individual request with authentication."""
        try:
            auth_config = self.get_auth_config_from_headers(request_headers)
            logger.debug("Auth method detected: %s", auth_config["auth_method"])

            if auth_config["auth_method"] == "bearer":
                return self.handle_bearer_request(auth_config, request_body)
            else:
                return self.handle_unauthenticated_request(request_body)

        except Exception as e:
            logger.error("Error handling request: %s", e)
            return {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32603,
                    "message": "Internal error",
                    "data": str(e),
                },
                "id": request_body.get("id"),
            }

    def get_auth_config_from_headers(
        self, request_headers: Dict[str, str]
    ) -> Dict[str, Any]:
        """Extract authentication configuration from HTTP headers."""
        # Check for Bearer token in Authorization header
        auth_header = request_headers.get("Authorization") or request_headers.get(
            "authorization"
        )
        if auth_header and auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
            logger.info("Bearer authentication detected")
            return {
                "auth_method": "bearer",
                "bearer_token": token,
            }

        # Check for direct Meta token
        meta_token = request_headers.get("X-META-ACCESS-TOKEN") or request_headers.get(
            "x-meta-access-token"
        )
        if meta_token:
            logger.info("Direct Meta access token detected")
            return {
                "auth_method": "bearer",
                "bearer_token": meta_token,
            }

        logger.warning("No authentication method detected in headers")
        return {"auth_method": "none"}

    def handle_bearer_request(
        self, auth_config: Dict[str, Any], request_body: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle request with Bearer token."""
        logger.debug("Processing Bearer authenticated request")
        return {
            "jsonrpc": "2.0",
            "result": {
                "status": "ready",
                "auth_method": "bearer",
                "message": "Authentication successful with Bearer token",
                "token_source": "bearer_header",
            },
            "id": request_body.get("id"),
        }

    def handle_unauthenticated_request(
        self, request_body: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle request with no authentication."""
        logger.warning("Unauthenticated request received")
        return {
            "jsonrpc": "2.0",
            "error": {
                "code": -32600,
                "message": "Authentication required",
                "data": {
                    "supported_methods": [
                        "Authorization: Bearer <token> (Clerk JWT or pgs_xxx system token)",
                        "X-META-ACCESS-TOKEN: <direct_meta_token> (escape hatch)",
                    ],
                },
            },
            "id": request_body.get("id"),
        }


def main():
    """Main entry point for the package."""
    logger.info("Meta Ads MCP server starting")
    logger.debug("Python version: %s", sys.version)
    logger.debug("Args: %s", sys.argv)

    parser = argparse.ArgumentParser(
        description="Meta Ads MCP Server - Model Context Protocol server for Meta Ads API",
    )
    parser.add_argument(
        "--version", action="store_true", help="Show the version of the package"
    )
    parser.add_argument(
        "--transport",
        type=str,
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="Transport method: 'stdio' (default) or 'streamable-http'",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port for Streamable HTTP transport (default: 8080)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="localhost",
        help="Host for Streamable HTTP transport (default: localhost)",
    )
    parser.add_argument(
        "--sse-response",
        action="store_true",
        help="Use SSE response format instead of JSON (only with streamable-http)",
    )

    args = parser.parse_args()
    logger.debug(
        "Parsed args: version=%s, transport=%s, port=%s, host=%s, sse_response=%s",
        args.version,
        args.transport,
        args.port,
        args.host,
        args.sse_response,
    )

    # Validate CLI argument combinations
    if args.transport == "stdio" and (
        args.port != 8080 or args.host != "localhost" or args.sse_response
    ):
        logger.warning(
            "HTTP transport arguments (--port, --host, --sse-response) are ignored with stdio"
        )
        print("Warning: HTTP transport arguments are ignored when using stdio transport")

    # Show version if requested
    if args.version:
        from meta_ads_mcp import __version__

        logger.info("Displaying version: %s", __version__)
        print(f"Meta Ads MCP v{__version__}")
        return 0

    # Transport-specific server initialization
    if args.transport == "streamable-http":
        logger.info(
            "Starting MCP server with Streamable HTTP transport on %s:%s",
            args.host,
            args.port,
        )
        logger.info("Response format: %s", "SSE" if args.sse_response else "JSON")
        logger.info("Auth: Rule1 Bearer token (Authorization header)")

        print("Starting Meta Ads MCP server with Streamable HTTP transport")
        print(f"Server will listen on {args.host}:{args.port}")
        print(f"Response format: {'SSE' if args.sse_response else 'JSON'}")
        print("Authentication: Bearer token via Authorization header")

        # Configure the server
        mcp_server.settings.host = args.host
        mcp_server.settings.port = args.port
        mcp_server.settings.stateless_http = True
        mcp_server.settings.json_response = not args.sse_response

        # Import all tool modules to ensure they are registered
        logger.info("Ensuring all tools are registered for HTTP transport")
        from . import accounts, campaigns, adsets, ads, insights, authentication
        from . import ads_library, budget_schedules, reports, openai_deep_research

        # Setup HTTP authentication middleware (Rule1)
        logger.info("Setting up HTTP authentication middleware (Rule1)")
        try:
            from .http_auth_integration import setup_fastmcp_http_auth

            setup_fastmcp_http_auth(mcp_server)
            logger.info("FastMCP HTTP authentication integration setup successful")
            print("HTTP authentication integration enabled (Rule1)")
            print("  - Bearer tokens via Authorization: Bearer <token> header")
            print("  - Direct Meta tokens via X-META-ACCESS-TOKEN header (fallback)")

        except Exception as e:
            logger.error(
                "Failed to setup FastMCP HTTP authentication integration: %s", e
            )
            print(f"WARNING: HTTP authentication integration setup failed: {e}")
            print("  Server will still start but may not support header-based auth")

        # Log final server configuration
        logger.info("FastMCP server configured with:")
        logger.info("  - Host: %s", mcp_server.settings.host)
        logger.info("  - Port: %s", mcp_server.settings.port)
        logger.info("  - Stateless HTTP: %s", mcp_server.settings.stateless_http)
        logger.info("  - JSON Response: %s", mcp_server.settings.json_response)

        # Start the server
        try:
            logger.info("Starting FastMCP server with Streamable HTTP transport")
            print("Server configured successfully")
            print(
                f"  URL: http://{args.host}:{args.port}{mcp_server.settings.streamable_http_path}/"
            )
            print(
                f"  Mode: {'Stateless' if mcp_server.settings.stateless_http else 'Stateful'}"
            )
            print(
                f"  Format: {'JSON' if mcp_server.settings.json_response else 'SSE'}"
            )
            mcp_server.run(transport="streamable-http")
        except Exception as e:
            logger.error("Error starting Streamable HTTP server: %s", e)
            print(f"Error: Failed to start Streamable HTTP server: {e}")
            return 1
    else:
        # Default stdio transport
        logger.info("Starting MCP server with stdio transport")
        mcp_server.run(transport="stdio")
