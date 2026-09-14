"""The Yahoo read path.

Everything the recommendation needs out of Yahoo: league settings, my roster for a week,
the matchup, and the free agent pool. No writes live here.

Three rules this module exists to enforce, all from CLAUDE.md 2.4:

* **Serialize every call.** Yahoo throttles per application id, not per user token, so two
  concurrent requests do not go twice as fast -- they bring the throttle on twice as fast.
  A module-level lock plus a minimum gap covers every instance in the process.
* **Check ``status_code`` before parsing.** A throttled Yahoo answers HTTP 999 with an HTML
  body. ``resp.json()`` on that raises somewhere your error handling is not.
* **Cache with the source's real cadence.** League settings change roughly never.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import httpx

from ff.adapters.yahoo.auth import YahooAuth
from ff.adapters.yahoo.parse import (
    parse_faab_balance,
    parse_game_id,
    parse_league_settings,
    parse_matchup,
    parse_players,
    parse_roster,
    parse_transactions,
)
from ff.core.cache import DEFAULT_TTL_SECONDS, FileCache
from ff.core.errors import AuthExpired, SchemaDrift, SourceUnavailable, Throttled
from ff.core.logging import get_logger
from ff.core.retry import YAHOO_THROTTLE_STATUS, with_backoff
from ff.domain.models import LeagueSettings, Matchup, Player, Roster
from ff.domain.opponent import WinningBid

BASE = "https://fantasysports.yahooapis.com/fantasy/v2"

#: Yahoo caps a players page at 25 regardless of what you ask for.
PAGE_SIZE = 25

#: Throttling is keyed to the application id, so the lock is process-wide rather than
#: per-client. One Yahoo request at a time, always.
_YAHOO_LOCK = threading.Lock()

log = get_logger(__name__)


class YahooClient:
    """Reads from Yahoo. One request at a time, cached, and never parsed before checked."""

    def __init__(
        self,
        auth: YahooAuth,
        cache: FileCache,
        *,
        league_id: str = "",
        team_key: str = "",
        client: httpx.Client | None = None,
        min_interval_s: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.auth = auth
        self.cache = cache
        self.client = client or httpx.Client(timeout=30.0)
        self.min_interval_s = min_interval_s
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_call_at = 0.0
        self._game_id: str | None = None
        self._configured_league = league_id
        self._configured_team = team_key

    # ---- transport ---------------------------------------------------------------

    def _request_once(self, path: str) -> Any:
        """One GET. Serialized, rate-paced, status-checked, then parsed."""
        with _YAHOO_LOCK:
            gap = self._monotonic() - self._last_call_at
            if gap < self.min_interval_s:
                self._sleep(self.min_interval_s - gap)
            url = f"{BASE}{path}"
            separator = "&" if "?" in path else "?"
            try:
                resp = self.client.get(f"{url}{separator}format=json", headers=self.auth.headers())
            except httpx.HTTPError as exc:
                raise SourceUnavailable("yahoo", str(exc), required=True) from exc
            finally:
                self._last_call_at = self._monotonic()

        status = resp.status_code

        # Status first. A 999 body is an HTML throttle page and parsing it raises outside
        # any handler that expects a JSON error.
        if status == YAHOO_THROTTLE_STATUS:
            raise Throttled("yahoo")
        if status in (401, 403):
            self._handle_auth_failure(status, resp.text)
        if status != 200:
            raise SourceUnavailable("yahoo", f"HTTP {status} for {path}", required=True)

        try:
            return resp.json()
        except ValueError as exc:
            raise SchemaDrift(
                "yahoo", f"HTTP 200 for {path} but the body is not JSON", required=True
            ) from exc

    def _handle_auth_failure(self, status: int, body: str) -> None:
        """Refresh only what refreshing can fix.

        Exactly one 401 is recoverable: ``token_expired``. Every other one describes a
        state the caller has to go and change, and retrying it burns the backoff budget
        before reporting the wrong cause. The version of this that matched any
        ``oauth_problem`` at all retried ``additional_authorization_required`` four times
        and then announced a throttle -- while the real answer, that the app is not
        approved for the Fantasy Sports API, was sitting in the first response. (BUG-006.)
        """
        lowered = body.lower()

        if "token_expired" in lowered:
            log.info("yahoo_token_expired_on_call")
            self.auth.force_refresh()
            raise Throttled("yahoo")  # retryable: with_backoff runs the call again

        if "additional_authorization_required" in lowered:
            raise AuthExpired(
                "Yahoo accepted the token but refused the call: "
                'oauth_problem="additional_authorization_required".\n'
                "The token is fine. The app is not approved for the Fantasy Sports API.\n"
                "There is nothing to fix in this repo and nothing to tick in the developer "
                "portal: as of 2026-09-14 an app's API Permissions list offers OpenID "
                "Connect only, with no Fantasy Sports entry to select. Approval on the "
                "access application at sports.yahoo.com/developer/access is what grants "
                "it. Until that lands, this error is the expected answer -- it is a wait, "
                "not a misconfiguration. See CLAUDE.md section 6."
            )

        detail = body.strip()[:200] or f"HTTP {status} with an empty body"
        raise AuthExpired(
            f"Yahoo returned HTTP {status} and the body does not say the token expired, so "
            f"refreshing cannot fix it. Yahoo said: {detail}\n"
            f"See docs/YAHOO_SETUP.md."
        )

    def get(self, path: str, cache_key: str, ttl_s: int | None = None) -> Any:
        """Cached GET with backoff. Every read in this module goes through here."""
        cached = self.cache.get(cache_key, ttl_s)
        if cached is not None:
            return cached
        # The retry sleeps through the same injected clock as the pacing, so a test can
        # prove the backoff happened without waiting for it.
        payload = with_backoff(lambda: self._request_once(path), source="yahoo", sleep=self._sleep)
        self.cache.set(cache_key, payload)
        return payload

    # ---- keys --------------------------------------------------------------------

    def game_id(self) -> str:
        """The current NFL game id, from Yahoo, at runtime.

        Hardcoding it is the single most common way a Yahoo integration breaks in
        September: 461 was 2025, and a stale id fails in ways that look like an auth bug.
        """
        if self._game_id is None:
            payload = self.get("/game/nfl", "yahoo_settings:game_nfl")
            self._game_id = parse_game_id(payload)
            log.info("yahoo_game_id", game_id=self._game_id)
        return self._game_id

    def league_key(self) -> str:
        """Accepts a full key (461.l.1000) or a bare league id (1000).

        A bare id is the better thing to configure, because it carries no season in it and
        so cannot go stale when the game id rolls over.
        """
        configured = self._configured_league
        if not configured:
            raise SourceUnavailable("yahoo", "FF_YAHOO_LEAGUE_KEY is not set", required=True)
        if ".l." in configured:
            return configured
        return f"{self.game_id()}.l.{configured}"

    def team_key(self) -> str:
        """Accepts a full key (470.l.1000.t.3) or a bare team number (3).

        Same reason as ``league_key``: a bare number carries no season. Until this existed
        the team key was the one setting that forced a hardcoded game id, which
        docs/YAHOO_SETUP.md tells you never to do -- and the game id is the one part of the
        key you cannot read off your own team page, so the documented way to obtain it was
        ``make leagues``, which needs the API access this is all waiting on.

        Composing a bare number calls ``league_key``, so it can resolve the game id on
        first use. That is one cached request, the same one ``league_key`` already makes.
        """
        configured = self._configured_team
        if not configured:
            return ""
        if ".t." in configured:
            return configured
        return f"{self.league_key()}.t.{configured}"

    # ---- reads -------------------------------------------------------------------

    def league_settings(self) -> LeagueSettings:
        """Roster slots, scoring and whether the league uses FAAB. Cached for a week."""
        key = self.league_key()
        payload = self.get(
            f"/league/{key}/settings",
            f"yahoo_settings:{key}",
            DEFAULT_TTL_SECONDS["yahoo_settings"],
        )
        return parse_league_settings(payload)

    def roster(self, week: int, team_key: str | None = None) -> Roster:
        """My roster for a week. NFL is addressed by week, never by date."""
        team = team_key or self.team_key()
        if not team:
            raise SourceUnavailable("yahoo", "FF_YAHOO_TEAM_KEY is not set", required=True)
        payload = self.get(
            f"/team/{team}/roster;week={week}",
            f"yahoo_roster:{team}:{week}",
            DEFAULT_TTL_SECONDS["yahoo_roster"],
        )
        return parse_roster(payload, week)

    def matchup(self, week: int, team_key: str | None = None) -> Matchup:
        """Who I play this week and who they start.

        Two calls, not one. ``/matchups`` names the opponent and carries their score, but
        it does not carry their roster -- so the starters come from a second, serialized
        roster read against the opponent's team key. The margin distribution needs their
        actual starters, and guessing them from a projection ranking would be a different
        (and worse) product.
        """
        team = team_key or self.team_key()
        if not team:
            raise SourceUnavailable("yahoo", "FF_YAHOO_TEAM_KEY is not set", required=True)
        payload = self.get(
            f"/team/{team}/matchups;weeks={week}",
            f"yahoo_roster:matchup:{team}:{week}",
            DEFAULT_TTL_SECONDS["yahoo_roster"],
        )
        found = parse_matchup(payload, team, week)
        if found.opponent_starters:
            return found
        opponent = self.roster(week, str(found.opponent))
        starters = tuple(spot.player.id for spot in opponent.starters)
        return replace(found, opponent_starters=starters)

    def free_agents(
        self, position: str | None = None, limit: int = 50, status: str = "FA"
    ) -> list[Player]:
        """The free agent pool, sorted by Yahoo's points ranking.

        Yahoo caps a page at 25 whatever you ask for, so this pages with ``start``. Pages
        are fetched one at a time -- see the note at the top of this module about why they
        are not fetched in parallel.
        """
        key = self.league_key()
        out: list[Player] = []
        for start in range(0, limit, PAGE_SIZE):
            filters = f";start={start};count={PAGE_SIZE};status={status};sort=PTS"
            if position:
                filters += f";position={position}"
            payload = self.get(
                f"/league/{key}/players{filters}",
                f"yahoo_freeagents:{key}:{status}:{position or 'ALL'}:{start}",
                DEFAULT_TTL_SECONDS["yahoo_freeagents"],
            )
            page = parse_players(payload)
            out.extend(page)
            if len(page) < PAGE_SIZE:
                break
        return out[:limit]

    def faab_balance(self, team_key: str | None = None) -> int:
        """Remaining FAAB budget, live from Yahoo.

        Deliberately uncached beyond a short TTL and re-read immediately before every
        submission (CLAUDE.md 5.2). The owner bids from the Yahoo app sometimes, so any
        value older than a few minutes may already be wrong.
        """
        team = team_key or self.team_key()
        if not team:
            raise SourceUnavailable("yahoo", "FF_YAHOO_TEAM_KEY is not set", required=True)
        payload = self.get(
            f"/team/{team}",
            f"yahoo_faab:{team}",
            DEFAULT_TTL_SECONDS["yahoo_faab"],
        )
        return parse_faab_balance(payload)

    def transactions(self, limit: int = 100) -> list[WinningBid]:
        """Completed league transactions, newest first, paged.

        The opponent model reads this. It yields winning bids only -- see
        domain/opponent.py for why that bounds what can be inferred.
        """
        key = self.league_key()
        out: list[WinningBid] = []
        for start in range(0, limit, PAGE_SIZE):
            payload = self.get(
                f"/league/{key}/transactions;start={start};count={PAGE_SIZE};type=add",
                f"yahoo_transactions:{key}:{start}",
                DEFAULT_TTL_SECONDS["yahoo_transactions"],
            )
            page = parse_transactions(payload)
            out.extend(page)
            if len(page) < PAGE_SIZE:
                break
        return out[:limit]
