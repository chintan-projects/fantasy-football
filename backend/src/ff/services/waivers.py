"""The waiver board, and the bid that follows from it.

Ranking free agents by projected points is the obvious thing and it is wrong. A 12-point
WR is worthless if the worst WR already on the roster projects for 11.5, and a 9-point RB
is a major add if the current RB2 projects for 4. What matters is the gap over **my own**
replacement at that slot, every week for the rest of the season -- which is what VORP
means and why the board is built this way.

The bid then converts that gap into dollars through ``domain.faab``, using the opponent
model's estimate of how many rivals could credibly outbid me. That estimate is the weakest
number in the chain and is labelled as such wherever it surfaces.
"""

from __future__ import annotations

from dataclasses import dataclass

from ff.adapters.base import LeagueReader, ProjectionSource
from ff.adapters.store import Store
from ff.core.logging import get_logger
from ff.domain.faab import BidContext, recommend_bid
from ff.domain.models import (
    BidRecommendation,
    LeagueSettings,
    Player,
    Position,
    Projection,
    TeamKey,
    WaiverTarget,
)
from ff.domain.opponent import (
    LeagueBidModel,
    build_model,
    credible_bidders,
    expected_clearing_price,
)
from ff.services.projections import build_projections
from ff.services.week import WeekBundle

log = get_logger(__name__)

#: A regular season is 17 weeks; fantasy playoffs usually start at 15. Rest-of-season value
#: is counted to the end of the fantasy regular season, not the NFL one.
DEFAULT_FINAL_WEEK = 17


#: Roughly how many bid periods remain, one a week, used for the option-value term.
def bid_periods_remaining(week: int, final_week: int = DEFAULT_FINAL_WEEK) -> int:
    return max(0, final_week - week)


@dataclass(frozen=True, slots=True)
class BoardEntry:
    """One waiver target with everything needed to explain its rank."""

    target: WaiverTarget
    projection: Projection
    replacement_points: float
    replacement_player_id: str | None
    reason: str


def replacement_level(
    roster_players: list[Player],
    projections: dict[str, Projection],
    position: Position,
) -> tuple[float, str | None]:
    """The worst rostered player at this position, and what he projects for.

    That player is who the add would actually displace, so he -- not a league-wide
    positional average -- is the right baseline. A league-average replacement level answers
    a different question: what a *typical* manager gains, which is not what I am deciding.
    """
    at_position = [
        (projections[str(p.id)].mean, str(p.id))
        for p in roster_players
        if p.position is position and str(p.id) in projections
    ]
    if not at_position:
        return 0.0, None
    points, player_id = min(at_position)
    return points, player_id


def build_board(
    yahoo: LeagueReader,
    sources: list[ProjectionSource],
    bundle: WeekBundle,
    *,
    position: Position | None = None,
    limit: int = 25,
    final_week: int = DEFAULT_FINAL_WEEK,
    bidder_estimate: dict[Position, int] | None = None,
) -> list[BoardEntry]:
    """Rank the free agent pool by rest-of-season value over my current replacement."""
    free_agents = yahoo.free_agents(
        position=position.value if position else None, limit=max(limit * 2, 50)
    )
    if not free_agents:
        return []

    blended = build_projections(sources, bundle.week, free_agents)
    fa_projections = {str(k): v for k, v in blended.projections.items()}
    my_projections = {str(k): v for k, v in bundle.projections.items()}
    weeks_left = max(1, final_week - bundle.week + 1)

    entries: list[BoardEntry] = []
    for player in free_agents:
        projection = fa_projections.get(str(player.id))
        if projection is None:
            continue  # fewer than two sources cover him; no guess goes on the board
        baseline, baseline_id = replacement_level(
            list(bundle.roster.players), my_projections, player.position
        )
        vorp = projection.mean - baseline
        if vorp <= 0:
            continue  # he does not beat what is already on the roster

        bidders = (bidder_estimate or {}).get(player.position, 0)
        entries.append(
            BoardEntry(
                target=WaiverTarget(
                    player=player,
                    ros_vorp_per_week=vorp,
                    weeks_remaining=weeks_left,
                    interested_bidders=bidders,
                    trending_adds=None,
                ),
                projection=projection,
                replacement_points=baseline,
                replacement_player_id=baseline_id,
                reason=(
                    f"Projects {projection.mean:.1f} against your current worst "
                    f"{player.position.value} at {baseline:.1f} -- a gap of {vorp:.1f} "
                    f"points a week for {weeks_left} weeks. Sources: "
                    f"{', '.join(projection.sources)}."
                ),
            )
        )

    entries.sort(key=lambda e: e.target.ros_vorp_per_week, reverse=True)
    log.info("waiver_board", week=bundle.week, pool=len(free_agents), ranked=len(entries))
    return entries[:limit]


def refresh_opponent_model(
    yahoo: LeagueReader, store: Store, my_team_key: str, num_teams: int
) -> LeagueBidModel:
    """Rebuild the model from Yahoo's transaction log and persist it.

    Persisted because the derivation is worth keeping between requests, not because it is
    authoritative -- it is a cache of a derivation and is safe to delete and recompute.
    """
    league_key = yahoo.league_key()
    bids = yahoo.transactions(limit=200)
    model = build_model(league_key, bids, num_teams=num_teams, my_team_key=TeamKey(my_team_key))
    for team_key, profile in model.profiles.items():
        store.save_opponent_model(
            league_key,
            str(team_key),
            {
                "bids_won": profile.bids_won,
                "total_spent": profile.total_spent,
                "max_winning_bid": profile.max_winning_bid,
                "mean_winning_bid": profile.mean_winning_bid,
                "positions_bought": {k.value: v for k, v in profile.positions_bought.items()},
                "is_aggressive": profile.is_aggressive,
                "has_enough_history": profile.has_enough_history,
            },
        )
    log.info(
        "opponent_model_refreshed",
        league=league_key,
        bids=model.total_bids_observed,
        thin=model.is_thin,
    )
    return model


def estimate_bidders(
    model: LeagueBidModel,
    position: Position,
    week: int,
    num_teams: int,
    budgets: dict[TeamKey, int] | None = None,
) -> tuple[int, str]:
    """How many rivals could credibly outbid me, plus a sentence saying how sure that is.

    When rival budgets are unknown -- Yahoo gives every team's ``faab_balance`` only on a
    league-wide team read we do not make on every request -- this falls back to assuming
    everyone can afford the clearing price, which is the most competitive assumption and
    therefore shades the least. Combined with KNOWN-001 (winning bids only), both unknowns
    push the same way: **toward bidding too high.** Said out loud rather than buried.
    """
    if model.is_thin:
        fallback = max(2, num_teams // 4)
        return fallback, (
            f"Only {model.total_bids_observed} FAAB transactions seen in this league so "
            f"far, which is too few to tell you anything a guess would not. Falling back "
            f"to {fallback} of {num_teams}. [folk]"
        )

    if budgets is None:
        price, _ = expected_clearing_price(model, position)
        budgets = {team: int(price) + 1 for team in model.profiles}

    n = credible_bidders(model, position, budgets, week)
    return max(1, n), (
        f"{n} of {num_teams} managers have both the budget and a history of buying "
        f"{position.value} in this league. This counts who COULD bid, not who did -- "
        f"Yahoo publishes winning bids only -- so it is an upper bound, and an upper "
        f"bound here shades less and bids higher. [theory]"
    )


def bid_for(
    entry: BoardEntry,
    *,
    week: int,
    remaining_budget: int,
    settings: LeagueSettings,
    final_week: int = DEFAULT_FINAL_WEEK,
) -> BidRecommendation:
    """Convert one board entry into one number to bid.

    ``remaining_budget`` must come from Yahoo's ``faab_balance``, read live. Never from a
    spend log of our own: bids placed from the Yahoo app never reach it, and a balance that
    is quietly wrong is worse than one that is missing.
    """
    return recommend_bid(
        BidContext(
            target=entry.target,
            week=week,
            remaining_budget=remaining_budget,
            bid_periods_remaining=bid_periods_remaining(week, final_week),
            playoff_start_week=settings.playoff_start_week,
        )
    )
