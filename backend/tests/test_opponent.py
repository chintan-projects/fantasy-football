"""The opponent model: what the other managers actually do with their money.

Two things are under test and they are different in kind.

The **derivation** is pure math over a transaction list, so it is tested directly against
numbers whose right answer can be worked out by hand.

The **honesty** of the derivation is tested just as hard, because this module's whole risk
is quiet overconfidence. Yahoo reports winning bids only. Anything claiming to know how
many managers bid, or what the losers offered, would be invented -- so there are tests
asserting the model declines to say, and asserting that the direction of its known bias is
stated where it crosses into the bid math.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ff.adapters.yahoo.parse import parse_faab_balance, parse_transactions
from ff.core.errors import SchemaDrift
from ff.domain.models import Position, TeamKey
from ff.domain.opponent import (
    MIN_BIDS_FOR_A_PROFILE,
    WinningBid,
    build_model,
    credible_bidders,
    expected_clearing_price,
    is_hoarding,
)

FIXTURES = Path(__file__).parent / "fixtures" / "yahoo"


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def bid(team: str, position: Position, amount: int, week: int = 3) -> WinningBid:
    return WinningBid(
        team_key=TeamKey(team),
        player_id=f"p{amount}",
        position=position,
        amount=amount,
        week=week,
        timestamp=1757000000.0 + amount,
    )


ALL_TEAMS = {TeamKey(f"461.l.1000.t.{i}"): 60 for i in range(2, 13)}


class TestParsingYahooTransactions:
    def test_winning_bids_come_out_of_the_transaction_log(self) -> None:
        bids = parse_transactions(load("transactions.json"))
        assert len(bids) == 8
        assert bids[0].amount == 31
        assert bids[0].position is Position.RB
        assert str(bids[0].team_key) == "461.l.1000.t.2"

    def test_a_waiver_priority_claim_carries_no_price_and_is_skipped(self) -> None:
        """No faab_bid means waiver priority, not a free player. It is not a price signal
        and counting it as $0 would drag every clearing price toward zero."""
        bids = parse_transactions(load("transactions_with_noise.json"))
        assert all(b.player_id != "461.p.108" for b in bids)

    def test_a_failed_claim_is_skipped(self) -> None:
        """A losing bid that Yahoo happens to surface as 'failed' is still not a clearing
        price -- it is the one thing we know did NOT clear."""
        bids = parse_transactions(load("transactions_with_noise.json"))
        assert all(b.amount != 99 for b in bids)

    def test_a_missing_collection_is_drift(self) -> None:
        with pytest.raises(SchemaDrift, match="transactions"):
            parse_transactions(load("malformed/transactions_no_collection.json"))

    def test_an_empty_log_is_not_an_error(self) -> None:
        """Week 1 of a new season looks exactly like this."""
        assert parse_transactions(load("malformed/transactions_empty.json")) == []


class TestFaabBalanceComesFromYahoo:
    def test_the_balance_is_read_off_the_team_resource(self) -> None:
        assert parse_faab_balance(load("team_faab.json")) == 73

    def test_a_missing_balance_is_drift_not_a_zero(self) -> None:
        """Defaulting to 0 would silently refuse every bid; defaulting to the budget would
        silently allow overspending. Neither is a guess worth making."""
        with pytest.raises(SchemaDrift, match="faab_balance"):
            parse_faab_balance(load("malformed/team_no_faab_balance.json"))


class TestBuildingTheModel:
    def test_clearing_price_is_the_median_winning_bid_for_the_position(self) -> None:
        bids = [
            bid("t.2", Position.RB, 10),
            bid("t.3", Position.RB, 20),
            bid("t.4", Position.RB, 30),
            bid("t.5", Position.WR, 5),
        ]
        model = build_model("461.l.1", bids, num_teams=12)
        assert model.clearing_by_position[Position.RB] == 20.0
        assert model.clearing_by_position[Position.WR] == 5.0

    def test_my_own_bids_are_excluded(self) -> None:
        """Bidding against my own history would be a feedback loop, and it inflates every
        clearing price by exactly the amount I overpaid last time."""
        bids = [bid("mine", Position.RB, 90), bid("t.3", Position.RB, 10)]
        model = build_model("461.l.1", bids, num_teams=12, my_team_key=TeamKey("mine"))
        assert model.clearing_by_position[Position.RB] == 10.0
        assert TeamKey("mine") not in model.profiles

    def test_an_aggressive_manager_is_identified(self) -> None:
        aggressive = [bid("t.2", Position.RB, 40 + i) for i in range(3)]
        quiet = [bid(f"t.{i}", Position.WR, 3) for i in range(3, 9)]
        model = build_model("461.l.1", aggressive + quiet, num_teams=12)
        assert model.profiles[TeamKey("t.2")].is_aggressive is True
        assert model.profiles[TeamKey("t.3")].is_aggressive is False

    def test_a_thin_history_is_flagged_rather_than_fitted(self) -> None:
        """Four transactions cannot tell you anything a league-size guess would not, and
        saying so beats reporting a confident number."""
        model = build_model("461.l.1", [bid("t.2", Position.RB, 10)], num_teams=12)
        assert model.is_thin is True
        assert model.profiles[TeamKey("t.2")].has_enough_history is False

    def test_enough_history_clears_the_flag(self) -> None:
        bids = [bid("t.2", Position.RB, 10 + i) for i in range(MIN_BIDS_FOR_A_PROFILE)]
        bids += [bid("t.3", Position.WR, 5 + i) for i in range(MIN_BIDS_FOR_A_PROFILE)]
        model = build_model("461.l.1", bids, num_teams=12)
        assert model.is_thin is False
        assert model.profiles[TeamKey("t.2")].has_enough_history is True

    def test_what_each_manager_buys_is_recorded(self) -> None:
        bids = [
            bid("t.2", Position.RB, 10),
            bid("t.2", Position.RB, 12),
            bid("t.2", Position.WR, 4),
        ]
        model = build_model("461.l.1", bids, num_teams=12)
        profile = model.profiles[TeamKey("t.2")]
        assert profile.positions_bought == {Position.RB: 2, Position.WR: 1}
        assert profile.wants(Position.RB) is True
        assert profile.wants(Position.TE) is False

    def test_the_real_fixture_builds_a_model(self) -> None:
        bids = parse_transactions(load("transactions.json"))
        model = build_model("461.l.1000", bids, num_teams=12)
        assert model.total_bids_observed == 8
        assert Position.RB in model.clearing_by_position
        assert model.weeks_observed == (3, 4, 5, 6)


class TestCredibleBidders:
    """The number that actually moves the bid. (n-1)/n: n=2 shades to 50%, n=6 to 83%."""

    def test_a_manager_who_cannot_afford_the_clearing_price_is_not_competition(self) -> None:
        bids = [bid("t.2", Position.RB, 20 + i) for i in range(MIN_BIDS_FOR_A_PROFILE)]
        model = build_model("461.l.1", bids, num_teams=12)
        budgets = {TeamKey("t.2"): 60, TeamKey("t.3"): 3, TeamKey("t.4"): 2}
        assert credible_bidders(model, Position.RB, budgets, week=5) == 1

    def test_an_unknown_manager_counts_as_live(self) -> None:
        """Absence of evidence is not evidence he will sit this one out."""
        bids = [bid("t.2", Position.RB, 10 + i) for i in range(MIN_BIDS_FOR_A_PROFILE)]
        model = build_model("461.l.1", bids, num_teams=12)
        budgets = {TeamKey("t.2"): 60, TeamKey("t.99"): 60}
        assert credible_bidders(model, Position.RB, budgets, week=5) == 2

    def test_a_manager_with_no_appetite_for_the_position_is_excluded(self) -> None:
        bids = [bid("t.3", Position.WR, 10 + i) for i in range(MIN_BIDS_FOR_A_PROFILE)]
        bids += [bid("t.2", Position.RB, 10 + i) for i in range(MIN_BIDS_FOR_A_PROFILE)]
        model = build_model("461.l.1", bids, num_teams=12)
        budgets = {TeamKey("t.2"): 60, TeamKey("t.3"): 60}
        assert credible_bidders(model, Position.RB, budgets, week=5) == 1

    def test_a_late_season_hoarder_counts_whatever_his_history_says(self) -> None:
        """Unspent FAAB is worth nothing at season end, so $60 in week 10 is not frugality
        -- it is about to be spent on whatever comes up next."""
        bids = [bid("t.3", Position.WR, 5 + i) for i in range(MIN_BIDS_FOR_A_PROFILE)]
        model = build_model("461.l.1", bids, num_teams=12)
        budgets = {TeamKey("t.3"): 80}
        assert credible_bidders(model, Position.RB, budgets, week=5) == 0
        assert credible_bidders(model, Position.RB, budgets, week=10) == 1

    def test_a_known_roster_need_narrows_the_field(self) -> None:
        bids = [bid(f"t.{i}", Position.RB, 10) for i in (2, 3, 4)]
        model = build_model("461.l.1", bids, num_teams=12)
        budgets = {TeamKey("t.2"): 60, TeamKey("t.3"): 60, TeamKey("t.4"): 60}
        needs = {TeamKey("t.2"): True, TeamKey("t.3"): False, TeamKey("t.4"): False}
        assert credible_bidders(model, Position.RB, budgets, 5, roster_needs=needs) == 1

    def test_an_unknown_roster_need_counts_the_manager_in(self) -> None:
        bids = [bid(f"t.{i}", Position.RB, 10) for i in (2, 3)]
        model = build_model("461.l.1", bids, num_teams=12)
        budgets = {TeamKey("t.2"): 60, TeamKey("t.3"): 60}
        assert credible_bidders(model, Position.RB, budgets, 5, roster_needs={}) == 2

    def test_it_is_an_upper_bound_and_the_docstring_says_which_way_that_errs(self) -> None:
        """The sign of a known bias has to be written down. An upper bound on n makes
        (n-1)/n shade LESS, so this bids high, not low. A correction whose direction is
        unknown is worse than no correction."""
        doc = credible_bidders.__doc__ or ""
        assert "upper bound" in doc
        assert "higher" in doc
        assert "not who did" in doc


class TestSayingWhatIsNotKnown:
    def test_the_module_states_the_winning_bids_only_limitation(self) -> None:
        import ff.domain.opponent as module

        doc = module.__doc__ or ""
        assert "winning bids only" in doc.lower()
        assert "not observable" in doc.lower()

    def test_clearing_price_reports_its_own_provenance(self) -> None:
        bids = [bid("t.2", Position.RB, 20), bid("t.3", Position.RB, 30)]
        model = build_model("461.l.1", bids, num_teams=12)
        price, why = expected_clearing_price(model, Position.RB)
        assert price == 25.0
        assert "winning bids only" in why.lower()
        assert "[empirical]" in why

    def test_a_position_with_no_history_falls_back_and_says_so(self) -> None:
        model = build_model("461.l.1", [bid("t.2", Position.RB, 20)], num_teams=12)
        price, why = expected_clearing_price(model, Position.TE)
        assert price == 20.0
        assert "falls back" in why

    def test_an_empty_league_history_invents_nothing(self) -> None:
        model = build_model("461.l.1", [], num_teams=12)
        price, why = expected_clearing_price(model, Position.RB)
        assert price == 0.0
        assert "would be invented" in why
        assert "[theory]" in why

    def test_hoarding_is_not_claimed_early_in_the_season(self) -> None:
        bids = [bid("t.2", Position.RB, 10)]
        profile = build_model("461.l.1", bids, num_teams=12).profiles[TeamKey("t.2")]
        assert is_hoarding(profile, budget=90, week=3) is False
