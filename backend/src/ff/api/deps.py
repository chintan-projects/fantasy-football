"""What the MCP tools need, in one object, built once.

The tools are thin by rule (CLAUDE.md 2.2) -- they unpack arguments, call a service, and
shape the answer. Everything they call lives here so a test can hand them fakes without
patching module globals.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ff.adapters.base import LeagueReader, ProjectionSource
from ff.adapters.espn import EspnProjections
from ff.adapters.sleeper import SleeperClient, SleeperProjections
from ff.adapters.store import Store
from ff.adapters.yahoo.auth import TokenStore, YahooAuth
from ff.adapters.yahoo.client import YahooClient
from ff.core.cache import FileCache
from ff.core.config import REPO_ROOT, Settings, settings


@dataclass(frozen=True, slots=True)
class Deps:
    yahoo: LeagueReader
    sources: list[ProjectionSource]
    store: Store
    sleeper: SleeperClient
    config: Settings

    @property
    def my_team_key(self) -> str:
        return self.config.yahoo_team_key


def build_deps(config: Settings | None = None) -> Deps:
    """Wire the real thing from configuration."""
    cfg = config or settings()
    cache = FileCache(REPO_ROOT / ".cache")
    season = _season_from_config(cfg)

    auth = YahooAuth(
        TokenStore(Path(cfg.yahoo_token_path)),
        client_id=cfg.yahoo_client_id,
        client_secret=cfg.yahoo_client_secret,
        redirect_uri=cfg.yahoo_redirect_uri,
    )
    yahoo = YahooClient(auth, cache, league_id=cfg.yahoo_league_key, team_key=cfg.yahoo_team_key)

    available: dict[str, ProjectionSource] = {
        "espn": EspnProjections(season, cache),
        "sleeper": SleeperProjections(season, cache),
    }
    sources = [available[name] for name in cfg.projection_sources if name in available]

    return Deps(
        yahoo=yahoo,
        sources=sources,
        store=Store(cfg.database_path),
        sleeper=SleeperClient(cache),
        config=cfg,
    )


def _season_from_config(cfg: Settings) -> int:
    """The NFL season year.

    Taken from the league key's game id era rather than from the clock, because a league
    key is stable and a date near New Year is not. Falls back to the configured season.
    """
    return cfg.season


@lru_cache(maxsize=1)
def default_deps() -> Deps:
    return build_deps()


__all__ = ["Deps", "LeagueReader", "build_deps", "default_deps"]
