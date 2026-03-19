"""
Rule1 Authentication Integration

Validates user Bearer tokens against the Rule1 API and fetches
Meta access tokens for ad accounts.

Flow:
1. User sends Bearer token (Clerk JWT or pgs_xxx) in HTTP header
2. We validate it against Rule1 API -> get org context
3. When a tool needs a Meta token, we fetch it from Rule1 API
   (Rule1 decrypts it from the ad_account database table)
"""

import os
import time
from typing import Any, Dict, List, Optional

import httpx

from .utils import logger

# Base URL for Rule1 API (defaults to production)
RULE1_API_URL = os.environ.get("RULE1_API_URL", "https://app.rule1.ai")

# ---------------------------------------------------------------------------
# Simple in-memory TTL cache
# ---------------------------------------------------------------------------

class _TTLCache:
    """Minimal in-memory cache with per-entry TTL."""

    def __init__(self) -> None:
        # key -> (value, expiry_timestamp)
        self._store: Dict[str, tuple] = {}

    def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expiry = entry
        if time.monotonic() > expiry:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        self._store[key] = (value, time.monotonic() + ttl_seconds)

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()


# Shared caches
_org_cache = _TTLCache()          # bearer_token -> org info   (long-lived, ~60 s)
_meta_token_cache = _TTLCache()   # (bearer, account_id) -> meta token (5 min)
_accounts_cache = _TTLCache()     # bearer_token:org_id -> account list (60 s)

# Cache TTLs in seconds
_ORG_CACHE_TTL = 60
_META_TOKEN_CACHE_TTL = 300  # 5 minutes
_ACCOUNTS_CACHE_TTL = 60

# Persistent org_id store: bearer_token -> organization_id
# Set once by get_ad_accounts, reused by all other tools in the session.
_bearer_org_map: Dict[str, str] = {}


def set_organization_for_bearer(bearer_token: str, org_id: str) -> None:
    """Remember which organization_id to use for this bearer token."""
    _bearer_org_map[bearer_token] = org_id


def get_organization_for_bearer(bearer_token: str) -> Optional[str]:
    """Get the stored organization_id for this bearer token."""
    return _bearer_org_map.get(bearer_token)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def validate_token(bearer_token: str) -> Dict[str, Any]:
    """Validate a Bearer token against the Rule1 API.

    Calls ``GET {RULE1_API_URL}/api/mcp/organizations`` with the Bearer token.

    Returns:
        Organisation context dictionary on success.

    Raises:
        ValueError: If the token is rejected or the API returns an error.
    """
    if not bearer_token:
        raise ValueError("Bearer token is empty")

    cached = _org_cache.get(bearer_token)
    if cached is not None:
        logger.debug("validate_token: returning cached org context")
        return cached

    url = f"{RULE1_API_URL}/api/mcp/organizations"
    headers = {"Authorization": f"Bearer {bearer_token}"}

    logger.debug("validate_token: calling %s", url)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, headers=headers)

        if response.status_code == 200:
            data = response.json()
            _org_cache.set(bearer_token, data, _ORG_CACHE_TTL)
            logger.info("validate_token: token validated successfully")
            return data

        # Non-200 -- treat as auth failure
        body = ""
        try:
            body = response.text
        except Exception:
            pass
        logger.warning(
            "validate_token: Rule1 API returned %s: %s",
            response.status_code,
            body[:200],
        )
        raise ValueError(
            f"Token validation failed (HTTP {response.status_code}): {body[:200]}"
        )

    except httpx.RequestError as exc:
        logger.error("validate_token: network error: %s", exc)
        raise ValueError(f"Could not reach Rule1 API: {exc}") from exc


async def get_meta_token(bearer_token: str, account_id: str, organization_id: Optional[str] = None) -> str:
    """Fetch the decrypted Meta access token for a given ad account.

    Calls ``GET {RULE1_API_URL}/api/mcp/accounts/{account_id}/meta-token``
    with the Bearer token.

    Returns:
        The Meta access token string.

    Raises:
        ValueError: If the token cannot be retrieved.
    """
    if not bearer_token:
        raise ValueError("Bearer token is empty")
    if not account_id:
        raise ValueError("account_id is required to fetch a Meta token")

    # Strip act_ prefix — Rule1 DB stores numeric IDs without prefix
    clean_id = account_id.removeprefix("act_")
    cache_key = f"{bearer_token}:{clean_id}"
    cached = _meta_token_cache.get(cache_key)
    if cached is not None:
        logger.debug("get_meta_token: returning cached Meta token for account %s", account_id)
        return cached

    url = f"{RULE1_API_URL}/api/mcp/accounts/{clean_id}/meta-token"
    if organization_id:
        url += f"?organizationId={organization_id}"
    headers = {"Authorization": f"Bearer {bearer_token}"}

    logger.debug("get_meta_token: calling %s", url)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, headers=headers)

        if response.status_code == 200:
            data = response.json()
            # The Rule1 endpoint returns { success: true, data: { access_token, ... } }
            nested = data.get("data", {})
            token = nested.get("access_token") or data.get("access_token") or data.get("token")
            if not token:
                raise ValueError("Rule1 API returned 200 but no token in response body")
            _meta_token_cache.set(cache_key, token, _META_TOKEN_CACHE_TTL)
            logger.info("get_meta_token: Meta token retrieved for account %s", account_id)
            return token

        body = ""
        try:
            body = response.text
        except Exception:
            pass
        logger.warning(
            "get_meta_token: Rule1 API returned %s for account %s: %s",
            response.status_code,
            account_id,
            body[:200],
        )
        raise ValueError(
            f"Failed to get Meta token for account {account_id} "
            f"(HTTP {response.status_code}): {body[:200]}"
        )

    except httpx.RequestError as exc:
        logger.error("get_meta_token: network error: %s", exc)
        raise ValueError(f"Could not reach Rule1 API: {exc}") from exc


async def list_ad_accounts(bearer_token: str, organization_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """List ad accounts accessible by the authenticated user.

    Calls ``GET {RULE1_API_URL}/api/mcp/accounts`` with the Bearer token.

    Returns:
        List of ad account dictionaries.

    Raises:
        ValueError: If the request fails.
    """
    if not bearer_token:
        raise ValueError("Bearer token is empty")

    cache_key = f"{bearer_token}:{organization_id or 'default'}"
    cached = _accounts_cache.get(cache_key)
    if cached is not None:
        logger.debug("list_ad_accounts: returning cached account list")
        return cached

    url = f"{RULE1_API_URL}/api/mcp/accounts"
    if organization_id:
        url += f"?organizationId={organization_id}"
    headers = {"Authorization": f"Bearer {bearer_token}"}

    logger.debug("list_ad_accounts: calling %s", url)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, headers=headers)

        if response.status_code == 200:
            data = response.json()
            # The Rule1 endpoint returns { success: true, data: { accounts: [...], ... } }
            nested = data.get("data", {})
            if isinstance(nested, dict):
                accounts = nested.get("accounts", [])
            elif isinstance(data, list):
                accounts = data
            else:
                accounts = data.get("accounts", [])
            _accounts_cache.set(cache_key, accounts, _ACCOUNTS_CACHE_TTL)
            logger.info("list_ad_accounts: retrieved %d accounts", len(accounts))
            return accounts

        body = ""
        try:
            body = response.text
        except Exception:
            pass
        logger.warning(
            "list_ad_accounts: Rule1 API returned %s: %s",
            response.status_code,
            body[:200],
        )
        raise ValueError(
            f"Failed to list ad accounts (HTTP {response.status_code}): {body[:200]}"
        )

    except httpx.RequestError as exc:
        logger.error("list_ad_accounts: network error: %s", exc)
        raise ValueError(f"Could not reach Rule1 API: {exc}") from exc


def invalidate_meta_token_cache(bearer_token: str, account_id: str) -> None:
    """Remove a cached Meta token so the next call fetches a fresh one."""
    cache_key = f"{bearer_token}:{account_id}"
    _meta_token_cache.invalidate(cache_key)
    logger.debug("invalidate_meta_token_cache: cleared cache for account %s", account_id)


def clear_all_caches() -> None:
    """Clear every in-memory cache (useful on token invalidation)."""
    _org_cache.clear()
    _meta_token_cache.clear()
    _accounts_cache.clear()
    logger.debug("clear_all_caches: all Rule1 auth caches cleared")
