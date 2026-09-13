"""Token refresh has one job: never hand out a token that is about to die."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from ff.adapters.yahoo.auth import (
    REFRESH_AT_AGE_S,
    TokenStore,
    YahooAuth,
    YahooToken,
    authorize_url,
    refresh_token,
)
from ff.core.clock import FrozenClock
from ff.core.errors import AuthExpired, ConfigError

T0 = datetime(2026, 9, 13, 16, 0, 0, tzinfo=UTC)


def _token(obtained_at: float, access: str = "old-access") -> YahooToken:
    return YahooToken(
        access_token=access,
        refresh_token="refresh-1",
        obtained_at_epoch=obtained_at,
        expires_in_s=3600,
    )


def _transport(
    calls: list[httpx.Request], status: int = 200, body: dict[str, object] | None = None
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        payload = (
            body
            if body is not None
            else {
                "access_token": "new-access",
                "refresh_token": "refresh-2",
                "expires_in": 3600,
            }
        )
        return httpx.Response(status, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


class TestExpiry:
    def test_fresh_token_does_not_refresh(self) -> None:
        now = T0.timestamp()
        assert _token(now).needs_refresh(now) is False

    def test_refreshes_at_fifty_five_minutes_not_at_sixty(self) -> None:
        now = T0.timestamp()
        assert _token(now - REFRESH_AT_AGE_S + 1).needs_refresh(now) is False
        assert _token(now - REFRESH_AT_AGE_S).needs_refresh(now) is True

    def test_unknown_age_reads_as_stale(self) -> None:
        """A token file with no obtained_at must refresh, not be trusted."""
        assert _token(0.0).needs_refresh(T0.timestamp()) is True

    def test_short_lived_token_refreshes_before_its_own_expiry(self) -> None:
        """If Yahoo ever issues a token shorter than 55 minutes, honour the shorter life."""
        now = T0.timestamp()
        short = YahooToken("a", "r", now - 400, expires_in_s=300)
        assert short.needs_refresh(now) is True


class TestStore:
    def test_round_trip_records_obtained_at(self, tmp_path: Path) -> None:
        store = TokenStore(tmp_path / "yahoo.json")
        store.save(_token(1_700_000_000.0))
        assert store.load().obtained_at_epoch == 1_700_000_000.0

    def test_saved_token_is_owner_only(self, tmp_path: Path) -> None:
        path = tmp_path / "yahoo.json"
        TokenStore(path).save(_token(1.0))
        assert path.stat().st_mode & 0o077 == 0

    def test_missing_file_says_what_to_run(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="make auth"):
            TokenStore(tmp_path / "nope.json").load()

    def test_legacy_file_without_obtained_at_loads_as_stale(self, tmp_path: Path) -> None:
        path = tmp_path / "yahoo.json"
        path.write_text(json.dumps({"access_token": "a", "refresh_token": "r"}))
        assert TokenStore(path).load().needs_refresh(T0.timestamp()) is True


class TestRefresh:
    def test_refresh_sends_redirect_uri(self) -> None:
        """Yahoo requires redirect_uri on refresh. Omitting it is the classic failure."""
        calls: list[httpx.Request] = []
        refresh_token(
            _token(0.0),
            client_id="cid",
            client_secret="sec",
            redirect_uri="https://localhost:8080/callback",
            now_epoch=T0.timestamp(),
            client=_transport(calls),
        )
        body = calls[0].content.decode()
        assert "redirect_uri=https%3A%2F%2Flocalhost%3A8080%2Fcallback" in body
        assert "grant_type=refresh_token" in body

    def test_refresh_keeps_old_refresh_token_when_yahoo_omits_one(self) -> None:
        calls: list[httpx.Request] = []
        client = _transport(
            calls, body={"access_token": "new", "refresh_token": "", "expires_in": 3600}
        )
        out = refresh_token(
            _token(0.0),
            client_id="cid",
            client_secret="sec",
            redirect_uri="https://localhost:8080/callback",
            now_epoch=T0.timestamp(),
            client=client,
        )
        assert out.refresh_token == "refresh-1"

    def test_non_200_raises_auth_expired_without_parsing(self) -> None:
        """The token endpoint can answer with HTML. Check the status, never blind-parse."""

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(400, text="<html>invalid_grant</html>")

        with pytest.raises(AuthExpired, match="HTTP 400"):
            refresh_token(
                _token(0.0),
                client_id="cid",
                client_secret="sec",
                redirect_uri="https://localhost:8080/callback",
                now_epoch=T0.timestamp(),
                client=httpx.Client(transport=httpx.MockTransport(handler)),
            )


class TestYahooAuth:
    def test_fresh_token_is_served_without_a_network_call(self, tmp_path: Path) -> None:
        store = TokenStore(tmp_path / "t.json")
        store.save(_token(T0.timestamp()))
        calls: list[httpx.Request] = []
        auth = YahooAuth(
            store,
            client_id="cid",
            client_secret="sec",
            redirect_uri="https://localhost:8080/callback",
            clock=FrozenClock(T0),
            client=_transport(calls),
        )
        assert auth.access_token() == "old-access"
        assert calls == []

    def test_old_token_refreshes_and_persists(self, tmp_path: Path) -> None:
        store = TokenStore(tmp_path / "t.json")
        store.save(_token(T0.timestamp() - REFRESH_AT_AGE_S - 1))
        calls: list[httpx.Request] = []
        auth = YahooAuth(
            store,
            client_id="cid",
            client_secret="sec",
            redirect_uri="https://localhost:8080/callback",
            clock=FrozenClock(T0),
            client=_transport(calls),
        )
        assert auth.access_token() == "new-access"
        assert len(calls) == 1
        # Persisted, so the next process start does not refresh again.
        assert store.load().access_token == "new-access"

    def test_token_is_cached_across_calls(self, tmp_path: Path) -> None:
        store = TokenStore(tmp_path / "t.json")
        store.save(_token(T0.timestamp() - REFRESH_AT_AGE_S - 1))
        calls: list[httpx.Request] = []
        auth = YahooAuth(
            store,
            client_id="cid",
            client_secret="sec",
            redirect_uri="https://localhost:8080/callback",
            clock=FrozenClock(T0),
            client=_transport(calls),
        )
        auth.access_token()
        auth.access_token()
        assert len(calls) == 1

    def test_refreshes_again_once_the_new_token_ages_out(self, tmp_path: Path) -> None:
        store = TokenStore(tmp_path / "t.json")
        store.save(_token(T0.timestamp()))
        calls: list[httpx.Request] = []
        clock = FrozenClock(T0)
        auth = YahooAuth(
            store,
            client_id="cid",
            client_secret="sec",
            redirect_uri="https://localhost:8080/callback",
            clock=clock,
            client=_transport(calls),
        )
        auth.access_token()
        clock.at = T0 + timedelta(seconds=REFRESH_AT_AGE_S)
        auth.access_token()
        assert len(calls) == 1

    def test_missing_credentials_fail_at_construction(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="FF_YAHOO_CLIENT_ID"):
            YahooAuth(
                TokenStore(tmp_path / "t.json"),
                client_id="",
                client_secret="",
                redirect_uri="https://localhost:8080/callback",
            )

    def test_bearer_header(self, tmp_path: Path) -> None:
        store = TokenStore(tmp_path / "t.json")
        store.save(_token(T0.timestamp(), access="abc"))
        auth = YahooAuth(
            store,
            client_id="cid",
            client_secret="sec",
            redirect_uri="https://localhost:8080/callback",
            clock=FrozenClock(T0),
        )
        assert auth.headers() == {"Authorization": "Bearer abc"}


def test_authorize_url_requests_write_scope() -> None:
    url = authorize_url("cid", "https://localhost:8080/callback")
    assert "scope=fspt-w" in url
    assert "response_type=code" in url
