"""Core API functionality for Meta Ads API."""

from typing import Any, Dict, Optional, Callable
import json
import httpx
import asyncio
import functools
import os
from . import auth
from .utils import logger


class McpToolError(Exception):
    """Base class for MCP tool errors that must set isError: true.

    Subclasses should be raised (not returned) from tool handlers.
    meta_api_tool re-raises these so FastMCP sees them and sets
    isError: true in the JSON-RPC response, which triggers the usage
    credit refund in the Next.js proxy.
    """
    pass


# Constants
META_GRAPH_API_VERSION = "v24.0"
META_GRAPH_API_BASE = f"https://graph.facebook.com/{META_GRAPH_API_VERSION}"
USER_AGENT = "meta-ads-mcp/1.0"

# Log key environment and configuration at startup
logger.info("Core API module initialized")
logger.info("Graph API Version: %s", META_GRAPH_API_VERSION)


class GraphAPIError(Exception):
    """Exception raised for errors from the Graph API."""
    def __init__(self, error_data: Dict[str, Any]):
        self.error_data = error_data
        self.message = error_data.get('message', 'Unknown Graph API error')
        super().__init__(self.message)

        logger.error("Graph API Error: %s", self.message)
        logger.debug("Error details: %s", error_data)

        # Check if this is an auth error (code 4 is rate limiting, NOT auth)
        if "code" in error_data and error_data["code"] in [190, 102]:
            logger.warning(
                "Auth error detected (code: %s). Invalidating token.",
                error_data["code"],
            )
            auth.invalidate_token()
        elif "code" in error_data and error_data["code"] == 4:
            logger.warning(
                "Rate limit error detected (code: 4, subcode: %s). Token still valid.",
                error_data.get("error_subcode", "N/A"),
            )


def _log_meta_rate_limit_headers(headers: dict, endpoint: str) -> None:
    """Log Meta's rate limit headers for observability."""
    app_usage = headers.get("x-app-usage")
    biz_usage = headers.get("x-business-use-case-usage")
    ad_account_usage = headers.get("x-ad-account-usage")

    if app_usage or biz_usage or ad_account_usage:
        usage_data = {}
        if app_usage:
            try:
                usage_data["app_usage"] = json.loads(app_usage)
            except (json.JSONDecodeError, TypeError):
                usage_data["app_usage_raw"] = str(app_usage)
        if biz_usage:
            try:
                usage_data["business_use_case_usage"] = json.loads(biz_usage)
            except (json.JSONDecodeError, TypeError):
                usage_data["business_use_case_usage_raw"] = str(biz_usage)
        if ad_account_usage:
            try:
                usage_data["ad_account_usage"] = json.loads(ad_account_usage)
            except (json.JSONDecodeError, TypeError):
                usage_data["ad_account_usage_raw"] = str(ad_account_usage)

        # Warn at high usage levels (any field >= 80%)
        is_high = False
        for key, val in usage_data.items():
            if isinstance(val, dict):
                for metric, pct in val.items():
                    if isinstance(pct, (int, float)) and pct >= 80:
                        is_high = True
                        break

        log_fn = logger.warning if is_high else logger.info
        log_fn("meta_rate_limit_usage endpoint=%s %s", endpoint, json.dumps(usage_data))


async def make_api_request(
    endpoint: str,
    access_token: str,
    params: Optional[Dict[str, Any]] = None,
    method: str = "GET",
) -> Dict[str, Any]:
    """Make a request to the Meta Graph API.

    Args:
        endpoint: API endpoint path (without base URL)
        access_token: Meta API access token
        params: Additional query parameters
        method: HTTP method (GET, POST, DELETE)

    Returns:
        API response as a dictionary
    """
    if not access_token:
        logger.error("API request attempted with blank access token")
        return {
            "error": {
                "message": "Authentication Required",
                "details": "A valid access token is required to access the Meta API",
                "action_required": "Please authenticate first",
            }
        }

    url = f"{META_GRAPH_API_BASE}/{endpoint}"

    headers = {
        "User-Agent": USER_AGENT,
    }

    request_params = params or {}
    request_params["access_token"] = access_token

    # Logging the request (masking token for security)
    masked_params = {
        k: "***MASKED***" if k in ("access_token", "appsecret_proof") else v
        for k, v in request_params.items()
    }
    logger.debug("API Request: %s %s", method, url)
    logger.debug("Request params: %s", masked_params)

    async with httpx.AsyncClient() as client:
        try:
            if method == "GET":
                encoded_params = {}
                for key, value in request_params.items():
                    if isinstance(value, (dict, list)):
                        encoded_params[key] = json.dumps(value)
                    else:
                        encoded_params[key] = value
                response = await client.get(
                    url, params=encoded_params, headers=headers, timeout=30.0
                )
            elif method == "POST":
                if "targeting" in request_params and isinstance(
                    request_params["targeting"], dict
                ):
                    request_params["targeting"] = json.dumps(
                        request_params["targeting"]
                    )

                for key, value in request_params.items():
                    if isinstance(value, (list, dict)):
                        request_params[key] = json.dumps(value)

                logger.debug("POST params (prepared): %s", masked_params)
                response = await client.post(
                    url, data=request_params, headers=headers, timeout=30.0
                )
            elif method == "DELETE":
                response = await client.delete(
                    url, params=request_params, headers=headers, timeout=30.0
                )
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            response.raise_for_status()
            logger.debug("API Response status: %s", response.status_code)

            _log_meta_rate_limit_headers(response.headers, endpoint)

            try:
                return response.json()
            except json.JSONDecodeError:
                return {
                    "text_response": response.text,
                    "status_code": response.status_code,
                }

        except httpx.HTTPStatusError as e:
            error_info = {}
            try:
                error_info = e.response.json()
            except Exception:
                error_info = {
                    "status_code": e.response.status_code,
                    "text": e.response.text,
                }

            logger.error("HTTP Error: %s - %s", e.response.status_code, error_info)

            _log_meta_rate_limit_headers(e.response.headers, endpoint)

            if "error" in error_info:
                error_obj = error_info.get("error", {})
                error_code = (
                    error_obj.get("code") if isinstance(error_obj, dict) else None
                )

                if error_code == 4:
                    logger.warning(
                        "Facebook API rate limit (code=4, subcode=%s). Token still valid.",
                        error_obj.get("error_subcode", "N/A"),
                    )
                elif error_code in [190, 102, 200, 10]:
                    logger.warning(
                        "Detected Facebook API auth error: %s", error_code
                    )
                    if error_code == 200 and "Provide valid app ID" in error_obj.get(
                        "message", ""
                    ):
                        logger.error("Meta API authentication configuration issue")
                        return {
                            "error": {
                                "message": "Meta API authentication configuration issue.",
                                "original_error": error_obj.get("message"),
                                "code": error_code,
                            }
                        }
                    auth.invalidate_token()
                elif e.response.status_code in [401, 403]:
                    logger.warning(
                        "Detected authentication error (%s)",
                        e.response.status_code,
                    )
                    auth.invalidate_token()
            elif e.response.status_code in [401, 403]:
                logger.warning(
                    "Detected authentication error (%s)", e.response.status_code
                )
                auth.invalidate_token()

            full_response = {
                "headers": dict(e.response.headers),
                "status_code": e.response.status_code,
                "url": str(e.response.url),
                "reason": getattr(e.response, "reason_phrase", "Unknown reason"),
                "request_method": e.request.method,
                "request_url": str(e.request.url),
            }

            return {
                "error": {
                    "message": f"HTTP Error: {e.response.status_code}",
                    "details": error_info,
                    "full_response": full_response,
                }
            }

        except Exception as e:
            logger.error("Request Error: %s", str(e))
            return {"error": {"message": str(e)}}


# ---------------------------------------------------------------------------
# Generic wrapper for all Meta API tools
# ---------------------------------------------------------------------------


def meta_api_tool(func):
    """Decorator for Meta API tools that handles authentication and error handling.

    Authentication resolution order:
    1. Explicit ``access_token`` kwarg (escape hatch)
    2. Direct Meta token from X-META-ACCESS-TOKEN header (via ContextVar)
    3. Rule1 Bearer token + account_id -> fetch Meta token from Rule1 API
    4. Base ``get_current_access_token()`` fallback (returns None in stdio)
    """

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            logger.debug("Function call: %s", func.__name__)
            safe_kwargs = {
                k: ("***TOKEN***" if k == "access_token" else v)
                for k, v in kwargs.items()
            }
            logger.debug("Kwargs: %s", safe_kwargs)

            # If access_token is not provided, resolve it
            if "access_token" not in kwargs or not kwargs["access_token"]:
                # Import here to avoid circular imports at module level
                from .http_auth_integration import FastMCPAuthIntegration
                from . import rule1_auth as _rule1_auth

                # Check for direct Meta token first
                direct_meta = FastMCPAuthIntegration.get_direct_meta_token()
                if direct_meta:
                    kwargs["access_token"] = direct_meta
                    logger.debug("Using direct Meta token from X-META-ACCESS-TOKEN header")
                else:
                    # Try Rule1 Bearer token path
                    bearer = FastMCPAuthIntegration.get_auth_token()
                    if bearer:
                        # Extract account_id and organization_id from tool kwargs
                        account_id = kwargs.get("account_id")
                        org_id = kwargs.get("organization_id") or FastMCPAuthIntegration.get_current_organization_id()
                        if account_id:
                            # Set ContextVar so get_current_access_token can also use it
                            FastMCPAuthIntegration.set_current_account_id(account_id)
                            if org_id:
                                FastMCPAuthIntegration.set_current_organization_id(org_id)
                            try:
                                meta_token = await _rule1_auth.get_meta_token(
                                    bearer, account_id, org_id
                                )
                                kwargs["access_token"] = meta_token
                                logger.debug(
                                    "Using Meta token from Rule1 for account %s",
                                    account_id,
                                )
                            except Exception as exc:
                                logger.error(
                                    "Failed to get Meta token via Rule1: %s", exc
                                )
                                # Fall through to get_current_access_token
                        else:
                            logger.debug(
                                "Bearer token present but no account_id in kwargs"
                            )

                    # If still no token, try the patched get_current_access_token
                    if "access_token" not in kwargs or not kwargs.get("access_token"):
                        try:
                            access_token = await auth.get_current_access_token()
                            if access_token:
                                kwargs["access_token"] = access_token
                                logger.debug("Using access token from get_current_access_token")
                            else:
                                logger.warning("No access token available")
                        except Exception as e:
                            logger.error("Error getting access token: %s", e)

            # Final validation
            if "access_token" not in kwargs or not kwargs["access_token"]:
                logger.warning("No access token available, authentication needed")
                return json.dumps(
                    {
                        "error": {
                            "message": "Authentication Required",
                            "details": {
                                "description": "A valid access token is required",
                                "action_required": "Provide a Bearer token (Clerk JWT or pgs_xxx) in the Authorization header",
                                "alternative": "Or provide a direct Meta token via X-META-ACCESS-TOKEN header",
                            },
                        }
                    },
                    indent=2,
                )

            # Call the original function
            result = await func(*args, **kwargs)

            # If the result is a string (JSON), try to parse it to check for errors
            if isinstance(result, str):
                try:
                    result_dict = json.loads(result)
                    if "error" in result_dict:
                        logger.error("Error in API response: %s", result_dict["error"])
                except Exception:
                    return json.dumps({"data": result}, indent=2)

            # If result is already a dictionary, ensure it's properly serialized
            if isinstance(result, dict):
                return json.dumps(result, indent=2)

            return result
        except McpToolError:
            raise  # Let FastMCP set isError: true and refund the usage credit
        except Exception as e:
            logger.error("Error in %s: %s", func.__name__, str(e))
            return json.dumps({"error": str(e)}, indent=2)

    return wrapper
