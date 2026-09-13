"""Who is allowed to talk to this server.

Claude's custom connectors support three auth types out of the box: OAuth with Dynamic
Client Registration, OAuth with client id metadata documents, and none at all. The obvious
thing for a one-user server -- a bearer token in a header -- is Claude's ``static_headers``
type, which is in beta, is entered by an organization administrator rather than by a user,
and needs Anthropic's approval for any non-standard header name. Machine-to-machine
``client_credentials`` is not supported at all: every connection requires a human to
consent. So OAuth it is, even for an audience of one. `[empirical]` -- read off Anthropic's
connector documentation on 2026-09-13, not inferred.

GitHub is the identity provider because the owner already has an account there and the app
already lives there. FastMCP's ``GitHubProvider`` presents a DCR-compliant face to Claude
while using one pre-registered GitHub OAuth app underneath, which is exactly the shape
Claude expects.

GitHub will happily authenticate three hundred million people, and this server is for one.
``OnlyTheOwner`` is that second gate: anyone else completes the OAuth dance and is then
refused at the first tool call. Without it, publishing the URL would publish the league.
"""

from __future__ import annotations

from typing import Any

from fastmcp.server.auth.providers.github import GitHubProvider
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware, MiddlewareContext

from ff.core.config import Settings
from ff.core.errors import ConfigError
from ff.core.logging import get_logger

log = get_logger(__name__)


class NotTheOwner(Exception):
    """Authenticated as somebody, but not as the one person this server serves."""


class OnlyTheOwner(Middleware):
    """Refuse every request that is not the configured GitHub login.

    Applied to tool calls and to listing alike. Hiding the tool list from a stranger is
    not security, but leaking the tool list is a small gift to one, and there is no reason
    to hand it over.
    """

    def __init__(self, allowed_login: str) -> None:
        self.allowed_login = allowed_login.lower()

    def _check(self) -> None:
        token = get_access_token()
        if token is None:
            raise NotTheOwner("Not authenticated.")
        claims: dict[str, Any] = token.claims or {}
        login = str(claims.get("login") or claims.get("preferred_username") or "").lower()
        if login != self.allowed_login:
            # Log the refusal but not the login: it is somebody else's identifier and this
            # server has no business keeping it.
            log.warning("access_refused", reason="login not allowed")
            raise NotTheOwner("This server serves one fantasy team and you are not on it.")

    async def on_call_tool(self, context: MiddlewareContext, call_next: Any) -> Any:
        self._check()
        return await call_next(context)

    async def on_list_tools(self, context: MiddlewareContext, call_next: Any) -> Any:
        self._check()
        return await call_next(context)


def github_auth(config: Settings) -> GitHubProvider:
    """The OAuth provider, or a clear error saying exactly what is missing."""
    missing = [
        name
        for name, value in (
            ("FF_GITHUB_CLIENT_ID", config.github_client_id),
            ("FF_GITHUB_CLIENT_SECRET", config.github_client_secret),
            ("FF_ALLOWED_GITHUB_LOGIN", config.allowed_github_login),
        )
        if not value
    ]
    if missing:
        raise ConfigError(
            f"MCP auth needs {', '.join(missing)}. Create a GitHub OAuth app with the "
            f"callback {config.mcp_base_url.rstrip('/')}/auth/callback and put its "
            f"credentials in .env. See docs/DEPLOY.md."
        )
    return GitHubProvider(
        client_id=config.github_client_id,
        client_secret=config.github_client_secret,
        base_url=config.mcp_base_url,
    )
