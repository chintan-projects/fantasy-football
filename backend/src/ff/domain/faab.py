"""FAAB bidding.

FAAB is a sequential, budget-constrained, first-price sealed-bid auction with *common*
values. Three corrections stack, and skipping any of them means systematically overpaying:

1. Private-value shading, ``(n-1)/n``, where n is the number of managers who actually want
   this player -- not the league size. This is the near-universal error.
2. Winner's curse shading. Values are common (everyone read the same beat report) and
   signals are noisy, so conditional on winning you held the highest of n noisy estimates,
   which is biased upward. The one real dataset shows 85-90% of large FAAB spends graded as
   failures. That is this effect, measured.
3. Option value. A reserved dollar is worth the surplus it buys later. That shadow price
   declines to roughly zero by week 12, so unspent budget at season end is a strict error.

See .claude/skills/fantasy-decision-math, section 3. Note the honest caveat there: the
framework is right and the inputs are near-noise, because the rest-of-season value of a
two-game breakout is the least estimable number in the whole model.
"""

from __future__ import annotations

from dataclasses import dataclass

from ff.domain.lineup import MARGIN_SD
from ff.domain.models import BidRecommendation, WaiverTarget

#: Reserve against a late-season injury to your own RB1 -- the one case where a dollar's
#: shadow price stays high late, because replacement level can collapse without warning.
DEFAULT_LATE_SEASON_RESERVE = 18

#: Week after which a withheld dollar is worth nothing. Bid full value from here.
OPTION_VALUE_ZERO_WEEK = 12


@dataclass(frozen=True, slots=True)
class BidContext:
    target: WaiverTarget
    week: int
    remaining_budget: int
    bid_periods_remaining: int
    playoff_start_week: int
    reserve: int = DEFAULT_LATE_SEASON_RESERVE


def _normal_cdf(x: float) -> float:
    from math import erf, sqrt

    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def win_prob_gain_per_week(vorp_per_week: float, margin_sd: float = MARGIN_SD) -> float:
    """Convert points over replacement into win probability, the common currency.

    A player worth +4 points a week moves a single week's win probability by about 4
    points. Over a dozen weeks that is roughly half an expected win.
    """
    return _normal_cdf(vorp_per_week / margin_sd) - 0.5


def reservation_value(ctx: BidContext) -> float:
    """What the player is worth to me, in dollars, before any shading.

    Value the whole remaining season of surplus, convert to win probability, and price it
    against the budget that is actually deployable this season.
    """
    gain_per_week = win_prob_gain_per_week(ctx.target.ros_vorp_per_week)
    total_gain = gain_per_week * ctx.target.weeks_remaining
    deployable = max(ctx.remaining_budget - ctx.reserve, 0)

    # A full season's worth of win probability from one add is roughly 0.5 expected wins,
    # which is worth most of a deployable budget. Anchor on that and scale linearly.
    reference_gain = 0.5
    if reference_gain <= 0:
        return 0.0
    return max(0.0, min(float(deployable), deployable * total_gain / reference_gain))


def competition_shading(interested_bidders: int) -> float:
    """Symmetric first-price equilibrium: bid ``(n-1)/n`` of value.

    n is the count of managers with the roster hole, the bench space and the budget -- not
    the league size. If 3 of 12 teams need an RB2, shade to 67%, not 92%. Reading the
    league's rosters to estimate n is worth more than any published bid-percentage table.
    """
    n = max(1, interested_bidders)
    return (n - 1) / n if n > 1 else 0.85


def winners_curse_shading(interested_bidders: int) -> float:
    """Extra shading for the common-value structure. More bidders, more curse.

    The functional form is a calibrated guess, not a derived result -- the correction grows
    with n, which is the part theory guarantees. Backtest it before trusting the constants.
    """
    n = max(1, interested_bidders)
    return max(0.55, 1.0 - 0.06 * (n - 1))


def option_value_penalty(week: int, bid_periods_remaining: int) -> float:
    """The shadow price of a reserved dollar, as a multiplier on the bid.

    Declines monotonically to zero by week 12. This is the rigorous version of "FAAB
    depreciates", and it implies shading should be *heaviest* early -- which contradicts
    the popular advice to spend 30-40% of budget in the first few weeks.
    """
    if week >= OPTION_VALUE_ZERO_WEEK or bid_periods_remaining <= 1:
        return 1.0
    remaining_fraction = min(1.0, bid_periods_remaining / 20.0)
    return 1.0 - 0.25 * remaining_fraction


def recommend_bid(ctx: BidContext) -> BidRecommendation:
    """One number to bid. Bid it once; do not probe.

    A losing bid costs nothing -- you pay only when you win -- so probing low to learn the
    price is incoherent. It forfeits the player for free and teaches you nothing you would
    not have learned anyway.
    """
    value = reservation_value(ctx)
    comp = competition_shading(ctx.target.interested_bidders)
    curse = winners_curse_shading(ctx.target.interested_bidders)
    option = option_value_penalty(ctx.week, ctx.bid_periods_remaining)

    raw = value * comp * curse * option
    late_season = ctx.week >= OPTION_VALUE_ZERO_WEEK
    cap = ctx.remaining_budget if late_season else max(ctx.remaining_budget - ctx.reserve, 0)
    bid = round(min(raw, cap))

    if bid <= 0:
        reason = (
            "Not worth a bid. The projected edge over your current replacement does not "
            "clear the cost, and a losing bid is free."
        )
    elif late_season:
        reason = (
            f"Week {ctx.week}: a withheld dollar is worth nothing now, so this is close to "
            f"full value. Shaded to {comp:.0%} for roughly "
            f"{ctx.target.interested_bidders} interested bidders and {curse:.0%} for the "
            f"common-value winner's curse."
        )
    else:
        reason = (
            f"Reservation value ${value:.0f}. Shaded to {comp:.0%} for about "
            f"{ctx.target.interested_bidders} interested bidders, {curse:.0%} for the "
            f"winner's curse, {option:.0%} for the option value of holding budget. "
            f"Reserving ${ctx.reserve} against a late-season RB1 injury."
        )

    return BidRecommendation(
        player_id=ctx.target.player.id,
        reservation_value=round(value),
        recommended_bid=bid,
        shading_for_competition=comp,
        shading_for_winners_curse=curse,
        option_value_penalty=option,
        reason=reason,
    )


def normalized_demand(trending_adds: int, percent_rostered: float) -> float:
    """Trending adds, corrected for how many leagues could even add him.

    A player rostered in 60% of leagues has a mechanically smaller add pool than one
    rostered in 5%. Without this correction you systematically under-rank the breakouts
    everyone already knows about. It is also a *lagging* signal -- by the time a player
    trends, your competitors have read the same news.
    """
    availability = max(1.0 - percent_rostered, 0.01)
    return trending_adds / availability


def unspent_budget_warning(week: int, remaining_budget: int, total_budget: int) -> str | None:
    """Leftover FAAB at season end is pure waste. Say so before it is too late to fix."""
    if week < OPTION_VALUE_ZERO_WEEK:
        return None
    if remaining_budget > total_budget * 0.35:
        return (
            f"You are holding ${remaining_budget} of ${total_budget} with the season nearly "
            f"over. A dollar you do not spend is worth nothing. Bid full value from here."
        )
    return None
