"""Organization-related functionality for Rule1 API."""

import json
from typing import Optional
from .server import mcp_server
from .utils import logger


@mcp_server.tool()
async def list_my_organizations() -> str:
    """
    List all organizations the authenticated user has access to.

    Call this first to get your organization ID, which is required
    by most other tools (get_ad_accounts, get_campaigns, etc.).

    Returns a list of organizations with their IDs, names, and your role.
    """
    try:
        from .http_auth_integration import FastMCPAuthIntegration
        from . import rule1_auth

        # Check for Bearer token
        bearer = FastMCPAuthIntegration.get_auth_token()
        if not bearer:
            return json.dumps({
                "error": {
                    "message": "Not authenticated",
                    "details": "No Bearer token found. Ensure your MCP client sends an Authorization header.",
                }
            }, indent=2)

        # validate_token calls GET /api/mcp/organizations
        org_data = await rule1_auth.validate_token(bearer)

        # The Rule1 API returns organization data — extract the list
        organizations = []
        if isinstance(org_data, dict):
            # Could be { organizations: [...] } or { data: { organizations: [...] } }
            orgs = (
                org_data.get("organizations")
                or (org_data.get("data", {}) or {}).get("organizations")
                or []
            )
            if isinstance(orgs, list):
                organizations = [
                    {
                        "id": org.get("id", ""),
                        "name": org.get("name", ""),
                        "slug": org.get("slug"),
                        "role": org.get("role", ""),
                    }
                    for org in orgs
                ]

        return json.dumps({
            "organizations": organizations,
            "count": len(organizations),
            "hint": "Use an organization ID from this list as the organization_id parameter in other tools like get_ad_accounts.",
        }, indent=2)

    except Exception as e:
        logger.error("list_my_organizations error: %s", e)
        return json.dumps({
            "error": {
                "message": "Failed to list organizations",
                "details": str(e),
            }
        }, indent=2)
