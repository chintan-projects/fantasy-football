"""The MCP tools, end to end against recorded fixtures.

Two things are under test and they are different. The first is that the tools compute
real answers -- a lineup comes back with a win probability and a band, a board comes back
ranked. The second is the part that matters more: that the tool *surface* keeps its
promises. Claude sees only names, descriptions and annotations, and acts on them. A tool
that quietly stops being read-only, or a description that drops its caveat, is a defect
the type checker cannot see, so it is asserted here.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fakes import FIXTURE_WEEK, FixtureLeague, fixture_sources

from ff.adapters.store import Store
from ff.api.deps import Deps
from ff.api.mcp import build_server, http_app
from ff.core.config import Settings

READ_ONLY_TOOLS = {
    "ff_recommend_lineup",
    "ff_waiver_board",
    "ff_recommend_bid",
    "ff_my_roster",
    "ff_league_transactions",
    "ff_list_preferences",
    "ff_how_am_i_doing",
}
WRITING_TOOLS = {"ff_record_preference", "ff_propose_claim", "ff_confirm"}


class FixtureSleeper:
    """The week oracle. Fixed, because the fixtures are week 2 recordings."""

    def current_week(self) -> int:
        return FIXTURE_WEEK


def build(tmp_path: Path, **overrides: Any) -> Deps:
    config = Settings(
        yahoo_league_key="470.l.1000",
        yahoo_team_key="470.l.1000.t.3",
        database_path=str(tmp_path / "ff.db"),
        monte_carlo_draws=400,  # enough to be a real simulation, fast enough for CI
        random_seed=7,
        **overrides,
    )
    return Deps(
        yahoo=FixtureLeague(),
        sources=list(fixture_sources()),
        store=Store(config.database_path),
        sleeper=FixtureSleeper(),  # type: ignore[arg-type]
        config=config,
    )


def call(deps: Deps, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    server = build_server(deps)
    result = asyncio.run(server.call_tool(name, args or {}))
    assert isinstance(result.structured_content, dict)
    return result.structured_content


def tools(deps: Deps) -> dict[str, Any]:
    return {t.name: t for t in asyncio.run(build_server(deps).list_tools())}


# ---- the surface Claude sees ------------------------------------------------------


def test_every_promised_tool_is_registered(tmp_path: Path) -> None:
    assert set(tools(build(tmp_path))) == READ_ONLY_TOOLS | WRITING_TOOLS


def test_read_tools_are_annotated_read_only(tmp_path: Path) -> None:
    for name, tool in tools(build(tmp_path)).items():
        if name in READ_ONLY_TOOLS:
            assert tool.annotations is not None, name
            assert tool.annotations.read_only_hint is True, name


def test_only_confirm_is_annotated_destructive(tmp_path: Path) -> None:
    """The annotation is a hint, not a guard -- but a wrong hint is a lie to the client."""
    destructive = {
        name
        for name, tool in tools(build(tmp_path)).items()
        if tool.annotations and tool.annotations.destructive_hint
    }
    assert destructive == {"ff_confirm"}


def test_propose_is_not_read_only_and_not_destructive(tmp_path: Path) -> None:
    """It writes an approval row, so it is not read-only. It spends nothing, so it is not
    destructive. Marking it either way would misdescribe the one safety boundary here."""
    tool = tools(build(tmp_path))["ff_propose_claim"]
    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is False
    assert tool.annotations.destructive_hint is False


def test_every_tool_has_a_description(tmp_path: Path) -> None:
    for name, tool in tools(build(tmp_path)).items():
        assert tool.description and len(tool.description) > 80, name


def test_the_descriptions_carry_the_caveats_that_stop_overclaiming(tmp_path: Path) -> None:
    """CLAUDE.md section 3. A tool description is documentation the model acts on, so the
    honesty rule applies to it exactly as it applies to the UI."""
    registered = tools(build(tmp_path))
    assert "3-23%" in (registered["ff_recommend_lineup"].description or "")
    assert "winning" in (registered["ff_league_transactions"].description or "").lower()
    assert "85-90%" in (registered["ff_recommend_bid"].description or "")


def test_claims_in_descriptions_are_labelled(tmp_path: Path) -> None:
    for name in ("ff_recommend_lineup", "ff_recommend_bid", "ff_waiver_board"):
        description = tools(build(tmp_path))[name].description or ""
        assert "[empirical]" in description or "[theory]" in description, name


def test_the_write_tools_tell_the_model_not_to_chain_them(tmp_path: Path) -> None:
    propose = tools(build(tmp_path))["ff_propose_claim"].description or ""
    assert "submits nothing" in propose.lower()
    assert "same turn" in propose.lower()


# ---- the decisions ----------------------------------------------------------------


def test_recommend_lineup_returns_a_decision_not_a_roster(tmp_path: Path) -> None:
    plan = call(build(tmp_path), "ff_recommend_lineup")
    assert plan["week"] == FIXTURE_WEEK
    assert 0.0 <= plan["win_probability"] <= 1.0
    low, high = plan["win_probability_band"]
    assert low <= plan["win_probability"] <= high
    assert plan["starters"], "a plan with no starters is not a plan"
    assert plan["caveats"], "a recommendation with no caveats is overclaiming"
    assert plan["recommendation_id"]


def test_recommend_lineup_reports_which_sources_it_used(tmp_path: Path) -> None:
    plan = call(build(tmp_path), "ff_recommend_lineup")
    assert {s["name"] for s in plan["sources"]} == {"fixture-a", "fixture-b"}
    assert all(s["age_seconds"] is not None for s in plan["sources"])


def test_the_recommendation_and_its_inputs_are_persisted(tmp_path: Path) -> None:
    """Without this the app can never answer "was it right?", which is the whole point."""
    deps = build(tmp_path)
    plan = call(deps, "ff_recommend_lineup")
    stored = deps.store.list_recommendations(week=FIXTURE_WEEK)
    assert [r.id for r in stored] == [plan["recommendation_id"]]
    assert stored[0].reasoning
    snapshot = deps.store.latest_snapshot(FIXTURE_WEEK, "lineup")
    assert snapshot is not None and snapshot["projections"]


def test_waiver_board_ranks_by_vorp_and_names_the_displaced_player(tmp_path: Path) -> None:
    board = call(build(tmp_path), "ff_waiver_board", {"limit": 5})
    targets = board["targets"]
    assert targets, "no targets from a fixture pool that contains some"
    assert [t["vorp_per_week"] for t in targets] == sorted(
        (t["vorp_per_week"] for t in targets), reverse=True
    )
    for target in targets:
        assert target["vorp_per_week"] > 0
        assert "against your current worst" in target["reason"]


def test_waiver_board_filters_by_position(tmp_path: Path) -> None:
    board = call(build(tmp_path), "ff_waiver_board", {"position": "RB", "limit": 10})
    assert {t["position"] for t in board["targets"]} <= {"RB"}


def test_my_roster_is_a_listing_with_no_judgment_in_it(tmp_path: Path) -> None:
    roster = call(build(tmp_path), "ff_my_roster")
    assert roster["players"]
    assert not any("projection" in p or "rank" in p for p in roster["players"])


def test_transactions_says_out_loud_that_losing_bids_are_missing(tmp_path: Path) -> None:
    log = call(build(tmp_path), "ff_league_transactions")
    assert log["transactions"]
    assert "losing bids" in log["note"].lower()


# ---- preferences ------------------------------------------------------------------


def test_a_preference_round_trips(tmp_path: Path) -> None:
    deps = build(tmp_path)
    call(deps, "ff_record_preference", {"text": "Never drop Bijan.", "kind": "do_not_drop"})
    listed = call(deps, "ff_list_preferences")["preferences"]
    assert [p["text"] for p in listed] == ["Never drop Bijan."]


# ---- the two-step write -----------------------------------------------------------


def propose(deps: Deps) -> dict[str, Any]:
    board = call(deps, "ff_waiver_board", {"limit": 1})
    player = board["targets"][0]["player"]
    return call(deps, "ff_propose_claim", {"player": player})


def test_propose_submits_nothing(tmp_path: Path) -> None:
    deps = build(tmp_path)
    plan = propose(deps)
    assert plan["submitted"] is False
    assert plan["approval_id"]
    assert deps.store.unfinished_writes() == []


def test_propose_shows_the_budget_before_and_after(tmp_path: Path) -> None:
    plan = propose(build(tmp_path))
    assert plan["remaining_budget_after"] == plan["remaining_budget_now"] - plan["bid"]


def test_confirm_spends_the_approval_once(tmp_path: Path) -> None:
    deps = build(tmp_path)
    approval_id = propose(deps)["approval_id"]
    first = call(deps, "ff_confirm", {"approval_id": approval_id})
    assert first["submitted"] is True
    second = call(deps, "ff_confirm", {"approval_id": approval_id})
    assert second["submitted"] is False
    assert "already used" in second["error"]


def test_confirm_rejects_an_approval_it_never_issued(tmp_path: Path) -> None:
    result = call(build(tmp_path), "ff_confirm", {"approval_id": "not-a-real-id"})
    assert result["submitted"] is False
    assert "No such approval" in result["error"]


def test_confirm_is_a_dry_run_by_default(tmp_path: Path) -> None:
    """CLAUDE.md 5.5. The default configuration must not be able to spend money."""
    deps = build(tmp_path)
    result = call(deps, "ff_confirm", {"approval_id": propose(deps)["approval_id"]})
    assert result["executor"] == "dryrun"
    assert "nothing was sent" in result["note"].lower()


def test_confirm_journals_intent_and_result(tmp_path: Path) -> None:
    deps = build(tmp_path)
    call(deps, "ff_confirm", {"approval_id": propose(deps)["approval_id"]})
    assert deps.store.unfinished_writes() == [], "a journalled intent with no result"


def test_a_bid_over_the_live_budget_is_rejected_not_clamped(tmp_path: Path) -> None:
    """The budget is re-read at confirm time, so a balance that fell in between -- a bid
    placed from the Yahoo app -- stops the write rather than shrinking it."""
    deps = build(tmp_path)
    approval_id = propose(deps)["approval_id"]
    object.__setattr__(deps, "yahoo", FixtureLeague(faab=0))
    result = call(deps, "ff_confirm", {"approval_id": approval_id})
    assert result["submitted"] is False
    assert "budget" in result["error"].lower()


def test_an_unknown_player_is_refused_rather_than_priced(tmp_path: Path) -> None:
    result = call(build(tmp_path), "ff_propose_claim", {"player": "Nobody At All"})
    assert "error" in result
    assert result.get("approval_id") is None


# ---- the bid ----------------------------------------------------------------------


def test_recommend_bid_shows_its_whole_shading_breakdown(tmp_path: Path) -> None:
    deps = build(tmp_path)
    player = call(deps, "ff_waiver_board", {"limit": 1})["targets"][0]["player"]
    bid = call(deps, "ff_recommend_bid", {"player": player})
    assert bid["recommended_bid"] <= bid["reservation_value"]
    assert bid["recommended_bid"] <= bid["remaining_budget"]
    assert set(bid["shading"]) == {"for_competition", "for_winners_curse", "option_value"}
    assert bid["how_bidders_were_estimated"]


def test_the_bidder_estimate_says_which_way_it_errs(tmp_path: Path) -> None:
    """Both unknowns -- winning bids only, rival budgets unseen -- push toward bidding too
    high. The tool has to say so rather than present a number with no direction on it."""
    deps = build(tmp_path)
    player = call(deps, "ff_waiver_board", {"limit": 1})["targets"][0]["player"]
    how = call(deps, "ff_recommend_bid", {"player": player})["how_bidders_were_estimated"]
    assert "[theory]" in how or "[folk]" in how


def test_no_tool_leaks_a_yahoo_shaped_dict(tmp_path: Path) -> None:
    """Adapters convert foreign shapes at their boundary (CLAUDE.md 2.2). A ``fantasy_content``
    key anywhere in a result means a provider dict escaped all the way to the model."""
    deps = build(tmp_path)
    for name, args in (
        ("ff_recommend_lineup", {}),
        ("ff_waiver_board", {"limit": 3}),
        ("ff_my_roster", {}),
        ("ff_league_transactions", {}),
    ):
        assert "fantasy_content" not in str(call(deps, name, args)), name


def test_the_plan_and_its_own_verdicts_never_disagree(tmp_path: Path) -> None:
    """BUG-008. Found by reading the demo output, not by a failing assertion.

    The plan named Brock Bowers at TE while the verdict for that same slot said to start
    Trey McBride. Both came out of one call. A recommendation that contradicts itself is
    worse than no recommendation -- the owner cannot act on it and has no way to tell which
    half to trust.
    """
    plan = call(build(tmp_path), "ff_recommend_lineup")
    started = {(s["slot"], s["player"]) for s in plan["starters"]}
    for decision in plan["contested_slots"]:
        assert (decision["slot"], decision["start"]) in started, decision


def test_the_asgi_app_starts_and_serves_health(tmp_path: Path) -> None:
    """Boot the real app the way uvicorn does.

    Every other test here calls build_server directly, so none of them would have caught
    the lifespan wiring being wrong -- and it was. A server that imports cleanly and then
    fails on startup looks fine from the inside.
    """
    import httpx

    deps = build(tmp_path)
    app = http_app(deps, authenticate=False)

    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=transport, base_url="http://t") as client,
        ):
            return await client.get("/health")

    response = asyncio.run(go())
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["authenticated"] is False
    assert body["write_executor"] == "dryrun"


def test_the_mcp_endpoint_is_mounted_where_claude_expects_it(tmp_path: Path) -> None:
    """/mcp, Streamable HTTP. A GET without the session handshake is refused, not 404."""
    import httpx

    app = http_app(build(tmp_path), authenticate=False)

    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=transport, base_url="http://t") as client,
        ):
            return await client.get("/mcp")

    assert asyncio.run(go()).status_code != 404


def test_the_server_never_asks_claude_for_a_metadata_document(tmp_path: Path) -> None:
    """BUG-011. The discovery document must not advertise CIMD support.

    When it does, Claude identifies itself with a client id that is a URL and this server
    has to go fetch that URL before it can recognise the client. Cloudflare answers a
    datacenter IP with a bot challenge, so on Fly the fetch returns 403 HTML, the client
    is not found, and every connection attempt ends at "Client Not Registered". The same
    fetch from a laptop succeeds, which is why this survived local testing.

    Dynamic client registration needs no outbound call at all. Asserting the absence of
    the advertisement is the honest test: the bug was a capability we claimed and could
    not deliver from where we actually run.
    """
    import httpx

    deps = build(
        tmp_path,
        github_client_id="test-client-id",
        github_client_secret="test-client-secret",
        allowed_github_login="somebody",
        mcp_base_url="https://ff-copilot.test",
    )
    app = http_app(deps, authenticate=True)

    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=transport, base_url="http://t") as client,
        ):
            return await client.get("/.well-known/oauth-authorization-server")

    response = asyncio.run(go())
    assert response.status_code == 200
    metadata = response.json()
    assert "client_id_metadata_document_supported" not in metadata, metadata
    assert metadata["registration_endpoint"], "DCR is the fallback; it has to be offered"


def test_the_score_is_empty_early_and_says_so_rather_than_claiming_a_record(
    tmp_path: Path,
) -> None:
    """An empty calibration report is the normal state in September.

    It must not read as a failure, and it must not read as a clean sheet either -- an app
    with no outcomes recorded has not been right, it has been unmeasured.
    """
    result = call(build(tmp_path), "ff_how_am_i_doing")
    assert result["scored"] is False
    assert result["weeks_scored"] == []
    assert result["sources"] == []
    assert result["note"]


def test_the_score_never_claims_a_winner_it_cannot_support(tmp_path: Path) -> None:
    """Two sources, one week, a dozen players. The honest answer is "not enough yet"."""
    deps = build(tmp_path)
    call(deps, "ff_recommend_lineup")  # writes a snapshot and a recommendation

    snapshot = deps.store.latest_snapshot(FIXTURE_WEEK, "lineup")
    assert snapshot is not None
    for player_id in snapshot["projections"]:
        deps.store.record_outcome(FIXTURE_WEEK, player_id, 11.0)

    result = call(deps, "ff_how_am_i_doing")
    assert result["scored"] is True
    assert result["weeks_scored"] == [FIXTURE_WEEK]
    assert result["sources"], "per-source scores must survive the snapshot round trip"
    for verdict in result["ensemble_versus_each_source"]:
        assert "Too few weeks" in verdict, verdict


def test_the_lineup_grade_compares_against_the_obvious_lineup_not_perfection(
    tmp_path: Path,
) -> None:
    deps = build(tmp_path)
    call(deps, "ff_recommend_lineup")
    snapshot = deps.store.latest_snapshot(FIXTURE_WEEK, "lineup")
    assert snapshot is not None
    for index, player_id in enumerate(snapshot["projections"]):
        deps.store.record_outcome(FIXTURE_WEEK, player_id, float(index))

    result = call(deps, "ff_how_am_i_doing")
    assert result["weeks_with_a_lineup_graded"] == 1
    graded = result["lineups"][0]
    assert graded["points_if_you_started_the_highest_projections"] is not None
    assert graded["points_left_on_the_bench"] >= 0
