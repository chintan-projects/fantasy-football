from __future__ import annotations

import pytest

from ff.domain.blend import SingleSourceError, blend, implied_team_total, wind_adjustment
from ff.domain.models import PlayerId, Position


def test_single_source_is_an_error_not_a_setup() -> None:
    with pytest.raises(SingleSourceError):
        blend(PlayerId("p"), Position.WR, {"espn": 12.0})


def test_blend_averages_and_keeps_uncertainties_separate() -> None:
    proj = blend(PlayerId("p"), Position.WR, {"espn": 10.0, "fp": 14.0}, p_zero=0.05)
    assert proj.mean == pytest.approx(12.0)
    assert proj.epistemic_sd == pytest.approx(2.0)  # source disagreement
    assert proj.aleatoric_sd > proj.epistemic_sd  # week-to-week volatility dominates
    assert proj.sources == ("espn", "fp")


def test_observed_variance_is_shrunk_toward_the_position_prior() -> None:
    few = blend(
        PlayerId("p"), Position.RB, {"a": 12.0, "b": 12.0}, observed_sd=1.0, games_observed=2
    )
    many = blend(
        PlayerId("p"), Position.RB, {"a": 12.0, "b": 12.0}, observed_sd=1.0, games_observed=40
    )
    assert few.aleatoric_sd > many.aleatoric_sd  # small samples trust the prior more


def test_implied_team_total() -> None:
    # 47.5 total, favored by 3 -> 25.25 implied
    assert implied_team_total(47.5, -3.0) == pytest.approx(25.25)


def test_wind_below_twenty_changes_nothing() -> None:
    """96% of games. Returning 1.0 is the finding, not a stub."""
    assert wind_adjustment(15.0, is_indoor=False) == 1.0
    assert wind_adjustment(35.0, is_indoor=True) == 1.0
    assert wind_adjustment(28.0, is_indoor=False) < 1.0
