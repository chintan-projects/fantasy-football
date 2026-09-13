"""Collect projections from every source and hand them to ``domain.blend``.

The single rule this exists to enforce: **a projection is an ensemble or it is nothing.**
A simple average of sources beat individual sources in 63% of head-to-head comparisons,
which makes it the highest-return modelling decision available and makes a single-source
setup a misconfiguration rather than a degraded mode. So:

* fewer than two sources configured is a ``ConfigError``, raised before any fetch
* fewer than two sources answering is a ``SourceUnavailable``, raised after
* a *player* covered by fewer than two sources gets no projection at all and is listed in
  ``unprojected`` -- one source's number is not a thinner ensemble, it is a different and
  worse thing wearing the same type

Optional sources that fail degrade into a source status and a note. Required sources that
fail stop the run. No math happens here; ``domain.blend`` does that.
"""

from __future__ import annotations

from dataclasses import dataclass

from ff.adapters.base import ProjectionSource
from ff.core.errors import ConfigError, SourceUnavailable
from ff.core.logging import get_logger
from ff.domain.blend import blend
from ff.domain.models import Player, PlayerId, Projection, SourceStatus

MINIMUM_SOURCES = 2

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BlendedProjections:
    """What one week's projection pass produced, including what it could not produce."""

    week: int
    projections: dict[PlayerId, Projection]
    unprojected: tuple[PlayerId, ...]
    sources: tuple[SourceStatus, ...]
    notes: tuple[str, ...] = ()

    @property
    def coverage(self) -> float:
        total = len(self.projections) + len(self.unprojected)
        return len(self.projections) / total if total else 0.0


def build_projections(
    sources: list[ProjectionSource],
    week: int,
    players: list[Player],
) -> BlendedProjections:
    """Fetch every source, then blend per player.

    Raises ``ConfigError`` if fewer than two sources are configured, and
    ``SourceUnavailable`` if fewer than two answered.
    """
    if len(sources) < MINIMUM_SOURCES:
        names = ", ".join(s.name for s in sources) or "(none)"
        raise ConfigError(
            f"{len(sources)} projection source(s) configured: {names}. At least "
            f"{MINIMUM_SOURCES} are required -- an average of sources beat individual "
            f"sources in 63% of comparisons, so one source is a misconfiguration, not a "
            f"degraded mode. See docs/DECISIONS.md."
        )

    by_source: dict[str, dict[PlayerId, float]] = {}
    statuses: list[SourceStatus] = []
    notes: list[str] = []

    for source in sources:
        try:
            by_source[source.name] = source.weekly(week, players)
        except SourceUnavailable as exc:
            if exc.required:
                log.error("required_source_failed", source=source.name, detail=str(exc))
                raise
            log.warning("optional_source_degraded", source=source.name, detail=str(exc))
            notes.append(
                f"{source.name} was unavailable ({exc.detail}). Its players fall back to "
                f"the remaining sources, and some may have too few to project."
            )
        statuses.append(source.status())

    answered = [name for name, values in by_source.items() if values]
    if len(answered) < MINIMUM_SOURCES:
        raise SourceUnavailable(
            "projections",
            f"only {len(answered)} source(s) returned data: {', '.join(answered) or 'none'}. "
            f"Running on one source is a misconfiguration, not a degraded mode.",
            required=True,
        )

    projections: dict[PlayerId, Projection] = {}
    unprojected: list[PlayerId] = []
    for player in players:
        means = {
            name: values[player.id] for name, values in by_source.items() if player.id in values
        }
        if len(means) < MINIMUM_SOURCES:
            unprojected.append(player.id)
            continue
        projections[player.id] = blend(player.id, player.position, means)

    if unprojected:
        notes.append(
            f"{len(unprojected)} of {len(players)} players are covered by fewer than "
            f"{MINIMUM_SOURCES} sources and have no projection. They are shown without one "
            f"rather than with a single source's guess."
        )

    log.info(
        "projections_blended",
        week=week,
        sources=sorted(answered),
        projected=len(projections),
        unprojected=len(unprojected),
    )
    return BlendedProjections(
        week=week,
        projections=projections,
        unprojected=tuple(unprojected),
        sources=tuple(statuses),
        notes=tuple(notes),
    )
