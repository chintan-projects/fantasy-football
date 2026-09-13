"""What the other eleven managers actually do with their money.

`interested_bidders` is the weakest input in the FAAB math and the one with the largest
leverage on the answer. The equilibrium shading is ``(n-1)/n``: n=2 shades to 50%, n=6 to
83%. Getting n wrong by three managers moves the bid more than any refinement to the
valuation. The usual substitute -- league size -- is simply the wrong number, because
eleven managers do not all want a backup tight end.

So this derives n from what the league has actually done, rather than from a table in an
article about some other league.

## The limitation, stated once and plainly

**Yahoo's transaction log exposes winning bids only.** A completed add carries the
`faab_bid` that won. The bids that lost are not in the API at all. So:

* We learn the **clearing price** for a position tier. `[empirical]`
* We do **not** learn the bid distribution, the number of bidders on any past player, or
  how close the runner-up was. `[not observable]`

Everything here is therefore an estimate of *who could credibly have bid*, not a count of
who did. That is an **upper bound** on real competition, and an upper bound on n means
`(n-1)/n` shades too little -- it bids too high, not too low. The direction of the error
is stated wherever a number crosses into the bid math, because a correction whose sign is
unknown is worse than no correction.

See BUGS.yaml KNOWN-001 and the PROGRESS.yaml backlog item about scraping losing bids from
the Yahoo desktop UI, which is where they are visible.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from ff.domain.models import Position, TeamKey

#: A manager who has not bid at all this season is not modelled as uninterested -- he is
#: modelled as unknown, and falls back to the league's own behaviour. Below this many
#: observed bids, a per-manager rate is fitted noise.
MIN_BIDS_FOR_A_PROFILE = 3

#: Spend above this multiple of the league's median per-bid spend reads as aggressive.
AGGRESSIVE_MULTIPLE = 1.5

#: A manager sitting on more than this share of budget this late is hoarding, which makes
#: him a live bidder on anything he wants rather than a spent force.
HOARDING_SHARE = 0.5
HOARDING_FROM_WEEK = 8


@dataclass(frozen=True, slots=True)
class WinningBid:
    """One completed FAAB add, as Yahoo reports it. The losing bids do not exist here."""

    team_key: TeamKey
    player_id: str
    position: Position | None
    amount: int
    week: int
    timestamp: float


@dataclass(frozen=True, slots=True)
class ManagerProfile:
    """One rival's revealed behaviour. Derived, never authoritative."""

    team_key: TeamKey
    bids_won: int
    total_spent: int
    max_winning_bid: int
    mean_winning_bid: float
    positions_bought: dict[Position, int] = field(default_factory=dict)
    is_aggressive: bool = False
    has_enough_history: bool = False

    def wants(self, position: Position) -> bool:
        """Has this manager ever spent on this position?

        A manager with no history at a position is not excluded -- see ``credible_bidders``.
        This only answers what the record shows.
        """
        return self.positions_bought.get(position, 0) > 0


@dataclass(frozen=True, slots=True)
class LeagueBidModel:
    """The league's revealed price level, plus one profile per rival."""

    league_key: str
    profiles: dict[TeamKey, ManagerProfile]
    clearing_by_position: dict[Position, float]
    median_winning_bid: float
    total_bids_observed: int
    weeks_observed: tuple[int, ...] = ()

    @property
    def is_thin(self) -> bool:
        """Too few observations to say anything a league-size guess would not.

        Early in a season this is the normal state, and saying so beats reporting a
        confident number derived from four transactions.
        """
        return self.total_bids_observed < MIN_BIDS_FOR_A_PROFILE * 2


def build_model(
    league_key: str,
    bids: list[WinningBid],
    num_teams: int,
    my_team_key: TeamKey | None = None,
) -> LeagueBidModel:
    """Derive the league's bidding behaviour from its completed transactions."""
    rivals = [b for b in bids if b.team_key != my_team_key]

    by_team: dict[TeamKey, list[WinningBid]] = {}
    for bid in rivals:
        by_team.setdefault(bid.team_key, []).append(bid)

    amounts = [b.amount for b in rivals if b.amount > 0]
    median_bid = statistics.median(amounts) if amounts else 0.0

    profiles: dict[TeamKey, ManagerProfile] = {}
    for team_key, team_bids in by_team.items():
        paid = [b.amount for b in team_bids if b.amount > 0]
        positions: dict[Position, int] = {}
        for bid in team_bids:
            if bid.position is not None:
                positions[bid.position] = positions.get(bid.position, 0) + 1
        mean_bid = statistics.mean(paid) if paid else 0.0
        profiles[team_key] = ManagerProfile(
            team_key=team_key,
            bids_won=len(team_bids),
            total_spent=sum(paid),
            max_winning_bid=max(paid, default=0),
            mean_winning_bid=mean_bid,
            positions_bought=positions,
            is_aggressive=bool(median_bid) and mean_bid > median_bid * AGGRESSIVE_MULTIPLE,
            has_enough_history=len(team_bids) >= MIN_BIDS_FOR_A_PROFILE,
        )

    clearing: dict[Position, float] = {}
    for position in Position:
        at_position = [b.amount for b in rivals if b.position is position and b.amount > 0]
        if at_position:
            clearing[position] = float(statistics.median(at_position))

    return LeagueBidModel(
        league_key=league_key,
        profiles=profiles,
        clearing_by_position=clearing,
        median_winning_bid=float(median_bid),
        total_bids_observed=len(rivals),
        weeks_observed=tuple(sorted({b.week for b in rivals})),
    )


def credible_bidders(
    model: LeagueBidModel,
    position: Position,
    budgets: dict[TeamKey, int],
    week: int,
    roster_needs: dict[TeamKey, bool] | None = None,
) -> int:
    """How many rivals could credibly outbid me for this player.

    A rival counts when all of these hold:

    1. He has enough budget to clear the position's observed clearing price. A manager with
       $3 left is not competition for a $20 player, whatever his history says.
    2. He has either bought this position before, or has no usable history yet -- an
       unknown manager is treated as live, because absence of evidence is not evidence he
       will sit out.
    3. If roster need is known, he needs the position. Yahoo does not hand this over
       directly; it comes from reading rosters, and when it is missing the manager counts.

    **This counts who could bid, not who did.** Yahoo shows winning bids only, so the true
    count is unobservable. This is an upper bound, and an upper bound on n makes
    ``(n-1)/n`` shade less -- which bids *higher*, not lower. Treat a large n from this
    function as the weakest number in the recommendation. `[theory]`
    """
    price = model.clearing_by_position.get(position, model.median_winning_bid)
    threshold = max(1, int(price))

    count = 0
    for team_key, budget in budgets.items():
        if budget < threshold:
            continue
        if roster_needs is not None and not roster_needs.get(team_key, True):
            continue
        profile = model.profiles.get(team_key)
        if profile is None or not profile.has_enough_history:
            count += 1  # unknown reads as live
            continue
        if profile.wants(position) or is_hoarding(profile, budget, week):
            count += 1
    return count


def is_hoarding(profile: ManagerProfile, budget: int, week: int, total_budget: int = 100) -> bool:
    """A manager still holding most of his budget late is live on everything.

    Unspent FAAB is worth nothing at season end, so someone sitting on $60 in week 10 is
    not frugal -- he is about to spend it, and probably on whatever comes up next.
    """
    if week < HOARDING_FROM_WEEK:
        return False
    return budget > total_budget * HOARDING_SHARE


def expected_clearing_price(model: LeagueBidModel, position: Position) -> tuple[float, str]:
    """What this position has actually cleared at in **this** league, plus its provenance.

    Returns the price and a sentence about where it came from, because a median of two
    transactions and a median of thirty deserve different amounts of trust and the caller
    has no other way to tell them apart.
    """
    at_position = model.clearing_by_position.get(position)
    if at_position is not None:
        return at_position, (
            f"Median winning bid for a {position.value} in this league. "
            f"Winning bids only -- the losing bids are not in Yahoo's API, so this is the "
            f"clearing price, not the going rate. [empirical]"
        )
    if model.median_winning_bid > 0:
        return model.median_winning_bid, (
            f"No {position.value} has been claimed with FAAB in this league yet, so this "
            f"falls back to the median winning bid across all positions. [empirical]"
        )
    return 0.0, (
        "No FAAB transactions observed yet. There is nothing to learn from, and any "
        "number here would be invented. [theory]"
    )
