"""Official Meta OAuth and Graph API adapter.

OAuth connects an installation to Meta. Agent tools are a separate layer that
calls the small operations exposed here; no MCP protocol is required.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx


DEFAULT_GRAPH_VERSION = "v26.0"
THREADS_API_VERSION = "v1.0"


_PLATFORMS: dict[str, dict[str, Any]] = {
    "facebook": {
        "id": "facebook",
        "label": "Facebook",
        "description": "Manage Facebook Pages that the connected user is allowed to manage.",
        "account_kind": "Facebook Page or ad account",
        "default_api_version": DEFAULT_GRAPH_VERSION,
        "auth_strategies": [
            {"id": "facebook_login", "label": "Facebook Login"},
        ],
        "excluded": ["Facebook Groups", "personal profiles", "scraping"],
        "capabilities": [
            {
                "id": "pages",
                "label": "Discover Pages",
                "description": "List Pages the connected user is allowed to manage.",
                "scopes": ["pages_show_list"],
                "required": True,
            },
            {
                "id": "read_content",
                "label": "Read Page content",
                "description": "Read Page posts and engagement needed for management workflows.",
                "scopes": ["pages_read_engagement"],
            },
            {
                "id": "publish",
                "label": "Publish Page posts",
                "description": "Create posts as a selected Facebook Page.",
                "scopes": ["pages_manage_posts"],
            },
            {
                "id": "moderate",
                "label": "Manage Page engagement",
                "description": "Respond to and manage Page comments and engagement.",
                "scopes": ["pages_manage_engagement", "pages_read_engagement"],
                "available": False,
            },
            {
                "id": "insights",
                "label": "Read Page insights",
                "description": "Read Page performance metrics.",
                "scopes": ["read_insights", "pages_read_engagement"],
                "available": False,
            },
            {
                "id": "ads_read",
                "label": "Read Meta Ads",
                "description": "Discover ad accounts and read campaign configuration and delivery status.",
                "scopes": ["ads_read"],
            },
            {
                "id": "ads_manage",
                "label": "Manage Meta Ads",
                "description": "Change supported campaign settings in selected ad accounts.",
                "scopes": ["ads_management"],
            },
        ],
    },
    "messenger": {
        "id": "messenger",
        "label": "Messenger",
        "description": "Serve conversations for connected Facebook Pages within Meta messaging rules.",
        "account_kind": "Facebook Page",
        "default_api_version": DEFAULT_GRAPH_VERSION,
        "auth_strategies": [
            {"id": "facebook_login", "label": "Facebook Login"},
        ],
        "excluded": ["personal Messenger accounts", "cold automated DMs", "scraping"],
        "capabilities": [
            {
                "id": "pages",
                "label": "Discover Pages",
                "description": "List Pages that can own Messenger conversations.",
                "scopes": ["pages_show_list"],
                "required": True,
            },
            {
                "id": "conversations",
                "label": "Page conversations",
                "description": "Receive and reply to Page conversations after a person contacts the Page.",
                "scopes": [
                    "pages_messaging",
                    "pages_manage_metadata",
                    "pages_read_engagement",
                ],
                "available": False,
            },
        ],
    },
    "instagram": {
        "id": "instagram",
        "label": "Instagram",
        "description": "Manage Instagram professional accounts using an official Instagram API login.",
        "account_kind": "Instagram professional account",
        "default_api_version": DEFAULT_GRAPH_VERSION,
        "auth_strategies": [
            {"id": "instagram_login", "label": "Instagram Login"},
            {"id": "facebook_login", "label": "Facebook Login"},
        ],
        "excluded": ["personal accounts", "cold automated DMs", "scraping"],
        "capabilities": [
            {
                "id": "profile",
                "label": "Professional profile",
                "description": "Identify the connected professional account.",
                "scopes_by_auth": {
                    "instagram_login": ["instagram_business_basic"],
                    "facebook_login": ["instagram_basic", "pages_show_list"],
                },
                "required": True,
            },
            {
                "id": "publish",
                "label": "Publish content",
                "description": "Publish supported media to the professional account.",
                "scopes_by_auth": {
                    "instagram_login": ["instagram_business_content_publish"],
                    "facebook_login": ["instagram_content_publish"],
                },
            },
            {
                "id": "moderate",
                "label": "Manage comments",
                "description": "Read, reply to, hide, or delete comments where supported.",
                "scopes_by_auth": {
                    "instagram_login": ["instagram_business_manage_comments"],
                    "facebook_login": ["instagram_manage_comments"],
                },
                "available": False,
            },
            {
                "id": "messages",
                "label": "Customer messages",
                "description": "Handle conversations initiated through the professional account.",
                "scopes_by_auth": {
                    "instagram_login": ["instagram_business_manage_messages"],
                    "facebook_login": ["instagram_manage_messages"],
                },
                "available": False,
            },
            {
                "id": "insights",
                "label": "Read insights",
                "description": "Read professional account and media metrics.",
                "scopes_by_auth": {
                    "instagram_login": ["instagram_business_manage_insights"],
                    "facebook_login": ["instagram_manage_insights"],
                },
                "available": False,
            },
        ],
    },
    "threads": {
        "id": "threads",
        "label": "Threads",
        "description": "Publish and manage a connected Threads profile through the Threads API.",
        "account_kind": "Threads profile",
        "default_api_version": THREADS_API_VERSION,
        "auth_strategies": [
            {"id": "threads_oauth", "label": "Threads OAuth"},
        ],
        "excluded": ["password automation", "scraping", "unsolicited messaging"],
        "capabilities": [
            {
                "id": "profile",
                "label": "Threads profile",
                "description": "Identify the connected Threads profile.",
                "scopes": ["threads_basic"],
                "required": True,
            },
            {
                "id": "publish",
                "label": "Publish posts",
                "description": "Create and publish supported Threads posts.",
                "scopes": ["threads_content_publish"],
            },
            {
                "id": "read_replies",
                "label": "Read replies",
                "description": "Read replies to the connected profile's posts.",
                "scopes": ["threads_read_replies"],
                "available": False,
            },
            {
                "id": "moderate",
                "label": "Manage replies",
                "description": "Manage replies where the Threads API permits it.",
                "scopes": ["threads_manage_replies"],
                "available": False,
            },
            {
                "id": "insights",
                "label": "Read insights",
                "description": "Read Threads profile and media metrics.",
                "scopes": ["threads_manage_insights"],
                "available": False,
            },
        ],
    },
}


class MetaApiError(RuntimeError):
    """A sanitized error returned by an official Meta endpoint."""


def platform_definitions() -> list[dict]:
    return [deepcopy(_PLATFORMS[key]) for key in ("facebook", "messenger", "instagram", "threads")]


def platform_definition(platform: str) -> dict:
    normalized = str(platform or "").strip().lower()
    if normalized not in _PLATFORMS:
        raise ValueError("Meta platform is not supported")
    return deepcopy(_PLATFORMS[normalized])


def scopes_for(connection: dict) -> list[str]:
    definition = platform_definition(connection.get("platform", ""))
    selected = set(connection.get("requested_capabilities") or [])
    scopes: list[str] = []
    for capability in definition["capabilities"]:
        if capability.get("available") is False:
            continue
        if not capability.get("required") and capability["id"] not in selected:
            continue
        values = capability.get("scopes")
        if values is None:
            values = (capability.get("scopes_by_auth") or {}).get(
                connection.get("auth_strategy"), []
            )
        for scope in values:
            if scope not in scopes:
                scopes.append(scope)
    return scopes


def authorization_url(connection: dict, redirect_uri: str, state: str) -> str:
    platform = str(connection.get("platform") or "").lower()
    strategy = str(connection.get("auth_strategy") or "").lower()
    params = {
        "client_id": connection.get("app_id", ""),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": ",".join(scopes_for(connection)),
        "state": state,
    }
    if strategy == "facebook_login":
        base = f"https://www.facebook.com/{connection['api_version']}/dialog/oauth"
    elif strategy == "instagram_login":
        base = "https://www.instagram.com/oauth/authorize"
        params.update(enable_fb_login="0", force_authentication="1")
    elif strategy == "threads_oauth":
        base = "https://threads.net/oauth/authorize"
    else:
        raise ValueError(f"Unsupported OAuth strategy for {platform}")
    return f"{base}?{urlencode(params)}"


def _error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        message = error.get("message") or error.get("error_user_msg")
    else:
        message = error or payload.get("error_message") if isinstance(payload, dict) else ""
    return str(message or f"Meta returned HTTP {response.status_code}")[:600]


def _request_json(client: httpx.Client, method: str, url: str, **kwargs) -> dict:
    try:
        response = client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise MetaApiError(f"Could not reach Meta: {exc}") from exc
    if response.is_error:
        raise MetaApiError(_error_message(response))
    try:
        payload = response.json()
    except ValueError as exc:
        raise MetaApiError("Meta returned an invalid response") from exc
    if not isinstance(payload, dict):
        raise MetaApiError("Meta returned an unexpected response")
    if payload.get("error"):
        raise MetaApiError(_error_message(response))
    return payload


def _expiry(expires_in) -> str | None:
    try:
        seconds = max(0, int(expires_in))
    except (TypeError, ValueError):
        return None
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def exchange_code(
    connection: dict,
    code: str,
    redirect_uri: str,
    *,
    client: httpx.Client | None = None,
) -> dict:
    """Exchange an OAuth code and opportunistically obtain a long-lived token."""
    owned = client is None
    client = client or httpx.Client(timeout=20.0)
    strategy = connection["auth_strategy"]
    try:
        form = {
            "client_id": connection["app_id"],
            "client_secret": connection["app_secret"],
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "code": code,
        }
        if strategy == "facebook_login":
            url = f"https://graph.facebook.com/{connection['api_version']}/oauth/access_token"
        elif strategy == "instagram_login":
            url = "https://api.instagram.com/oauth/access_token"
        elif strategy == "threads_oauth":
            url = "https://graph.threads.net/oauth/access_token"
        else:
            raise ValueError("Unsupported Meta OAuth strategy")
        short = _request_json(client, "POST", url, data=form)
        result = {
            "access_token": str(short.get("access_token") or ""),
            "token_type": str(short.get("token_type") or "bearer"),
            "expires_at": _expiry(short.get("expires_in")),
        }
        if not result["access_token"]:
            raise MetaApiError("Meta did not return an access token")

        try:
            if strategy == "facebook_login":
                long_lived = _request_json(
                    client,
                    "GET",
                    f"https://graph.facebook.com/{connection['api_version']}/oauth/access_token",
                    params={
                        "grant_type": "fb_exchange_token",
                        "client_id": connection["app_id"],
                        "client_secret": connection["app_secret"],
                        "fb_exchange_token": result["access_token"],
                    },
                )
            else:
                host = "graph.instagram.com" if strategy == "instagram_login" else "graph.threads.net"
                grant = "ig_exchange_token" if strategy == "instagram_login" else "th_exchange_token"
                long_lived = _request_json(
                    client,
                    "GET",
                    f"https://{host}/access_token",
                    params={
                        "grant_type": grant,
                        "client_secret": connection["app_secret"],
                        "access_token": result["access_token"],
                    },
                )
            if long_lived.get("access_token"):
                result.update(
                    access_token=str(long_lived["access_token"]),
                    token_type=str(long_lived.get("token_type") or result["token_type"]),
                    expires_at=_expiry(long_lived.get("expires_in")) or result["expires_at"],
                )
        except MetaApiError:
            # A valid short-lived token is still usable. Testing and refresh UI
            # will make a long-lived-token failure visible before expiry.
            pass
        return result
    finally:
        if owned:
            client.close()


def discover_accounts(
    connection: dict, *, client: httpx.Client | None = None
) -> list[dict]:
    owned = client is None
    client = client or httpx.Client(timeout=20.0)
    token = connection.get("access_token") or ""
    platform = connection["platform"]
    version = connection["api_version"]
    requested = list(connection.get("requested_capabilities") or [])
    try:
        if platform in {"facebook", "messenger"}:
            payload = _request_json(
                client,
                "GET",
                f"https://graph.facebook.com/{version}/me/accounts",
                params={
                    "fields": "id,name,access_token,tasks",
                    "access_token": token,
                },
            )
            accounts = []
            for page in payload.get("data") or []:
                tasks = [str(task) for task in page.get("tasks") or []]
                accounts.append(
                    {
                        "external_id": str(page.get("id") or ""),
                        "name": page.get("name") or "",
                        "account_type": "facebook_page",
                        "access_token": page.get("access_token") or "",
                        "tasks": tasks,
                        "capabilities": requested,
                    }
                )
            accounts = [account for account in accounts if account["external_id"]]
            if platform == "facebook" and {"ads_read", "ads_manage"}.intersection(requested):
                ads = _request_json(
                    client,
                    "GET",
                    f"https://graph.facebook.com/{version}/me/adaccounts",
                    params={
                        "fields": "id,name,account_id,account_status,currency,timezone_name",
                        "access_token": token,
                    },
                )
                for account in ads.get("data") or []:
                    if not account.get("id"):
                        continue
                    accounts.append({
                        "external_id": str(account["id"]),
                        "name": account.get("name") or str(account["id"]),
                        "account_type": "meta_ad_account",
                        "capabilities": requested,
                        "metadata": {
                            "account_id": str(account.get("account_id") or ""),
                            "account_status": account.get("account_status"),
                            "currency": account.get("currency") or "",
                            "timezone_name": account.get("timezone_name") or "",
                        },
                    })
            return accounts

        if platform == "instagram" and connection["auth_strategy"] == "facebook_login":
            payload = _request_json(
                client,
                "GET",
                f"https://graph.facebook.com/{version}/me/accounts",
                params={
                    "fields": "id,name,access_token,tasks,instagram_business_account{id,username,name,profile_picture_url}",
                    "access_token": token,
                },
            )
            accounts = []
            for page in payload.get("data") or []:
                profile = page.get("instagram_business_account") or {}
                if not profile.get("id"):
                    continue
                accounts.append(
                    {
                        "external_id": str(profile["id"]),
                        "name": profile.get("name") or profile.get("username") or "",
                        "username": profile.get("username") or "",
                        "account_type": "instagram_professional",
                        "access_token": page.get("access_token") or token,
                        "tasks": page.get("tasks") or [],
                        "capabilities": requested,
                        "metadata": {
                            "facebook_page_id": str(page.get("id") or ""),
                            "profile_picture_url": profile.get("profile_picture_url") or "",
                        },
                    }
                )
            return accounts

        if platform == "instagram":
            payload = _request_json(
                client,
                "GET",
                f"https://graph.instagram.com/{version}/me",
                params={
                    "fields": "user_id,username,name,account_type,profile_picture_url",
                    "access_token": token,
                },
            )
            external_id = payload.get("user_id") or payload.get("id")
            return [{
                "external_id": str(external_id or ""),
                "name": payload.get("name") or payload.get("username") or "",
                "username": payload.get("username") or "",
                "account_type": str(payload.get("account_type") or "instagram_professional").lower(),
                "capabilities": requested,
                "metadata": {"profile_picture_url": payload.get("profile_picture_url") or ""},
            }] if external_id else []

        if platform == "threads":
            payload = _request_json(
                client,
                "GET",
                f"https://graph.threads.net/{version}/me",
                params={
                    "fields": "id,username,name,threads_profile_picture_url,threads_biography",
                    "access_token": token,
                },
            )
            external_id = payload.get("id")
            return [{
                "external_id": str(external_id or ""),
                "name": payload.get("name") or payload.get("username") or "",
                "username": payload.get("username") or "",
                "account_type": "threads_profile",
                "capabilities": requested,
                "metadata": {
                    "profile_picture_url": payload.get("threads_profile_picture_url") or "",
                    "biography": payload.get("threads_biography") or "",
                },
            }] if external_id else []
        raise ValueError("Meta platform is not supported")
    finally:
        if owned:
            client.close()


def enabled_accounts(platform: str) -> list[dict]:
    """Return safe metadata for accounts an agent may use."""
    from . import db

    return db.list_enabled_meta_accounts(platform)


def _account_context(
    account_id: int,
    platform: str,
    *,
    account_types: set[str] | None = None,
    capability: str | None = None,
) -> tuple[dict, dict, str]:
    from . import db

    context = db.get_meta_account_context(account_id)
    if context is None:
        raise ValueError("The selected Meta account does not exist")
    account, connection = context["account"], context["connection"]
    if connection["platform"] != platform:
        raise ValueError(f"The selected account is not a {platform} account")
    if not connection["enabled"] or connection["connection_status"] != "connected":
        raise ValueError("The selected Meta connection is not enabled and connected")
    if not account["enabled"]:
        raise ValueError("The selected Meta account is disabled")
    if account_types and account["account_type"] not in account_types:
        raise ValueError("The selected account does not support this operation")
    if capability and capability not in connection["requested_capabilities"]:
        shipped = next(
            (
                item for item in _PLATFORMS[platform]["capabilities"]
                if item["id"] == capability
            ),
            {},
        )
        if not shipped.get("required"):
            raise ValueError(f"Enable the {capability} capability and reconnect with OAuth first")
    token = account.get("access_token") or connection.get("access_token") or ""
    if not token:
        raise ValueError("The selected account has no access token")
    return account, connection, token


def _graph_operation(
    method: str,
    url: str,
    token: str,
    *,
    params: dict | None = None,
    data: dict | None = None,
    json_body: dict | None = None,
    client: httpx.Client | None = None,
) -> dict:
    owned = client is None
    client = client or httpx.Client(timeout=25.0)
    query = dict(params or {})
    query["access_token"] = token
    try:
        return _request_json(client, method, url, params=query, data=data, json=json_body)
    finally:
        if owned:
            client.close()


def facebook_page_posts(account_id: int, limit: int = 10) -> list[dict]:
    account, connection, token = _account_context(
        account_id,
        "facebook",
        account_types={"facebook_page"},
        capability="read_content",
    )
    payload = _graph_operation(
        "GET",
        f"https://graph.facebook.com/{connection['api_version']}/{account['external_id']}/feed",
        token,
        params={
            "fields": "id,message,created_time,permalink_url",
            "limit": max(1, min(int(limit), 25)),
        },
    )
    return list(payload.get("data") or [])


def publish_facebook_page_post(account_id: int, message: str, link: str = "") -> dict:
    account, connection, token = _account_context(
        account_id,
        "facebook",
        account_types={"facebook_page"},
        capability="publish",
    )
    message = str(message or "").strip()
    link = str(link or "").strip()
    if not message and not link:
        raise ValueError("A Facebook Page post needs a message or link")
    data = {"message": message}
    if link:
        if not link.startswith(("http://", "https://")):
            raise ValueError("Facebook post link must use http:// or https://")
        data["link"] = link
    return _graph_operation(
        "POST",
        f"https://graph.facebook.com/{connection['api_version']}/{account['external_id']}/feed",
        token,
        data=data,
    )


def facebook_ad_campaigns(account_id: int, limit: int = 25) -> list[dict]:
    account, connection, token = _account_context(
        account_id,
        "facebook",
        account_types={"meta_ad_account"},
        capability="ads_read",
    )
    payload = _graph_operation(
        "GET",
        f"https://graph.facebook.com/{connection['api_version']}/{account['external_id']}/campaigns",
        token,
        params={
            "fields": "id,name,status,effective_status,objective,buying_type,updated_time",
            "limit": max(1, min(int(limit), 50)),
        },
    )
    return list(payload.get("data") or [])


def set_facebook_ad_campaign_status(
    account_id: int, campaign_id: str, status: str
) -> dict:
    account, connection, token = _account_context(
        account_id,
        "facebook",
        account_types={"meta_ad_account"},
        capability="ads_manage",
    )
    status = str(status or "").strip().upper()
    if status not in {"ACTIVE", "PAUSED"}:
        raise ValueError("Campaign status must be ACTIVE or PAUSED")
    campaign_id = str(campaign_id or "").strip()
    identity = _graph_operation(
        "GET",
        f"https://graph.facebook.com/{connection['api_version']}/{campaign_id}",
        token,
        params={"fields": "id,account_id"},
    )
    expected = str(account.get("metadata", {}).get("account_id") or "")
    if not expected or str(identity.get("account_id") or "") != expected:
        raise ValueError("The campaign does not belong to the selected ad account")
    return _graph_operation(
        "POST",
        f"https://graph.facebook.com/{connection['api_version']}/{campaign_id}",
        token,
        data={"status": status},
    )


def instagram_media(account_id: int, limit: int = 10) -> list[dict]:
    account, connection, token = _account_context(
        account_id,
        "instagram",
        account_types={"instagram_professional", "business", "media_creator"},
        capability="profile",
    )
    host = "graph.instagram.com" if connection["auth_strategy"] == "instagram_login" else "graph.facebook.com"
    payload = _graph_operation(
        "GET",
        f"https://{host}/{connection['api_version']}/{account['external_id']}/media",
        token,
        params={
            "fields": "id,caption,media_type,media_url,permalink,timestamp",
            "limit": max(1, min(int(limit), 25)),
        },
    )
    return list(payload.get("data") or [])


def publish_instagram_image(account_id: int, image_url: str, caption: str = "") -> dict:
    account, connection, token = _account_context(
        account_id,
        "instagram",
        account_types={"instagram_professional", "business", "media_creator"},
        capability="publish",
    )
    image_url = str(image_url or "").strip()
    if not image_url.startswith(("http://", "https://")):
        raise ValueError("Instagram publishing needs a publicly reachable http(s) image URL")
    host = "graph.instagram.com" if connection["auth_strategy"] == "instagram_login" else "graph.facebook.com"
    base = f"https://{host}/{connection['api_version']}/{account['external_id']}"
    container = _graph_operation(
        "POST", f"{base}/media", token,
        data={"image_url": image_url, "caption": str(caption or "").strip()},
    )
    creation_id = str(container.get("id") or "")
    if not creation_id:
        raise MetaApiError("Instagram did not return a media container ID")
    return _graph_operation(
        "POST", f"{base}/media_publish", token, data={"creation_id": creation_id}
    )


def threads_posts(account_id: int, limit: int = 10) -> list[dict]:
    account, connection, token = _account_context(
        account_id, "threads", account_types={"threads_profile"}, capability="profile"
    )
    payload = _graph_operation(
        "GET",
        f"https://graph.threads.net/{connection['api_version']}/{account['external_id']}/threads",
        token,
        params={
            "fields": "id,text,media_type,permalink,timestamp,username",
            "limit": max(1, min(int(limit), 25)),
        },
    )
    return list(payload.get("data") or [])


def publish_threads_text(account_id: int, text: str) -> dict:
    account, connection, token = _account_context(
        account_id,
        "threads",
        account_types={"threads_profile"},
        capability="publish",
    )
    text = str(text or "").strip()
    if not text:
        raise ValueError("A Threads post needs text")
    base = f"https://graph.threads.net/{connection['api_version']}/{account['external_id']}"
    container = _graph_operation(
        "POST", f"{base}/threads", token, data={"media_type": "TEXT", "text": text}
    )
    creation_id = str(container.get("id") or "")
    if not creation_id:
        raise MetaApiError("Threads did not return a media container ID")
    return _graph_operation(
        "POST", f"{base}/threads_publish", token, data={"creation_id": creation_id}
    )

