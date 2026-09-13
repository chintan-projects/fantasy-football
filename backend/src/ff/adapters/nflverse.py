"""nflverse injury reports, via ``nflreadpy``.

**Use ``nflreadpy``, not ``nfl_data_py``.** The latter is archived: "No further maintenance
or updates are planned." nflreadpy returns Polars frames, not pandas.

**The docs about this dataset are wrong, so this was probed rather than read.**
docs/DATA_SOURCES.md says nflverse's own schedule page claims there is no injury data for
recent seasons; ``injuries_2026.parquet`` returns 200 and 182 rows for week 1. Two further
corrections from the same probe (2026-09-12):

* ``practice_status`` is not ``Full/Limited/DNP``. The real values are the full phrases
  "Full Participation in Practice", "Limited Participation in Practice" and
  "Did Not Participate In Practice". This module normalizes them.
* ``report_status`` is frequently null. A row exists for every player on the report,
  including ones with a practice note and no game-status designation.

**Coverage is partial and this source is optional.** 182 rows in week 1 against a typical
400-600 means a player with no row is not necessarily healthy -- he may simply not have
been reported yet. So a missing row means "nothing known", never "fit".

**What the practice label is worth is unknown.** Per the fantasy-decision-math skill, the
practice-participation-to-performance link is unquantified in any free dataset; the
snap-level participation data that would calibrate it publishes post-season only. "Friday
DNP is bad" is folk wisdom. This module reports the label and makes no claim about it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from ff.adapters._common import canonical_team, match_key, parse_position
from ff.core.cache import DEFAULT_TTL_SECONDS, FileCache
from ff.core.errors import SchemaDrift, SourceUnavailable
from ff.core.logging import get_logger
from ff.domain.models import Player, PlayerId, SourceStatus

#: Columns this adapter reads. Checked on every fetch -- nflverse is stable but its
#: upstreams are not, and a renamed column should say so rather than yield empty reports.
REQUIRED_COLUMNS = (
    "season",
    "week",
    "team",
    "position",
    "full_name",
    "report_status",
    "practice_status",
    "report_primary_injury",
)

#: The real practice_status strings, verified against injuries_2026.parquet.
PRACTICE_STATUS = {
    "full participation in practice": "FULL",
    "limited participation in practice": "LIMITED",
    "did not participate in practice": "DNP",
}

log = get_logger(__name__)

Loader = Callable[[Sequence[int]], Any]


@dataclass(frozen=True, slots=True)
class InjuryReport:
    """One player's line on the official report.

    ``report_status`` is the game designation -- Out, Doubtful, Questionable -- and is
    often absent. ``practice_status`` is FULL, LIMITED or DNP. See the module docstring on
    what the practice label is and is not evidence for.
    """

    report_status: str | None
    practice_status: str | None
    primary_injury: str | None

    @property
    def is_out(self) -> bool:
        """The one designation that is not a judgement call."""
        return (self.report_status or "").strip().lower() == "out"


class NflverseInjuries:
    """Injury reports. Optional: partial coverage means it informs, never gates."""

    name = "nflverse_injuries"
    required = False

    def __init__(
        self,
        season: int,
        cache: FileCache,
        *,
        loader: Loader | None = None,
    ) -> None:
        self.season = season
        self.cache = cache
        self._loader = loader
        self._last_error: str | None = None

    def _load(self) -> Loader:
        if self._loader is not None:
            return self._loader
        try:
            import nflreadpy
        except ImportError as exc:  # pragma: no cover - a packaging problem, not a runtime one
            raise SourceUnavailable(
                "nflverse_injuries",
                "nflreadpy is not installed. Note it is nflreadpy, not the archived "
                "nfl_data_py. Run 'make setup'.",
                required=False,
            ) from exc
        return lambda seasons: nflreadpy.load_injuries(list(seasons))

    def _rows(self) -> list[dict[str, Any]]:
        key = f"nflverse_injuries:{self.season}"
        cached = self.cache.get(key, DEFAULT_TTL_SECONDS["nflverse_injuries"])
        if cached is not None:
            return list(cached)
        try:
            frame = self._load()([self.season])
        except SourceUnavailable:
            raise
        except Exception as exc:
            raise SourceUnavailable("nflverse_injuries", str(exc), required=False) from exc

        rows = to_rows(frame)
        validate(rows)
        self.cache.set(key, rows)
        return rows

    def weekly(self, week: int, players: list[Player]) -> dict[PlayerId, InjuryReport]:
        """Reports for this week, keyed by Yahoo player id.

        A player with no entry is absent from the result. That means "nothing reported",
        not "healthy" -- coverage is partial.
        """
        try:
            rows = self._rows()
        except (SourceUnavailable, SchemaDrift) as exc:
            self._last_error = str(exc)
            log.warning("nflverse_unavailable", detail=str(exc))
            raise

        index = index_by_match_key(rows, self.season, week)
        out: dict[PlayerId, InjuryReport] = {}
        for player in players:
            report = index.get(match_key(player.name, player.position, player.team))
            if report is not None:
                out[player.id] = report
        self._last_error = None
        log.info("nflverse_injuries", week=week, asked=len(players), matched=len(out))
        return out

    def status(self) -> SourceStatus:
        return SourceStatus(
            name=self.name,
            ok=self._last_error is None,
            required=self.required,
            age_seconds=self.cache.age_seconds(f"nflverse_injuries:{self.season}"),
            detail=self._last_error,
        )


def to_rows(frame: Any) -> list[dict[str, Any]]:
    """Polars frame to plain dicts. nflreadpy returns Polars, not pandas."""
    if isinstance(frame, list):
        return [dict(row) for row in frame]
    for method in ("to_dicts", "to_dict"):
        converter = getattr(frame, method, None)
        if callable(converter):
            result = converter(orient="records") if method == "to_dict" else converter()
            return [dict(row) for row in result]
    raise SchemaDrift(
        "nflverse_injuries",
        f"cannot read a {type(frame).__name__} as rows",
        required=False,
    )


def validate(rows: list[dict[str, Any]]) -> None:
    """Check the columns on every fetch. An empty frame is a valid answer in the offseason."""
    if not rows:
        return
    missing = [column for column in REQUIRED_COLUMNS if column not in rows[0]]
    if missing:
        raise SchemaDrift(
            "nflverse_injuries",
            f"missing columns: {', '.join(missing)}",
            required=False,
        )


def normalize_practice_status(raw: Any) -> str | None:
    """The full phrase down to FULL / LIMITED / DNP.

    The published column description says the values are already Full/Limited/DNP. They are
    not -- see the module docstring -- so both spellings are accepted.
    """
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if not text:
        return None
    if text in PRACTICE_STATUS:
        return PRACTICE_STATUS[text]
    short = {"full": "FULL", "limited": "LIMITED", "dnp": "DNP"}
    return short.get(text)


def index_by_match_key(
    rows: list[dict[str, Any]], season: int, week: int
) -> dict[str, InjuryReport]:
    out: dict[str, InjuryReport] = {}
    for row in rows:
        if int(row.get("season", -1)) != season or int(row.get("week", -1)) != week:
            continue
        position = parse_position(row.get("position"))
        if position is None:
            # Offensive linemen and defenders fill most of the report. Not modelled here.
            continue
        name = row.get("full_name")
        if not name:
            continue
        key = match_key(str(name), position, canonical_team(row.get("team")))
        out[key] = InjuryReport(
            report_status=_clean(row.get("report_status")),
            practice_status=normalize_practice_status(row.get("practice_status")),
            primary_injury=_clean(row.get("report_primary_injury"))
            or _clean(row.get("practice_primary_injury")),
        )
    return out


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
