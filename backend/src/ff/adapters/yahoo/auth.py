"""Yahoo OAuth2: hold a token, refresh it before it dies, never log it.

Two things here are easy to get wrong and both are handled deliberately:

1. **Refresh proactively at 55 minutes, not reactively on 401.** The token lives 3600
   seconds. Waiting for a 401 means the failure lands on the one request that mattered --
   an 11:58 Sunday lineup submission -- and costs a round trip you do not have.
2. **``redirect_uri`` is required on the refresh call too**, and must match the authorize
   call exactly. Yahoo's most common integration bug. See the yahoo-fantasy-api skill.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from ff.core.clock import Clock, SystemClock
from ff.core.errors import AuthExpired, ConfigError
from ff.core.logging import get_logger

TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"

#: Yahoo access tokens live 3600s. Refresh once the current one is 55 minutes old, so a
#: request never has to discover the expiry for us.
REFRESH_AT_AGE_S = 55 * 60

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class YahooToken:
    access_token: str
    refresh_token: str
    obtained_at_epoch: float
    expires_in_s: int = 3600

    @property
    def expires_at_epoch(self) -> float:
        return self.obtained_at_epoch + self.expires_in_s

    def needs_refresh(self, now_epoch: float) -> bool:
        """True once the token is older than the proactive window, or already dead.

        A token file with no recorded obtain time reads as age 0 from an epoch of 0, which
        is far past the window, so an unknown-age token refreshes rather than being trusted.
        """
        return now_epoch - self.obtained_at_epoch >= min(REFRESH_AT_AGE_S, self.expires_in_s)

    def to_json(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "obtained_at_epoch": self.obtained_at_epoch,
            "expires_in": self.expires_in_s,
        }


def token_from_response(payload: dict[str, Any], obtained_at_epoch: float) -> YahooToken:
    """Build a token from Yahoo's JSON. Missing fields are a hard error, not a default."""
    try:
        return YahooToken(
            access_token=str(payload["access_token"]),
            refresh_token=str(payload["refresh_token"]),
            obtained_at_epoch=obtained_at_epoch,
            expires_in_s=int(payload.get("expires_in", 3600)),
        )
    except KeyError as exc:
        raise AuthExpired(f"Yahoo token response has no {exc}. Run 'make auth' again.") from exc


class TokenStore:
    """The token on disk, mode 0600. Nothing here is ever logged."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> YahooToken:
        if not self.path.exists():
            raise ConfigError(
                f"No Yahoo token at {self.path}. Run 'make auth' first -- see docs/YAHOO_SETUP.md."
            )
        try:
            raw: dict[str, Any] = json.loads(self.path.read_text())
        except json.JSONDecodeError as exc:
            raise ConfigError(
                f"Yahoo token at {self.path} is not valid JSON. Re-run 'make auth'."
            ) from exc
        # obtained_at_epoch defaults to 0 on purpose: an age we cannot establish must read
        # as stale, never as fresh.
        return YahooToken(
            access_token=str(raw.get("access_token", "")),
            refresh_token=str(raw.get("refresh_token", "")),
            obtained_at_epoch=float(raw.get("obtained_at_epoch", 0.0)),
            expires_in_s=int(raw.get("expires_in", 3600)),
        )

    def save(self, token: YahooToken) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(token.to_json(), indent=2))
        self.path.chmod(0o600)


def exchange_code(
    code: str,
    *,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    now_epoch: float,
    client: httpx.Client | None = None,
) -> YahooToken:
    """Swap the authorization code for a token. Used once, by scripts/yahoo_auth.py."""
    return _post_token(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
        client_id=client_id,
        client_secret=client_secret,
        now_epoch=now_epoch,
        client=client,
    )


def refresh_token(
    token: YahooToken,
    *,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    now_epoch: float,
    client: httpx.Client | None = None,
) -> YahooToken:
    """Trade the refresh token for a new access token.

    ``redirect_uri`` is required here even though nothing is being redirected, and it must
    match the one used to authorize. Omitting it is the most common Yahoo OAuth bug.
    """
    fresh = _post_token(
        {
            "grant_type": "refresh_token",
            "refresh_token": token.refresh_token,
            "redirect_uri": redirect_uri,
        },
        client_id=client_id,
        client_secret=client_secret,
        now_epoch=now_epoch,
        client=client,
    )
    # Yahoo usually returns the same refresh token; if it omits one, keep the old.
    if not fresh.refresh_token:
        fresh = YahooToken(
            access_token=fresh.access_token,
            refresh_token=token.refresh_token,
            obtained_at_epoch=fresh.obtained_at_epoch,
            expires_in_s=fresh.expires_in_s,
        )
    return fresh


def _post_token(
    form: dict[str, str],
    *,
    client_id: str,
    client_secret: str,
    now_epoch: float,
    client: httpx.Client | None,
) -> YahooToken:
    http = client or httpx.Client(timeout=30.0)
    resp = http.post(
        TOKEN_URL,
        data={**form, "client_id": client_id, "client_secret": client_secret},
        auth=(client_id, client_secret),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    # Status before parsing, always. A failed token call can answer with an HTML error page.
    if resp.status_code != 200:
        raise AuthExpired(
            f"Yahoo token endpoint returned HTTP {resp.status_code}. "
            f"If this says invalid_grant the refresh token is dead -- run 'make auth' again. "
            f"If it says redirect_uri mismatch, FF_YAHOO_REDIRECT_URI does not match the "
            f"value registered on the app."
        )
    return token_from_response(resp.json(), now_epoch)


class YahooAuth:
    """Hands out a live access token. The only place Yahoo credentials are used."""

    def __init__(
        self,
        store: TokenStore,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        clock: Clock | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        if not client_id or not client_secret:
            raise ConfigError(
                "FF_YAHOO_CLIENT_ID and FF_YAHOO_CLIENT_SECRET must be set. "
                "See docs/YAHOO_SETUP.md."
            )
        self.store = store
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.clock = clock or SystemClock()
        self.client = client
        self._token: YahooToken | None = None

    def access_token(self) -> str:
        """A token with at least five minutes of life left in it."""
        now = self.clock.now().timestamp()
        token = self._token or self.store.load()
        if token.needs_refresh(now):
            log.info("yahoo_token_refresh", age_s=round(now - token.obtained_at_epoch))
            token = refresh_token(
                token,
                client_id=self.client_id,
                client_secret=self.client_secret,
                redirect_uri=self.redirect_uri,
                now_epoch=now,
                client=self.client,
            )
            self.store.save(token)
        self._token = token
        return token.access_token

    def force_refresh(self) -> str:
        """Refresh now, whatever the age. Used once after a 401 that says token_expired."""
        now = self.clock.now().timestamp()
        token = refresh_token(
            self._token or self.store.load(),
            client_id=self.client_id,
            client_secret=self.client_secret,
            redirect_uri=self.redirect_uri,
            now_epoch=now,
            client=self.client,
        )
        self.store.save(token)
        self._token = token
        return token.access_token

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token()}"}


READ_SCOPE = "fspt-r"
WRITE_SCOPE = "fspt-w"
SCOPES = (READ_SCOPE, WRITE_SCOPE)


def authorize_url(client_id: str, redirect_uri: str, scope: str | None = None) -> str:
    """The consent URL. **Send no scope parameter.**

    This contradicts every Yahoo integration guide, including this project's own skill, so
    here is the probe that settled it (2026-09-13, against a real client id, reading the
    302 Location off ``request_auth`` without following it):

    * ``scope=fspt-r``  -> 302 ``error=invalid_scope``
    * ``scope=fspt-w``  -> 302 ``error=invalid_scope``
    * no scope at all   -> 302 to the login page, handshake proceeds

    Yahoo derives permissions from the app's own configuration in the developer portal --
    the Fantasy Sports permission and its Read vs Read/Write setting. Passing an explicit
    scope string is not how you ask for access; it is how you fail the handshake. So the
    default is to omit it, and the knob survives only to re-test the documented behaviour
    if Yahoo ever restores it. (BUG-004.)
    """
    from urllib.parse import urlencode

    params: dict[str, str] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
    }
    if scope:
        if scope not in SCOPES:
            raise ConfigError(
                f"Unknown Yahoo scope {scope!r}. Use {READ_SCOPE!r}, {WRITE_SCOPE!r}, or -- "
                f"what actually works -- leave it unset so Yahoo uses the app's own "
                f"permissions."
            )
        params["scope"] = scope
    return f"{AUTH_URL}?{urlencode(params)}"


@dataclass(frozen=True, slots=True)
class CallbackResult:
    """What came back on the redirect: a code, a refusal, or neither.

    This lives here rather than in the script because it is the part worth testing and
    scripts/ is not importable. Yahoo puts its refusals in the query string, so a listener
    that only looks for ``code`` cannot tell "denied" from "still waiting" -- it reports a
    guess and then hangs forever on a code that is never coming. (BUG-005.)
    """

    code: str | None = None
    error: str | None = None
    description: str | None = None

    @property
    def is_final(self) -> bool:
        """Whether this settles the handshake. A stray favicon request does not."""
        return self.code is not None or self.error is not None

    @property
    def message(self) -> str:
        if self.code is not None:
            return "Authorized. You can close this tab."
        if self.error is None:
            return "Not the authorization callback. Still waiting."
        if self.error == "invalid_scope":
            return (
                "Yahoo refused the requested scope (invalid_scope).\n\n"
                "Yahoo rejects every explicit fspt-* scope string and takes permissions "
                "from your app's own settings in the developer portal instead. Probed "
                "2026-09-13: fspt-r and fspt-w are both refused, and sending no scope at "
                "all works.\n\n"
                "Fix: leave FF_YAHOO_SCOPE unset -- that is the default now -- and run "
                "`make auth` again. If it still fails, the app itself has no Fantasy Sports "
                "permission: open it at developer.yahoo.com/apps, tick Fantasy Sports under "
                "API Permissions, save, and retry."
            )
        detail = f": {self.description}" if self.description else ""
        return (
            f"Yahoo refused the authorization ({self.error}{detail}).\n\n"
            f"Nothing was saved. See docs/YAHOO_SETUP.md."
        )


def parse_callback(query: str) -> CallbackResult:
    """Read the redirect query string. Never raises -- the browser gets the message."""
    from urllib.parse import parse_qs

    params = parse_qs(query)
    code = params.get("code", [None])[0]
    error = params.get("error", [None])[0]
    description = params.get("error_description", [None])[0]
    return CallbackResult(code=code, error=error, description=description)
