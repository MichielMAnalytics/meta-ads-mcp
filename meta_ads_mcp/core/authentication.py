"""Authentication-specific functionality for Meta Ads API.

The Meta Ads MCP server authenticates via Rule1:

1. **HTTP / Streamable HTTP Mode**
   - Users send a Bearer token (Clerk JWT or pgs_xxx system token)
     in the ``Authorization`` header.
   - The middleware validates the token against the Rule1 API.
   - Meta access tokens are fetched per-account from Rule1.

2. **Direct Meta Token (escape hatch)**
   - Users may send a raw Meta access token via ``X-META-ACCESS-TOKEN`` header.
"""

import json
from typing import Optional
import os
from .api import meta_api_tool
from . import auth
from .server import mcp_server
from .utils import logger

# Only register the login link tool if not explicitly disabled
ENABLE_LOGIN_LINK = not bool(os.environ.get("META_ADS_DISABLE_LOGIN_LINK", ""))


async def get_login_link(access_token: Optional[str] = None) -> str:
    """
    Get authentication status and instructions for Meta Ads authentication.

    This tool checks the current authentication status. When using Rule1
    authentication (recommended), ensure the Bearer token is sent via the
    Authorization header.

    Args:
        access_token: Meta API access token (optional)

    Returns:
        Authentication status information as JSON
    """
    if access_token:
        return json.dumps(
            {
                "message": "Authentication Token Provided",
                "status": "Using provided access token for authentication",
                "token_info": f"Token preview: {access_token[:10]}...",
                "authentication_method": "direct_token",
                "ready_to_use": "You can now use all Meta Ads MCP tools and commands.",
            },
            indent=2,
        )

    return json.dumps(
        {
            "message": "Authentication Required",
            "instructions": "Send a Bearer token (Clerk JWT or pgs_xxx) in the Authorization header.",
            "alternative": "Or send a direct Meta access token via X-META-ACCESS-TOKEN header.",
            "authentication_method": "rule1_bearer",
        },
        indent=2,
    )


# Conditionally register as MCP tool only when enabled
if ENABLE_LOGIN_LINK:
    get_login_link = mcp_server.tool()(get_login_link)
