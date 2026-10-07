"""Manual league mode: names read off a screenshot, and the league built from them.

The failure this suite exists to prevent is a quiet one. A misread name that resolves to
the wrong player produces a lineup that looks complete and is wrong, and nothing
downstream can tell. So most of these tests are about refusing: two candidates, no
candidate, a duplicate, an input too old to trust.

The player list is a slice of Sleeper's real ``/players/nfl`` response, recorded
2026-10-06, chosen for its hard cases -- Bijan and Brian Robinson are both Falcons running
backs, and there is a guard named Josh Allen.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ff.adapters.manual import (
    MAX_AGE_SECONDS,
    MY_TEAM,
    ManualLeague,
    PlayerDirectory,
    PlayerEntry,
    parse_slot,
)
from ff.adapters.sleeper import parse_players
from ff.adapters.store import Store
from ff.core.errors import MissingInput
from ff.domain.models import Position, Slot
from ff.services import league_entry

PLAYERS = Path(__file__).parent / "fixtures" / "sleeper" / "players_sample.json"


@pytest.fixture(scope="module")
def directory() -> PlayerDirectory:
    return PlayerDirectory(parse_players(json.loads(PLAYERS.read_text())))


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "ff.db")


def resolve(
    directory: PlayerDirectory, name: str, position: str | None = None, team: str | None = None
) -> str:
    match = directory.resolve(PlayerEntry(name=name, position=position, team=team))
    return f"{match.how}:{match.player.name if match.player else ''}"


# ---- the player list -------------------------------------------------------------


class TestSleeperPlayers:
    def test_only_positions_a_fantasy_roster_holds(self) -> None:
        players = parse_players(json.loads(PLAYERS.read_text()))
        assert {p.position for p in players} <= set(Position)
        assert not any(p.name == "Josh Allen" and p.position is not Position.QB for p in players)

    def test_a_defense_is_named_city_and_nickname(self) -> None:
        players = parse_players(json.loads(PLAYERS.read_text()))
        broncos = next(p for p in players if p.id == "DEN")
        assert (broncos.name, broncos.position, broncos.team) == (
            "Denver Broncos",
            Position.DEF,
            "DEN",
        )

    def test_eligibility_follows_position(self) -> None:
        players = parse_players(json.loads(PLAYERS.read_text()))
        chase = next(p for p in players if p.name == "Ja'Marr Chase")
        assert chase.eligible_slots == {Slot.WR, Slot.FLEX, Slot.SUPERFLEX}


# ---- matching names --------------------------------------------------------------


class TestResolve:
    def test_full_name(self, directory: PlayerDirectory) -> None:
        assert resolve(directory, "Josh Allen") == "exact:Josh Allen"

    def test_punctuation_and_suffixes_do_not_matter(self, directory: PlayerDirectory) -> None:
        assert resolve(directory, "Amon Ra St Brown") == "exact:Amon-Ra St. Brown"
        assert resolve(directory, "Kenneth Walker III") == "exact:Kenneth Walker"

    def test_the_abbreviated_first_name_the_yahoo_app_shows(
        self, directory: PlayerDirectory
    ) -> None:
        assert resolve(directory, "J. Allen", "QB", "BUF") == "initial:Josh Allen"
        assert resolve(directory, "A. St. Brown", "WR") == "initial:Amon-Ra St. Brown"

    def test_two_teammates_with_the_same_initial_are_refused_not_guessed(
        self, directory: PlayerDirectory
    ) -> None:
        match = directory.resolve(PlayerEntry("B. Robinson", "RB", "ATL"))
        assert match.player is None and match.how == "ambiguous"
        assert {p.name for p in match.options} == {"Bijan Robinson", "Brian Robinson"}

    def test_position_settles_a_shared_initial(self, directory: PlayerDirectory) -> None:
        """Christian McCaffrey is a back and Luke McCaffrey a receiver."""
        assert resolve(directory, "C. McCaffrey", "RB") == "initial:Christian McCaffrey"
        assert directory.resolve(PlayerEntry("McCaffrey")).player is None

    def test_a_misspelling_is_corrected_and_labelled_as_such(
        self, directory: PlayerDirectory
    ) -> None:
        assert resolve(directory, "Puka Nakua", "WR") == "close:Puka Nacua"

    def test_a_name_that_matches_nobody_is_unknown(self, directory: PlayerDirectory) -> None:
        assert resolve(directory, "Zzz Nobody") == "unknown:"

    def test_a_wrong_team_does_not_lose_a_unique_name(self, directory: PlayerDirectory) -> None:
        """Yahoo shows a traded player's new team a day before Sleeper does."""
        assert resolve(directory, "Puka Nacua", "WR", "KC") == "exact:Puka Nacua"

    def test_a_wrong_position_does(self, directory: PlayerDirectory) -> None:
        assert resolve(directory, "Puka Nacua", "TE") == "unknown:"

    @pytest.mark.parametrize(
        "name,position,team",
        [
            ("Broncos D/ST", None, None),
            ("Denver", "DEF", None),
            ("Denver Broncos", None, None),
            ("DEN", "DEF", None),
            ("Defense", "DEF", "DEN"),
            ("Denver DST", None, None),
        ],
    )
    def test_every_way_a_defense_is_written(
        self, directory: PlayerDirectory, name: str, position: str | None, team: str | None
    ) -> None:
        assert resolve(directory, name, position, team) == "defense:Denver Broncos"

    def test_a_city_with_two_teams_is_ambiguous(self, directory: PlayerDirectory) -> None:
        match = directory.resolve(PlayerEntry("New York", "DEF"))
        assert match.how == "ambiguous"
        assert {p.team for p in match.options} == {"NYG", "NYJ"}


@pytest.mark.parametrize(
    "raw,slot",
    [
        ("W/R/T", Slot.FLEX),
        ("flex", Slot.FLEX),
        ("BN", Slot.BENCH),
        ("IR", Slot.IR),
        ("WR", Slot.WR),
    ],
)
def test_slots_as_yahoo_labels_them(raw: str, slot: Slot) -> None:
    assert parse_slot(raw) is slot


# ---- entering a roster -----------------------------------------------------------

MY_ROSTER = [
    PlayerEntry("J. Allen", "QB", "BUF", "QB"),
    PlayerEntry("Bijan Robinson", "RB", "ATL", "RB"),
    PlayerEntry("Christian McCaffrey", "RB", "SF", "RB"),
    PlayerEntry("Ja'Marr Chase", "WR", "CIN", "WR"),
    PlayerEntry("Puka Nacua", "WR", "LAR", "WR"),
    PlayerEntry("A. St. Brown", "WR", "DET", "WR"),
    PlayerEntry("Travis Kelce", "TE", "KC", "TE"),
    PlayerEntry("Rashid Shaheed", "WR", None, "W/R/T"),
    PlayerEntry("Brandon Aubrey", "K", "DAL", "K"),
    PlayerEntry("Broncos D/ST", "DEF", "DEN", "DEF"),
    PlayerEntry("Tyler Shough", "QB", "NO", "BN"),
    PlayerEntry("Kenneth Walker", "RB", None, "BN"),
    PlayerEntry("Tank Bigsby", "RB", None, "BN"),
    PlayerEntry("Jayden Reed", "WR", "GB", "BN"),
    PlayerEntry("Sam LaPorta", "TE", "DET", "BN"),
    PlayerEntry("Jake Elliott", "K", "PHI", "BN"),
]


class TestEnterRoster:
    def test_a_clean_roster_is_saved_and_read_back(
        self, store: Store, directory: PlayerDirectory
    ) -> None:
        result = league_entry.enter_roster(store, directory, MY_ROSTER, week=5, whose="mine")
        assert result.saved and not result.problems and not result.warnings
        roster = ManualLeague(store).roster(5)
        assert len(roster.players) == 16, "Yahoo's default roster is 16"
        assert {s.player.name for s in roster.starters} >= {"Josh Allen", "Denver Broncos"}
        assert next(s for s in roster.spots if s.player.name == "Rashid Shaheed").slot is Slot.FLEX

    def test_one_ambiguous_name_saves_nothing(
        self, store: Store, directory: PlayerDirectory
    ) -> None:
        """A lineup over fourteen of fifteen players looks complete and is not."""
        bad = [*MY_ROSTER[:-1], PlayerEntry("B. Robinson", "RB", "ATL")]
        result = league_entry.enter_roster(store, directory, bad, week=5, whose="mine")
        assert not result.saved
        assert "Bijan Robinson" in result.problems[0] and "Brian Robinson" in result.problems[0]
        assert store.latest_league_input("roster") is None

    def test_the_same_player_twice_is_a_misread(
        self, store: Store, directory: PlayerDirectory
    ) -> None:
        twice = [*MY_ROSTER, PlayerEntry("Josh Allen", "QB")]
        result = league_entry.enter_roster(store, directory, twice, week=5, whose="mine")
        assert not result.saved
        assert "same player" in result.problems[0]

    def test_an_unknown_slot_is_refused(self, store: Store, directory: PlayerDirectory) -> None:
        odd = [PlayerEntry("Josh Allen", "QB", "BUF", "CAPTAIN")]
        assert not league_entry.enter_roster(store, directory, odd, week=5, whose="mine").saved

    def test_a_short_roster_is_saved_with_a_warning(
        self, store: Store, directory: PlayerDirectory
    ) -> None:
        """Probably a cropped screenshot. Saved, because the starters may be all that is
        known, but said out loud."""
        result = league_entry.enter_roster(store, directory, MY_ROSTER[:9], week=5, whose="mine")
        assert result.saved
        assert "Only 9 players" in result.warnings[0]

    def test_a_new_roster_replaces_the_old_one(
        self, store: Store, directory: PlayerDirectory
    ) -> None:
        league_entry.enter_roster(store, directory, MY_ROSTER, week=5, whose="mine")
        league_entry.enter_roster(store, directory, MY_ROSTER[:12], week=5, whose="mine")
        assert len(ManualLeague(store).roster(5).players) == 12


# ---- the league reader -----------------------------------------------------------


class TestManualLeague:
    def test_nothing_entered_asks_for_the_screenshot(self, store: Store) -> None:
        with pytest.raises(MissingInput, match="screenshot of your team page"):
            ManualLeague(store).roster(5)

    def test_a_roster_older_than_a_week_is_refused(
        self, store: Store, directory: PlayerDirectory
    ) -> None:
        league_entry.enter_roster(store, directory, MY_ROSTER, week=5, whose="mine")
        entered = store.latest_league_input("roster")
        assert entered is not None
        later = ManualLeague(store, clock=lambda: entered.entered_at + MAX_AGE_SECONDS + 86400)
        with pytest.raises(MissingInput, match="8 days ago"):
            later.roster(5)

    def test_the_opponent_belongs_to_one_week(
        self, store: Store, directory: PlayerDirectory
    ) -> None:
        opponent = [
            PlayerEntry("Lamar Jackson", "QB", "BAL"),
            PlayerEntry("Derrick Henry", "RB", "BAL"),
            PlayerEntry("Justin Jefferson", "WR", "MIN"),
            PlayerEntry("George Kittle", "TE", "SF", "BN"),
        ]
        league_entry.enter_roster(
            store, directory, opponent, week=5, whose="opponent", opponent_name="Gridiron Gang"
        )
        league = ManualLeague(store)
        matchup = league.matchup(5)
        assert str(matchup.opponent) == "opponent:Gridiron Gang"
        assert len(matchup.opponent_starters) == 3, "the benched tight end is not a starter"
        assert len(league.roster(5, str(matchup.opponent)).starters) == 3
        with pytest.raises(MissingInput, match="week 6"):
            league.matchup(6)

    def test_free_agents_drop_anyone_since_added_to_my_roster(
        self, store: Store, directory: PlayerDirectory
    ) -> None:
        pool = [PlayerEntry("Luke McCaffrey", "WR"), PlayerEntry("Jayden Reed", "WR")]
        league_entry.enter_free_agents(store, directory, pool, week=5)
        league_entry.enter_roster(store, directory, MY_ROSTER, week=5, whose="mine")
        assert [p.name for p in ManualLeague(store).free_agents()] == ["Luke McCaffrey"]

    def test_free_agents_skip_what_cannot_be_matched(
        self, store: Store, directory: PlayerDirectory
    ) -> None:
        pool = [
            PlayerEntry("Luke McCaffrey", "WR"),
            PlayerEntry("Zzz Nobody", "WR"),
            PlayerEntry("Garrett Wilson", "WR"),
        ]
        result = league_entry.enter_free_agents(store, directory, pool, week=5)
        assert result.saved and len(result.problems) == 1
        assert [p.name for p in ManualLeague(store).free_agents(position="WR")] == [
            "Luke McCaffrey",
            "Garrett Wilson",
        ]

    def test_the_balance_is_whatever_was_last_entered(self, store: Store) -> None:
        with pytest.raises(MissingInput, match="FAAB"):
            ManualLeague(store).faab_balance()
        league_entry.update_league(store, week=5, faab_remaining=86)
        assert ManualLeague(store).faab_balance() == 86

    def test_settings_default_to_yahoo_and_can_be_changed(self, store: Store) -> None:
        assert ManualLeague(store).league_settings().slot_counts[Slot.WR] == 3
        problems = league_entry.update_league(
            store,
            week=5,
            num_teams=10,
            roster_slots={"QB": 1, "RB": 2, "WR": 2, "FLEX": 2, "BN": 7},
        )
        settings = ManualLeague(store).league_settings()
        assert not problems
        assert settings.num_teams == 10
        assert settings.slot_counts[Slot.FLEX] == 2 and Slot.TE not in settings.slot_counts

    def test_a_bad_slot_changes_nothing_including_the_balance(self, store: Store) -> None:
        problems = league_entry.update_league(
            store, week=5, faab_remaining=50, roster_slots={"CAPTAIN": 1}
        )
        assert problems
        assert store.latest_league_input("faab") is None
        assert store.latest_league_input("settings") is None

    def test_transactions_sent_twice_count_once(
        self, store: Store, directory: PlayerDirectory
    ) -> None:
        bids = [
            league_entry.Bid(PlayerEntry("Luke McCaffrey", "WR"), 14, "Gridiron Gang", 3),
            league_entry.Bid(PlayerEntry("Jaylin Lane", "WR", "WAS"), 6, "Me", 4, mine=True),
        ]
        league_entry.enter_transactions(store, directory, bids)
        league_entry.enter_transactions(store, directory, bids)
        history = ManualLeague(store).transactions()
        assert len(history) == 2
        assert history[0].week == 4 and str(history[0].team_key) == MY_TEAM
        assert str(history[1].team_key) == "manager:Gridiron Gang"


# ---- what to ask for -------------------------------------------------------------


def test_the_status_lists_what_to_send(store: Store, directory: PlayerDirectory) -> None:
    states = {s.name: s for s in league_entry.input_states(store, week=5)}
    assert not states["your roster"].entered
    assert states["your roster"].ask and "team page" in states["your roster"].ask
    assert states["week 5 opponent"].ask and "matchup page" in states["week 5 opponent"].ask
    assert "assumed" in states["league settings"].detail

    league_entry.enter_roster(store, directory, MY_ROSTER, week=5, whose="mine")
    states = {s.name: s for s in league_entry.input_states(store, week=5)}
    assert states["your roster"].fresh and states["your roster"].ask is None


def test_decisions_say_where_their_inputs_came_from(
    store: Store, directory: PlayerDirectory
) -> None:
    league_entry.enter_roster(store, directory, MY_ROSTER, week=5, whose="mine")
    league_entry.update_league(store, week=5, faab_remaining=86)
    caveats = " ".join(league_entry.hand_entered_caveats(store))
    assert "within the last hour" in caveats
    assert "$86" in caveats and "not read from Yahoo" in caveats
    assert "assumed" in caveats
