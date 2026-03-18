"""Authentication related functionality for Meta Ads API.

Simplified for Rule1 auth integration.  Bearer tokens are validated against
the Rule1 API, and Meta access tokens are fetched per-account from Rule1
(which decrypts them from the ad_account database table).
"""

from typing import Optional
import os
from .utils import logger
from . import rule1_auth

# Log important configuration information
logger.info("Authentication module initialized (Rule1 auth)")

# Global flag for authentication state
needs_authentication = False


async def get_current_access_token() -> Optional[str]:
    """Get the current access token.

    In HTTP / streamable-http mode this function is monkey-patched by
    ``http_auth_integration.setup_http_auth_patching`` to resolve tokens
    via Rule1 Bearer token + account_id context vars.

    In non-HTTP (stdio) mode there is no external auth flow -- the caller
    must pass an ``access_token`` directly to each tool.  This function
    returns ``None`` so that the ``meta_api_tool`` decorator reports an
    auth-required error.
    """
    logger.debug("get_current_access_token() called (base implementation)")
    return None


def invalidate_token() -> None:
    """Signal that the current token is invalid.

    Clears Rule1 auth caches so the next request fetches fresh data.
    """
    logger.info("invalidate_token: clearing Rule1 auth caches")
    rule1_auth.clear_all_caches()
    global needs_authentication
    needs_authentication = True
