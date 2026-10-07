"""Firecrawl's fantasy board: six more forecasters, read from one page.

https://www.firecrawl.dev/alexandria/fantasy collects weekly projections from Draft Sharks,
CBS Sports, FFToday, Fantasy Football Calculator, StartWho and FantasyData, and prints every
site's number next to every player. Free, no key. Probed 2026-10-06.

**Six sources, not one.** Each site is its own ``ProjectionSource`` (``FirecrawlSite``), so
the blend averages eight forecasters rather than ESPN, Sleeper and one pre-averaged
number. Two reasons. The spread between sources is the epistemic uncertainty, and
averaging six forecasts before the blend sees them would hide most of it. And calibration
grades each source on its own (CLAUDE.md 2.5), so after a few weeks this app can say which
of the six is worth listening to. One fetch serves all six: ``FirecrawlBoard`` holds the
page, the sites read from it.

**There is no feed.** The numbers are inside the Next.js flight payload
(``self.__next_f.push``) of a public page. So this adapter is fragile by nature, and it is
treated the way ESPN is: optional, validated on every fetch, and it says what drifted.

What the probe found, all `[empirical]`, 2026-10-06:

* ``?position=`` takes QB, RB, WR, TE, K and DST. An unknown value silently serves QBs, so
  every fetch checks that the rows are the position it asked for.
* ``?scoring=std`` serves standard scoring and ``?scoring=ppr`` full PPR. ``half_ppr`` and
  ``standard`` are silently ignored and serve PPR. Half-PPR is therefore computed as the
  mean of the PPR and standard numbers, which is exact: the two formats differ only by
  points per catch.
* The page is for one week, named in the title, and lags: on Tuesday 2026-10-06 morning it
  still showed week 4. A page for the wrong week is refused rather than read.
* The ``matchup`` grades are not used. On 2026-10-06 they named a different team than the
  listed opponent in 28 of 30 quarterback rows; by evening it was 40 of 368 rows across
  positions. Better, still wrong often enough that nothing here reads them.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from ff.adapters._common import Scoring, canonical_team, match_key, parse_position
from ff.core.cache import DEFAULT_TTL_SECONDS, FileCache
from ff.core.errors import SchemaDrift, SourceUnavailable
from ff.core.logging import get_logger
from ff.domain.models import Player, PlayerId, Position, SourceStatus

URL = "https://www.firecrawl.dev/alexandria/fantasy"

#: The site keys the page uses, and the short name each one gets as a source.
SITES: dict[str, str] = {
    "draftsharks-com": "draftsharks",
    "cbssports-com": "cbssports",
    "fftoday-com": "fftoday",
    "fantasyfootballcalculator-com": "ffcalculator",
    "startwho-com": "startwho",
    "fantasydata-com": "fantasydata",
}

#: The page's spelling of each position. Team defense is DST there.
PAGE_POSITIONS: dict[Position, str] = {
    Position.QB: "QB",
    Position.RB: "RB",
    Position.WR: "WR",
    Position.TE: "TE",
    Position.K: "K",
    Position.DEF: "DST",
}

#: Positions whose points depend on catches, so whose standard numbers differ from PPR.
RECEIVING_POSITIONS = (Position.QB, Position.RB, Position.WR, Position.TE)

#: Fewer rows than this means the page parsed into something that is not the board.
MINIMUM_ROWS = 10

_PUSH = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)')
_TITLE_WEEK = re.compile(r"<title>[^<]*?\bWeek (\d+)\b")
_ROW_START = '{"key":"'

log = get_logger(__name__)

#: site -> match key -> points, for one week.
SitePoints = dict[str, dict[str, float]]


class FirecrawlBoard:
    """One fetch of the page per position and format, shared by the six site sources."""

    name = "firecrawl"

    def __init__(
        self,
        cache: FileCache,
        *,
        scoring: Scoring = "ppr",
        client: httpx.Client | None = None,
    ) -> None:
        self.cache = cache
        self.scoring = scoring
        self.client = client or httpx.Client(
            timeout=30.0, follow_redirects=True, headers={"User-Agent": "ff-copilot/1.0"}
        )
        self._memo: tuple[int, float, SitePoints] | None = None
        self.last_error: str | None = None

    # ---- fetch -------------------------------------------------------------------

    def _page(self, position: Position, variant: str) -> dict[str, Any]:
        """One position in one format: ``{"week": n, "rows": [...]}``, cached."""
        page_position = PAGE_POSITIONS[position]
        key = f"firecrawl:{page_position}:{variant}"
        cached = self.cache.get(key, DEFAULT_TTL_SECONDS["firecrawl"])
        if cached is not None:
            return dict(cached)
        url = f"{URL}?position={page_position}&scoring={variant}"
        try:
            resp = self.client.get(url)
        except httpx.HTTPError as exc:
            raise SourceUnavailable("firecrawl", str(exc), required=False) from exc
        # Status before parsing. Always. See CLAUDE.md 2.4.
        if resp.status_code != 200:
            raise SourceUnavailable("firecrawl", f"HTTP {resp.status_code}", required=False)
        page = parse_page(resp.text, position)
        self.cache.set(key, page)
        return page

    def site_points(self, week: int) -> SitePoints:
        """Every site's projection for every player on the board, in the league's format.

        Held in memory for the cache lifetime, because six sources ask for the same thing
        within a second of each other.
        """
        ttl = DEFAULT_TTL_SECONDS["firecrawl"]
        if self._memo and self._memo[0] == week and time.monotonic() - self._memo[1] < ttl:
            return self._memo[2]
        try:
            out = self._collect(week)
        except (SourceUnavailable, SchemaDrift) as exc:
            self.last_error = str(exc)
            log.warning("firecrawl_unavailable", detail=str(exc))
            raise
        self.last_error = None
        self._memo = (week, time.monotonic(), out)
        log.info("firecrawl_board", week=week, players=len(out.get("draftsharks", {})))
        return out

    def _collect(self, week: int) -> SitePoints:
        out: SitePoints = {name: {} for name in SITES.values()}
        for position in PAGE_POSITIONS:
            ppr = self._page(position, "ppr")
            if ppr["week"] != week:
                raise SourceUnavailable(
                    "firecrawl",
                    f"the page is for week {ppr['week']}, not week {week}. It usually "
                    f"catches up midweek.",
                    required=False,
                )
            std = None
            if self.scoring != "ppr" and position in RECEIVING_POSITIONS:
                std = self._page(position, "std")
                check_formats(ppr["rows"], std["rows"], position)
            std_rows = std["rows"] if std is not None else None
            for site, points in scored(ppr["rows"], std_rows, self.scoring).items():
                out.setdefault(site, {}).update(points)
        return out

    def status(self) -> SourceStatus:
        return SourceStatus(
            name=self.name,
            ok=self.last_error is None,
            required=False,
            age_seconds=self.cache.age_seconds("firecrawl:QB:ppr"),
            detail=self.last_error,
        )


class FirecrawlSite:
    """One of the six sites on the board, as a ``ProjectionSource`` of its own."""

    required = False

    def __init__(self, board: FirecrawlBoard, site: str) -> None:
        self.board = board
        self.site = site
        self.name = site

    def weekly(self, week: int, players: list[Player]) -> dict[PlayerId, float]:
        by_key = self.board.site_points(week).get(self.site, {})
        out: dict[PlayerId, float] = {}
        for player in players:
            value = by_key.get(match_key(player.name, player.position, player.team))
            if value is not None:
                out[player.id] = value
        return out

    def status(self) -> SourceStatus:
        board = self.board.status()
        return SourceStatus(
            name=self.name,
            ok=board.ok,
            required=False,
            age_seconds=board.age_seconds,
            detail=board.detail,
        )


def firecrawl_sources(board: FirecrawlBoard) -> list[FirecrawlSite]:
    return [FirecrawlSite(board, name) for name in SITES.values()]


# ---- parsing ----------------------------------------------------------------------


def flight_text(html: str) -> str:
    """The Next.js flight payload, unescaped and joined. The board rows live in it."""
    return "".join(json.loads(f'"{chunk}"') for chunk in _PUSH.findall(html))


def page_week(html: str) -> int:
    found = _TITLE_WEEK.search(html)
    if not found:
        raise SchemaDrift("firecrawl", "the page title names no week", required=False)
    return int(found.group(1))


def parse_page(html: str, position: Position) -> dict[str, Any]:
    """The board for one position, reduced to what this app reads.

    Raises ``SchemaDrift`` if the page has no week, too few rows, or rows for a different
    position than the one asked for -- the last is what an ignored ``?position=`` looks
    like.
    """
    week = page_week(html)
    rows = board_rows(flight_text(html))
    expected = PAGE_POSITIONS[position]
    wrong = [r["key"] for r in rows if not str(r.get("key", "")).endswith(f":{expected}")]
    if wrong:
        raise SchemaDrift(
            "firecrawl",
            f"asked for {expected} and got {len(wrong)} rows of another position "
            f"(first: {wrong[0]}). The position parameter was ignored.",
            required=False,
        )
    if len(rows) < MINIMUM_ROWS:
        raise SchemaDrift(
            "firecrawl", f"only {len(rows)} {expected} rows on the page", required=False
        )
    unknown = sorted({site for r in rows for site in r["points"]} - set(SITES))
    if unknown:
        log.warning("firecrawl_unknown_sites", sites=unknown)
    slim = [
        {
            "name": r["name"],
            "team": r.get("team"),
            "position": position.value,
            "points": {
                SITES[site]: float(value)
                for site, value in r["points"].items()
                if site in SITES and isinstance(value, int | float)
            },
        }
        for r in rows
    ]
    return {"week": week, "rows": slim}


def board_rows(text: str) -> list[dict[str, Any]]:
    """Every board row in the flight text, de-duplicated on its key.

    Decoded with the JSON decoder from each row's opening brace rather than with a regex,
    because the rows nest objects and a regex that balances braces is a parser written
    badly.
    """
    decoder = json.JSONDecoder()
    rows: dict[str, dict[str, Any]] = {}
    at = text.find(_ROW_START)
    while at != -1:
        try:
            obj, end = decoder.raw_decode(text, at)
        except ValueError:
            at = text.find(_ROW_START, at + 1)
            continue
        if isinstance(obj, dict) and isinstance(obj.get("points"), dict) and obj.get("name"):
            rows.setdefault(str(obj["key"]), obj)
            at = text.find(_ROW_START, end)
        else:
            at = text.find(_ROW_START, at + 1)
    return list(rows.values())


def check_formats(ppr: list[dict[str, Any]], std: list[dict[str, Any]], position: Position) -> None:
    """Refuse a standard page that is really PPR.

    Wide receivers and tight ends catch passes, so their standard numbers sit below PPR
    nearly everywhere. If not one does, the scoring parameter was ignored -- which is
    exactly what ``half_ppr`` and ``standard`` do -- and averaging would yield PPR labelled
    as half-PPR.
    """
    if position not in (Position.WR, Position.TE):
        return
    std_by_name = {r["name"]: r["points"] for r in std}
    lower = sum(
        1
        for r in ppr
        for site, value in r["points"].items()
        if site in std_by_name.get(r["name"], {}) and std_by_name[r["name"]][site] < value
    )
    if lower == 0:
        raise SchemaDrift(
            "firecrawl",
            f"the standard-scoring {position.value} page matches PPR exactly; the scoring "
            f"parameter was ignored",
            required=False,
        )


def scored(
    ppr: list[dict[str, Any]], std: list[dict[str, Any]] | None, scoring: Scoring
) -> SitePoints:
    """Per-site points in the league's format, keyed for joining.

    Half-PPR is the mean of PPR and standard, per site, and only where both exist: a
    site that published one format and not the other gets no half-PPR number rather than
    one format passed off as the other.
    """
    std_points = {r["name"]: r["points"] for r in std or []}
    out: SitePoints = {}
    for row in ppr:
        position = parse_position(row["position"])
        if position is None:
            continue
        key = match_key(row["name"], position, canonical_team(row.get("team")))
        for site, value in row["points"].items():
            if std is None or scoring == "ppr":
                points: float | None = value
            else:
                other = std_points.get(row["name"], {}).get(site)
                if other is None:
                    points = None
                elif scoring == "std":
                    points = other
                else:
                    points = (value + other) / 2.0
            if points is not None:
                out.setdefault(site, {})[key] = round(points, 2)
    return out
