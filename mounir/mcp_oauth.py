"""Standards-based OAuth support for user-configured remote MCP servers."""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable

from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)

from . import db


class DatabaseOAuthStorage:
    """Keep tokens and dynamic client registration private in Mounir's DB."""

    def __init__(self, server_id: int):
        self.server_id = int(server_id)

    async def get_tokens(self) -> OAuthToken | None:
        state = db.get_server_oauth_state(self.server_id) or {}
        value = state.get("oauth_tokens") or ""
        return OAuthToken.model_validate_json(value) if value else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        expires_at = time.time() + tokens.expires_in if tokens.expires_in else 0
        db.save_server_oauth_state(
            self.server_id,
            tokens=tokens.model_dump_json(exclude_none=True),
            token_expires_at=expires_at,
        )

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        state = db.get_server_oauth_state(self.server_id) or {}
        value = state.get("oauth_client_info") or ""
        return OAuthClientInformationFull.model_validate_json(value) if value else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        db.save_server_oauth_state(
            self.server_id,
            client_info=client_info.model_dump_json(exclude_none=True),
        )


class PersistentOAuthClientProvider(OAuthClientProvider):
    """Restore absolute token expiry so refresh works across MCP sessions."""

    async def _initialize(self) -> None:
        await super()._initialize()
        storage = self.context.storage
        if isinstance(storage, DatabaseOAuthStorage):
            state = db.get_server_oauth_state(storage.server_id) or {}
            expires_at = float(state.get("oauth_token_expires_at") or 0)
            self.context.token_expiry_time = expires_at or None


def provider_for_spec(
    spec: dict,
    *,
    redirect_handler: Callable[[str], Awaitable[None]] | None = None,
    callback_handler: Callable[[], Awaitable[tuple[str, str | None]]] | None = None,
) -> OAuthClientProvider:
    """Build the official MCP SDK OAuth provider from saved generic metadata."""
    server_id = int(spec.get("server_id") or spec.get("mcp_server_id") or 0)
    if not server_id:
        raise ValueError("OAuth server identifier is missing.")
    redirect_uri = str(spec.get("oauth_redirect_uri") or "").strip()
    if not redirect_uri:
        raise ValueError("Connect OAuth from Agent Studio before testing this server.")
    metadata = OAuthClientMetadata(
        redirect_uris=[redirect_uri],
        client_name="Mounir",
    )
    return PersistentOAuthClientProvider(
        server_url=str(spec.get("connection") or ""),
        client_metadata=metadata,
        storage=DatabaseOAuthStorage(server_id),
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
        timeout=300.0,
    )


def oauth_state_is_valid(server_id: int) -> bool:
    """Validate stored JSON before exposing a connected state to the UI."""
    state = db.get_server_oauth_state(server_id) or {}
    value = state.get("oauth_tokens") or ""
    if not value:
        return False
    try:
        OAuthToken.model_validate(json.loads(value))
    except Exception:
        return False
    return True
