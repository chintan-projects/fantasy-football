"""Combining projection sources into one distribution.

A simple average beat individual sources in 63% of head-to-head comparisons, so the blend
is not a nicety -- a single-source configuration is a misconfiguration. See
.claude/skills/fantasy-decision-math, section 0.
"""

from __future__ import annotations

from statistics import mean, pstdev

from ff.domain.models import PlayerId, Position, Projection

#: Coefficient of variation by position, from published weekly MAE against typical means.
#: This is the aleatoric (week-to-week) prior that per-player game logs get shrunk toward.
POSITION_CV_PRIOR: dict[Position, float] = {
    Position.QB: 0.32,
    Position.RB: 0.48,
    Position.WR: 0.55,
    Position.TE: 0.58,
    Position.K: 0.45,
    Position.DEF: 0.70,
}

#: Games of personal history needed before the player's own variance outweighs the prior.
#: Sixteen games estimates a variance to about +/-20%, so shrink hard.
SHRINKAGE_GAMES = 12.0


class SingleSourceError(ValueError):
    """Raised when only one projection source is configured. That is a bug, not a setup."""


def blend(
    player_id: PlayerId,
    position: Position,
    source_means: dict[str, float],
    observed_sd: float | None = None,
    games_observed: int = 0,
    p_zero: float = 0.03,
    allow_single_source: bool = False,
) -> Projection:
    """Average the sources, and keep the two kinds of uncertainty separate.

    ``epistemic_sd`` is the spread between sources -- they disagree about his role.
    ``aleatoric_sd`` is week-to-week volatility, shrunk toward a position prior.
    These are different quantities and only the second is what "boom or bust" means.
    Conflating them is the most common modeling error in public fantasy tools.
    """
    if not source_means:
        raise ValueError(f"No projections for {player_id}")
    if len(source_means) == 1 and not allow_single_source:
        raise SingleSourceError(
            f"Only one projection source for {player_id}. Ensembles beat single sources in "
            f"63% of comparisons; configure at least two."
        )

    values = list(source_means.values())
    mu = mean(values)
    epistemic = pstdev(values) if len(values) > 1 else mu * 0.10

    prior_sd = mu * POSITION_CV_PRIOR.get(position, 0.5)
    if observed_sd is not None and games_observed > 0:
        weight = games_observed / (games_observed + SHRINKAGE_GAMES)
        aleatoric = weight * observed_sd + (1 - weight) * prior_sd
    else:
        aleatoric = prior_sd

    return Projection(
        player_id=player_id,
        mean=mu,
        epistemic_sd=max(epistemic, 1e-6),
        aleatoric_sd=max(aleatoric, 1e-6),
        p_zero=p_zero,
        per_source=tuple(sorted(source_means.items())),
    )


def implied_team_total(game_total: float, spread: float) -> float:
    """Vegas implied points for a team. ``spread`` is negative when favored.

    The betting market aggregates injury, weather and role information faster than any
    projection source. Use it as the prior on game environment -- and note that if you use
    it, applying a separate weather adjustment double-counts, because the market already
    priced the weather in.
    """
    return game_total / 2.0 - spread / 2.0


def wind_adjustment(wind_mph: float, is_indoor: bool) -> float:
    """Multiplier for passing volume. Almost always exactly 1.0.

    Only 3.7% of games have winds over 20 mph, and below that threshold the measured effect
    on passing is negligible. At 20+ mph pass rate over expectation drops under 3 points and
    total plays fall by under 2. Returning 1.0 for the other 96% of games is the finding,
    not a stub.
    """
    if is_indoor or wind_mph < 20.0:
        return 1.0
    return max(0.93, 1.0 - 0.005 * (wind_mph - 20.0))
