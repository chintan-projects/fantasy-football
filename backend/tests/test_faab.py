from __future__ import annotations

import pytest

from ff.domain.faab import (
    BidContext,
    competition_shading,
    normalized_demand,
    option_value_penalty,
    recommend_bid,
    reservation_value,
    unspent_budget_warning,
    winners_curse_shading,
)
from ff.domain.models import Player, PlayerId, Position, Slot, WaiverTarget


def target(vorp: float, bidders: int, weeks: int = 12) -> WaiverTarget:
    player = Player(
        id=PlayerId("p1"),
        name="Target",
        position=Position.RB,
        team="AAA",
        eligible_slots=frozenset({Slot.RB, Slot.FLEX}),
    )
    return WaiverTarget(player, vorp, weeks, bidders)


def test_competition_shading_uses_interested_bidders_not_league_size() -> None:
    """Three interested bidders means shade to 67%, not 92% for a 12-team league."""
    assert competition_shading(3) == pytest.approx(2 / 3)
    assert competition_shading(12) == pytest.approx(11 / 12)


def test_winners_curse_shading_grows_with_bidders() -> None:
    assert winners_curse_shading(8) < winners_curse_shading(2)
    assert winners_curse_shading(50) >= 0.55


def test_option_value_penalty_disappears_late() -> None:
    assert option_value_penalty(4, 20) < 1.0
    assert option_value_penalty(13, 2) == 1.0


def test_late_season_bids_spend_the_reserve() -> None:
    """A dollar held past week 12 is worth nothing, so the reserve stops applying."""
    early = recommend_bid(BidContext(target(6.0, 3), 4, 100, 20, 15))
    late = recommend_bid(BidContext(target(6.0, 3), 13, 100, 2, 15))
    assert late.recommended_bid > early.recommended_bid
    assert late.option_value_penalty == 1.0


def test_bid_never_exceeds_remaining_budget() -> None:
    rec = recommend_bid(BidContext(target(25.0, 2), 13, 9, 2, 15))
    assert rec.recommended_bid <= 9


def test_marginal_player_gets_a_small_or_zero_bid() -> None:
    rec = recommend_bid(BidContext(target(0.3, 6), 5, 100, 18, 15))
    assert rec.recommended_bid <= 3


def test_reservation_value_rises_with_value_and_weeks() -> None:
    ctx_short = BidContext(target(5.0, 3, weeks=2), 12, 100, 4, 15)
    ctx_long = BidContext(target(5.0, 3, weeks=12), 4, 100, 20, 15)
    assert reservation_value(ctx_long) > reservation_value(ctx_short)


def test_normalized_demand_corrects_for_availability() -> None:
    """A widely rostered player has a smaller pool of leagues that could add him."""
    scarce = normalized_demand(trending_adds=1000, percent_rostered=0.80)
    plentiful = normalized_demand(trending_adds=1000, percent_rostered=0.05)
    assert scarce > plentiful


def test_unspent_budget_warning_fires_only_late() -> None:
    assert unspent_budget_warning(5, 90, 100) is None
    assert unspent_budget_warning(13, 90, 100) is not None
    assert unspent_budget_warning(13, 10, 100) is None
