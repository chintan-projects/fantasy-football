"""The Yahoo transport: status before parse, one call at a time, cache, token recovery.

These are the behaviours that decide whether the app works at 11:58 on a Sunday, so they
are tested against the failures Yahoo actually produces rather than against happy paths.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from ff.adapters.yahoo.auth import TokenStore, YahooAuth, YahooToken
from ff.adapters.yahoo.client import PAGE_SIZE, YahooClient
from ff.core.cache import FileCache
from ff.core.clock import FrozenClock
from ff.core.errors import AuthExpired, SchemaDrift, SourceUnavailable
from ff.domain.models import Slot

FIXTURES = Path(__file__).parent / "fixtures" / "yahoo"
T0 = datetime(2026, 9, 15, 16, 0, 0, tzinfo=UTC)


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


class Recorder:
    """Records every request and answers from a routing table."""

    def __init__(self, routes: list[tuple[str, httpx.Response]]) -> None:
        self.routes = routes
        self.paths: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(str(request.url))
        for fragment, response in self.routes:
            if fragment in str(request.url):
                return response
        return httpx.Response(404, text="no route")


def build(
    tmp_path: Path,
    routes: list[tuple[str, httpx.Response]],
    *,
    league_id: str = "1000",
    team_key: str = "470.l.1000.t.6",
) -> tuple[YahooClient, Recorder, list[float]]:
    store = TokenStore(tmp_path / "token.json")
    store.save(YahooToken("access-1", "refresh-1", T0.timestamp(), 3600))
    auth = YahooAuth(
        store,
        client_id="cid",
        client_secret="sec",
        redirect_uri="https://localhost:8080/callback",
        clock=FrozenClock(T0),
    )
    recorder = Recorder(routes)
    slept: list[float] = []
    ticks = iter(range(0, 100_000))
    client = YahooClient(
        auth,
        FileCache(tmp_path / "cache"),
        league_id=league_id,
        team_key=team_key,
        client=httpx.Client(transport=httpx.MockTransport(recorder)),
        min_interval_s=0.0,
        sleep=slept.append,
        monotonic=lambda: float(next(ticks)),
    )
    return client, recorder, slept


def ok(payload: Any) -> httpx.Response:
    return httpx.Response(200, json=payload)


class TestTransport:
    def test_appends_format_json(self, tmp_path: Path) -> None:
        client, recorder, _ = build(tmp_path, [("/game/nfl", ok(load("game_nfl.json")))])
        client.game_id()
        assert recorder.paths[0].endswith("?format=json")

    def test_sends_the_bearer_token(self, tmp_path: Path) -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(request.headers)
            return ok(load("game_nfl.json"))

        store = TokenStore(tmp_path / "t.json")
        store.save(YahooToken("access-1", "refresh-1", T0.timestamp(), 3600))
        auth = YahooAuth(
            store,
            client_id="cid",
            client_secret="sec",
            redirect_uri="https://localhost:8080/callback",
            clock=FrozenClock(T0),
        )
        client = YahooClient(
            auth,
            FileCache(tmp_path / "c"),
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            min_interval_s=0.0,
        )
        client.game_id()
        assert seen["authorization"] == "Bearer access-1"

    def test_http_999_is_retried_then_reported(self, tmp_path: Path) -> None:
        """Yahoo signals throttling with 999 and an HTML body."""
        html = (FIXTURES / "malformed" / "throttled_999.html").read_text()
        client, recorder, slept = build(tmp_path, [("/game/nfl", httpx.Response(999, text=html))])
        with pytest.raises(SourceUnavailable, match="failed after"):
            client.game_id()
        assert len(recorder.paths) == 4  # the default attempt budget
        assert slept, "a throttle must back off before retrying"

    def test_999_body_is_never_parsed(self, tmp_path: Path) -> None:
        """The HTML body would raise inside .json() outside any handler expecting JSON."""
        html = (FIXTURES / "malformed" / "throttled_999.html").read_text()
        client, _, _ = build(tmp_path, [("/game/nfl", httpx.Response(999, text=html))])
        with pytest.raises(SourceUnavailable):
            client.game_id()
        # A JSONDecodeError escaping here would be the bug; SourceUnavailable is the fix.

    def test_backoff_is_in_seconds_not_milliseconds(self, tmp_path: Path) -> None:
        client, _, slept = build(tmp_path, [("/game/nfl", httpx.Response(999, text="x"))])
        with pytest.raises(SourceUnavailable):
            client.game_id()
        assert min(slept) >= 1.0

    def test_a_200_that_is_not_json_is_schema_drift(self, tmp_path: Path) -> None:
        client, _, _ = build(
            tmp_path, [("/game/nfl", httpx.Response(200, text="<html>hello</html>"))]
        )
        with pytest.raises(SchemaDrift, match="not JSON"):
            client.game_id()

    def test_other_statuses_report_the_code(self, tmp_path: Path) -> None:
        client, _, _ = build(tmp_path, [("/game/nfl", httpx.Response(500, text="boom"))])
        with pytest.raises(SourceUnavailable, match="HTTP 500"):
            client.game_id()

    def test_connection_errors_do_not_escape_as_httpx(self, tmp_path: Path) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("reset by peer")

        store = TokenStore(tmp_path / "t.json")
        store.save(YahooToken("a", "r", T0.timestamp(), 3600))
        auth = YahooAuth(
            store,
            client_id="cid",
            client_secret="sec",
            redirect_uri="https://localhost:8080/callback",
            clock=FrozenClock(T0),
        )
        client = YahooClient(
            auth,
            FileCache(tmp_path / "c"),
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            min_interval_s=0.0,
        )
        with pytest.raises(SourceUnavailable):
            client.game_id()


class TestAuthFailures:
    def test_expired_token_refreshes_and_the_call_is_retried(self, tmp_path: Path) -> None:
        state = {"calls": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if "get_token" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "access_token": "access-2",
                        "refresh_token": "refresh-1",
                        "expires_in": 3600,
                    },
                )
            state["calls"] += 1
            if state["calls"] == 1:
                return httpx.Response(401, json={"error": {"description": "token_expired"}})
            return ok(load("game_nfl.json"))

        store = TokenStore(tmp_path / "t.json")
        store.save(YahooToken("access-1", "refresh-1", T0.timestamp(), 3600))
        transport = httpx.Client(transport=httpx.MockTransport(handler))
        auth = YahooAuth(
            store,
            client_id="cid",
            client_secret="sec",
            redirect_uri="https://localhost:8080/callback",
            clock=FrozenClock(T0),
            client=transport,
        )
        client = YahooClient(
            auth,
            FileCache(tmp_path / "c"),
            client=transport,
            min_interval_s=0.0,
            sleep=lambda _: None,
        )
        assert client.game_id() == "470"
        assert store.load().access_token == "access-2"

    def test_a_scope_problem_is_not_retried(self, tmp_path: Path) -> None:
        """A read-only token does not become writable by asking again."""
        client, recorder, _ = build(
            tmp_path,
            [("/game/nfl", httpx.Response(401, text="Please provide valid credentials"))],
        )
        with pytest.raises(AuthExpired, match="refreshing cannot fix it"):
            client.game_id()
        assert len(recorder.paths) == 1

    def test_an_unrecognised_401_repeats_what_yahoo_said(self, tmp_path: Path) -> None:
        """Guessing at the cause is how BUG-006 stayed invisible. Quote the source."""
        client, _, _ = build(
            tmp_path,
            [("/game/nfl", httpx.Response(401, text="Please provide valid credentials"))],
        )
        with pytest.raises(AuthExpired, match="Please provide valid credentials"):
            client.game_id()

    def test_an_unauthorized_app_is_not_retried(self, tmp_path: Path) -> None:
        """BUG-006. Yahoo's answer when the app lacks Fantasy Sports API access.

        The token is valid and freshly minted; the *app* is not approved. Refreshing is
        hopeless by construction, so retrying four times and then reporting a throttle --
        which is what used to happen -- burned 25 seconds and named the wrong cause.
        """
        body = (
            '{"error":{"description":"Please provide valid credentials. OAuth '
            'oauth_problem="additional_authorization_required", realm="yahooapis.com""}}'
        )
        client, recorder, _ = build(tmp_path, [("/game/nfl", httpx.Response(401, text=body))])
        with pytest.raises(AuthExpired, match="additional_authorization_required"):
            client.game_id()
        assert len(recorder.paths) == 1

    def test_the_unauthorized_message_says_what_to_do(self, tmp_path: Path) -> None:
        body = 'oauth_problem="additional_authorization_required"'
        client, _, _ = build(tmp_path, [("/game/nfl", httpx.Response(401, text=body))])
        with pytest.raises(AuthExpired) as caught:
            client.game_id()
        message = str(caught.value)
        assert "API Permissions" in message
        assert "sports.yahoo.com/developer/access" in message

    def test_an_unknown_oauth_problem_is_not_retried_either(self, tmp_path: Path) -> None:
        """Only token_expired is recoverable by refreshing. Everything else is a state
        the caller has to fix, and retrying just delays them hearing about it."""
        client, recorder, _ = build(
            tmp_path,
            [("/game/nfl", httpx.Response(401, text='oauth_problem="consumer_key_rejected"'))],
        )
        with pytest.raises(AuthExpired, match="consumer_key_rejected"):
            client.game_id()
        assert len(recorder.paths) == 1


class TestSerializationAndCache:
    def test_calls_are_paced_apart(self, tmp_path: Path) -> None:
        """Throttling is keyed to the app id, so two calls in flight is twice the risk."""
        store = TokenStore(tmp_path / "t.json")
        store.save(YahooToken("a", "r", T0.timestamp(), 3600))
        auth = YahooAuth(
            store,
            client_id="cid",
            client_secret="sec",
            redirect_uri="https://localhost:8080/callback",
            clock=FrozenClock(T0),
        )
        recorder = Recorder(
            [
                ("/game/nfl", ok(load("game_nfl.json"))),
                ("/settings", ok(load("league_settings.json"))),
            ]
        )
        slept: list[float] = []
        client = YahooClient(
            auth,
            FileCache(tmp_path / "c"),
            league_id="1000",
            client=httpx.Client(transport=httpx.MockTransport(recorder)),
            min_interval_s=1.0,
            sleep=slept.append,
            monotonic=lambda: 100.0,  # no time passes between calls
        )
        client.league_settings()
        # Two requests: the game id lookup, then settings. The first needs no pacing
        # because nothing preceded it; the second waits the full interval.
        assert slept == [1.0]

    def test_a_cached_response_costs_no_request(self, tmp_path: Path) -> None:
        client, recorder, _ = build(
            tmp_path,
            [
                ("/game/nfl", ok(load("game_nfl.json"))),
                ("/settings", ok(load("league_settings.json"))),
            ],
        )
        client.league_settings()
        before = len(recorder.paths)
        client.league_settings()
        assert len(recorder.paths) == before

    def test_league_settings_use_the_season_long_ttl(self, tmp_path: Path) -> None:
        """Settings change roughly never; re-reading them spends throttle budget."""
        client, _, _ = build(
            tmp_path,
            [
                ("/game/nfl", ok(load("game_nfl.json"))),
                ("/settings", ok(load("league_settings.json"))),
            ],
        )
        client.league_settings()
        cached = client.cache.get("yahoo_settings:470.l.1000", 7 * 24 * 3600)
        assert cached is not None


class TestKeys:
    def test_a_bare_league_id_is_composed_with_the_resolved_game_id(self, tmp_path: Path) -> None:
        """The game id is read from Yahoo at runtime. 461 was 2025; it is never hardcoded."""
        client, recorder, _ = build(
            tmp_path, [("/game/nfl", ok(load("game_nfl.json")))], league_id="1000"
        )
        assert client.league_key() == "470.l.1000"
        assert any("/game/nfl" in p for p in recorder.paths)

    def test_a_full_league_key_is_used_as_given(self, tmp_path: Path) -> None:
        client, recorder, _ = build(tmp_path, [], league_id="470.l.1000")
        assert client.league_key() == "470.l.1000"
        assert recorder.paths == []

    def test_the_game_id_is_looked_up_once(self, tmp_path: Path) -> None:
        client, recorder, _ = build(tmp_path, [("/game/nfl", ok(load("game_nfl.json")))])
        client.game_id()
        client.game_id()
        assert len([p for p in recorder.paths if "/game/nfl" in p]) == 1

    def test_a_missing_league_id_says_which_setting_is_missing(self, tmp_path: Path) -> None:
        client, _, _ = build(tmp_path, [], league_id="")
        with pytest.raises(SourceUnavailable, match="FF_YAHOO_LEAGUE_KEY"):
            client.league_key()

    def test_a_missing_team_key_says_which_setting_is_missing(self, tmp_path: Path) -> None:
        client, _, _ = build(tmp_path, [], team_key="")
        with pytest.raises(SourceUnavailable, match="FF_YAHOO_TEAM_KEY"):
            client.roster(week=2)

    def test_a_bare_team_number_composes_into_a_full_key(self, tmp_path: Path) -> None:
        """The game id is the one part of a team key you cannot read off your own team
        page, and the documented way to obtain it needs the API access this is waiting on.
        So a bare team number has to be enough to configure."""
        client, _, _ = build(
            tmp_path,
            [("/game/nfl", ok(load("game_nfl.json")))],
            league_id="1000",
            team_key="3",
        )
        assert client.team_key() == "470.l.1000.t.3"

    def test_a_full_team_key_is_used_as_given(self, tmp_path: Path) -> None:
        client, recorder, _ = build(tmp_path, [], team_key="461.l.1000.t.3")
        assert client.team_key() == "461.l.1000.t.3"
        assert recorder.paths == []

    def test_an_unset_team_key_stays_empty_rather_than_composing_a_plausible_one(
        self, tmp_path: Path
    ) -> None:
        """Composing "470.l.1000.t." from nothing would produce a key that is shaped like
        a real one, addresses nobody, and slips past every "is not set" check downstream."""
        client, _, _ = build(tmp_path, [], team_key="")
        assert client.team_key() == ""


class TestReads:
    def test_roster_is_addressed_by_week(self, tmp_path: Path) -> None:
        client, recorder, _ = build(
            tmp_path, [("/roster", ok(load("roster_week2.json")))], league_id="470.l.1000"
        )
        roster = client.roster(week=2)
        assert "week=2" in recorder.paths[0]
        assert len(roster.starters) == 9

    def test_matchup_reads_the_opponent_roster_for_starters(self, tmp_path: Path) -> None:
        """The matchups payload names the opponent but does not carry their lineup."""
        client, recorder, _ = build(
            tmp_path,
            [
                ("matchups", ok(load("matchup_week2.json"))),
                ("t.3/roster", ok(load("roster_week2_opponent.json"))),
            ],
            league_id="470.l.1000",
        )
        matchup = client.matchup(week=2)
        assert matchup.opponent == "470.l.1000.t.3"
        assert len(matchup.opponent_starters) == 9
        assert "470.p.40010" not in matchup.opponent_starters  # their bench RB
        assert len(recorder.paths) == 2

    def test_free_agents_stop_paging_on_a_short_page(self, tmp_path: Path) -> None:
        client, recorder, _ = build(
            tmp_path, [("/players", ok(load("free_agents.json")))], league_id="470.l.1000"
        )
        players = client.free_agents(limit=100)
        assert len(players) == 3
        assert len(recorder.paths) == 1

    def test_free_agents_page_with_start_because_yahoo_caps_at_25(self, tmp_path: Path) -> None:
        full = _page_of(PAGE_SIZE)
        short = _page_of(4)

        def handler(request: httpx.Request) -> httpx.Response:
            return ok(full if "start=0;" in str(request.url) else short)

        store = TokenStore(tmp_path / "t.json")
        store.save(YahooToken("a", "r", T0.timestamp(), 3600))
        auth = YahooAuth(
            store,
            client_id="cid",
            client_secret="sec",
            redirect_uri="https://localhost:8080/callback",
            clock=FrozenClock(T0),
        )
        client = YahooClient(
            auth,
            FileCache(tmp_path / "c"),
            league_id="470.l.1000",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            min_interval_s=0.0,
        )
        players = client.free_agents(limit=50)
        assert len(players) == 29

    def test_free_agents_filter_by_position(self, tmp_path: Path) -> None:
        client, recorder, _ = build(
            tmp_path, [("/players", ok(load("free_agents.json")))], league_id="470.l.1000"
        )
        client.free_agents(position="RB", limit=25)
        assert ";position=RB" in recorder.paths[0]
        assert ";status=FA" in recorder.paths[0]

    def test_free_agents_carry_ownership_and_flex_eligibility(self, tmp_path: Path) -> None:
        client, _, _ = build(
            tmp_path, [("/players", ok(load("free_agents.json")))], league_id="470.l.1000"
        )
        wright = client.free_agents()[0]
        assert wright.percent_rostered == 14.0
        assert Slot.FLEX in wright.eligible_slots


def _page_of(n: int) -> dict[str, Any]:
    """A free-agent page of n players, built from the recorded single-player shape."""
    template = load("free_agents.json")["fantasy_content"]["league"][1]["players"]["0"]
    players: dict[str, Any] = {}
    for i in range(n):
        entry = json.loads(json.dumps(template))
        entry["player"][0][0]["player_key"] = f"470.p.9{i:04d}"
        entry["player"][0][2]["name"]["full"] = f"Player {i}"
        players[str(i)] = entry
    players["count"] = n
    return {"fantasy_content": {"league": [{"league_key": "470.l.1000"}, {"players": players}]}}
