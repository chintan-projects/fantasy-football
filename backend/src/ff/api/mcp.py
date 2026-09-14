"""The MCP server. This is the front door.

The one rule that governs every tool here: **tools return decisions, not raw data.**

The wrong shape is a tool that hands Claude a roster and lets it reason about who to start.
That reasoning is not reproducible, its inputs are never snapshotted, and it makes M6
calibration impossible -- the app could never answer "was it right?", which is the point of
the whole project (CLAUDE.md 2.5). So every tool that makes a judgment calls
``services/``, persists the input snapshot it decided on, and returns the computed answer
together with its reasoning.

Raw-data tools exist only where a human genuinely wants to browse: the roster listing and
the transaction log. Neither decides anything.

Two further rules show up in the tool set rather than in prose:

* **Nothing spends money in one hop.** ``ff_propose_claim`` computes a bid and returns an
  approval id; it submits nothing. ``ff_confirm`` submits, and only against an approval the
  owner has already seen. A single tool that could bid would be a tool the model could call
  on its own initiative.
* **Every claim is labelled.** Tool descriptions carry ``[empirical]``, ``[theory]`` or
  ``[folk]`` exactly as docs do, because a tool description is documentation that a model
  reads and acts on.

Transport is Streamable HTTP, which is what Claude's custom connectors expect.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.responses import JSONResponse

from ff.api import scheduler
from ff.api.auth import OnlyTheOwner, github_auth
from ff.api.deps import Deps, default_deps
from ff.api.payloads import lineup_plan, lineup_reasoning
from ff.core.errors import FFError
from ff.core.logging import get_logger
from ff.domain.models import Position
from ff.services import calibration
from ff.services import waivers as waiver_service
from ff.services.approvals import ApprovalStore, executor_for, submit_claim
from ff.services.recommend import recommend
from ff.services.week import load_week, to_week_inputs

log = get_logger(__name__)

INSTRUCTIONS = """\
Fantasy football decisions for one Yahoo league, one team, one owner.

Ask for a recommendation and this server computes it -- a correlated Monte Carlo over
blended projections, ranked by win probability rather than by projected points. Do not
re-derive its answers or reason over the raw numbers yourself: every recommendation is
snapshotted so it can be scored against what actually happened later, and a verdict you
reason to separately is not part of that record.

Read the caveats it returns and pass them on. Weekly projections explain roughly 3-23% of
the variance in actual points, so "too close to call" is a common and correct answer. A
recommendation stated more confidently than the tool stated it is a misquote.

Nothing is written to Yahoo except through ff_confirm, against an approval the owner has
already seen."""


def _read_only(idempotent: bool = True) -> ToolAnnotations:
    """A tool that computes or reads and changes nothing the owner can see."""
    return ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=idempotent,
        open_world_hint=True,
    )


def build_server(deps: Deps | None = None, auth: Any = None) -> FastMCP:
    """Construct the server. Dependencies are injected so tests drive the real tools."""
    d = deps if deps is not None else default_deps()
    mcp: FastMCP = FastMCP(name="fantasy-football-copilot", instructions=INSTRUCTIONS, auth=auth)
    approvals = ApprovalStore(d.store)

    def _week(week: int | None) -> int:
        """The week oracle is Sleeper. Never computed from a date (CLAUDE.md 2.6)."""
        return week if week is not None else d.sleeper.current_week()

    # ---- decisions ---------------------------------------------------------------

    @mcp.tool(
        name="ff_recommend_lineup",
        title="Recommend a starting lineup",
        annotations=_read_only(idempotent=False),
        description=(
            "Compute the starting lineup that maximizes win probability for this week, "
            "and explain every contested call.\n\n"
            "This runs a correlated Monte Carlo over the owner's roster against the "
            "opponent's actual starters, then ranks candidate lineups by P(win) rather "
            "than by projected points. Those differ: as a heavy favorite the higher floor "
            "wins, as a heavy underdog the higher ceiling wins even at a lower mean. "
            "[theory]\n\n"
            "Returns the plan, the win probability WITH its simulation band, a per-slot "
            "verdict including explicit 'too close to call' rows, the sources used and how "
            "stale each was, and any players it could not project.\n\n"
            "The gaps are usually inside the noise. Weekly projections explain 3-23% of "
            "variance in actual points and start/sit optimization is worth roughly 1-3 "
            "points of win probability a week. [empirical] Report the numbers as given; "
            "do not present a 0.4-point edge as a decisive call.\n\n"
            "The inputs are snapshotted so the recommendation can be scored later. Not "
            "idempotent: calling it again re-simulates and writes a new snapshot."
        ),
    )
    def ff_recommend_lineup(
        week: Annotated[
            int | None, Field(description="NFL week. Defaults to the current week.")
        ] = None,
    ) -> dict[str, Any]:
        wk = _week(week)
        bundle = load_week(d.yahoo, d.sources, wk)
        snapshot_id = d.store.save_snapshot(wk, "lineup", bundle.snapshot())
        result = recommend(
            to_week_inputs(bundle, draws=d.config.monte_carlo_draws, seed=d.config.random_seed)
        )

        plan = lineup_plan(result, bundle, wk)
        plan["recommendation_id"] = d.store.save_recommendation(
            wk, "lineup", plan, lineup_reasoning(result), snapshot_id=snapshot_id
        )
        return plan

    @mcp.tool(
        name="ff_waiver_board",
        title="Rank waiver targets",
        annotations=_read_only(),
        description=(
            "Rank available free agents by rest-of-season value over the owner's OWN "
            "current replacement at that position.\n\n"
            "Not by projected points. A 12-point receiver is worthless if the worst "
            "receiver already rostered projects for 11.5, and a 9-point back is a major "
            "add if the current RB2 projects for 4. The board answers the question the "
            "owner is actually deciding. [theory]\n\n"
            "Every entry names the player it would displace and the weekly gap over him. "
            "Players fewer than two sources cover are left off entirely rather than ranked "
            "on one source's guess."
        ),
    )
    def ff_waiver_board(
        position: Annotated[
            str | None, Field(description="Filter to QB, RB, WR, TE, K or DEF.")
        ] = None,
        week: Annotated[int | None, Field(description="Defaults to the current week.")] = None,
        limit: Annotated[int, Field(description="How many targets to return.", ge=1, le=50)] = 10,
    ) -> dict[str, Any]:
        wk = _week(week)
        pos = Position(position.upper()) if position else None
        bundle = load_week(d.yahoo, d.sources, wk, with_opponent=False)
        entries = waiver_service.build_board(d.yahoo, d.sources, bundle, position=pos, limit=limit)
        return {
            "week": wk,
            "targets": [
                {
                    "player": e.target.player.name,
                    "player_id": str(e.target.player.id),
                    "position": e.target.player.position.value,
                    "nfl_team": e.target.player.team,
                    "projected_points": round(e.projection.mean, 2),
                    "replacement_points": round(e.replacement_points, 2),
                    "vorp_per_week": round(e.target.ros_vorp_per_week, 2),
                    "weeks_remaining": e.target.weeks_remaining,
                    "reason": e.reason,
                }
                for e in entries
            ],
            "sources": _sources(bundle.sources),
            "caveats": [
                "Rest-of-season value assumes the current role holds. A role change is "
                "the whole reason a player is worth adding and also the least estimable "
                "number in the model. [theory]",
                *bundle.notes,
            ],
        }

    @mcp.tool(
        name="ff_recommend_bid",
        title="Recommend a FAAB bid",
        annotations=_read_only(),
        description=(
            "Compute one number to bid on one player, with the full shading breakdown.\n\n"
            "FAAB is a first-price sealed-bid auction with common values and a hard budget, "
            "so three corrections stack: equilibrium shading of (n-1)/n for the managers "
            "who genuinely want this player, extra shading for the winner's curse, and the "
            "option value of a dollar held back. [theory] Roughly 85-90% of large FAAB "
            "spends graded as failures in the one real dataset available, which is the "
            "winner's curse measured rather than theorized. [empirical]\n\n"
            "The bidder count comes from this league's own transaction history, not from "
            "league size. It is the weakest input in the chain and the returned reasoning "
            "says how it was derived and which way it errs.\n\n"
            "Remaining budget is read live from Yahoo every call, never from a local "
            "tally, because bids placed from the Yahoo app would never reach one.\n\n"
            "Returns a recommendation only. It cannot submit anything."
        ),
    )
    def ff_recommend_bid(
        player: Annotated[str, Field(description="Player name, or a Yahoo player key.")],
        week: Annotated[int | None, Field(description="Defaults to the current week.")] = None,
    ) -> dict[str, Any]:
        wk = _week(week)
        bundle = load_week(d.yahoo, d.sources, wk, with_opponent=False)
        entry = _find_target(d, bundle, player, wk)
        if entry is None:
            return {
                "error": f"No free agent matching {player!r} that beats your current "
                f"replacement at his position. Try ff_waiver_board to see who does."
            }

        model = waiver_service.refresh_opponent_model(
            d.yahoo, d.store, d.my_team_key, bundle.settings.num_teams
        )
        bidders, how = waiver_service.estimate_bidders(
            model, entry.target.player.position, wk, bundle.settings.num_teams
        )
        entry = _with_bidders(entry, bidders)
        budget = d.yahoo.faab_balance()
        bid = waiver_service.bid_for(
            entry, week=wk, remaining_budget=budget, settings=bundle.settings
        )
        payload = {
            "week": wk,
            "player": entry.target.player.name,
            "player_id": str(entry.target.player.id),
            "recommended_bid": bid.recommended_bid,
            "reservation_value": bid.reservation_value,
            "remaining_budget": budget,
            "shading": {
                "for_competition": round(bid.shading_for_competition, 3),
                "for_winners_curse": round(bid.shading_for_winners_curse, 3),
                "option_value": round(bid.option_value_penalty, 3),
            },
            "interested_bidders": bidders,
            "how_bidders_were_estimated": how,
            "vorp_per_week": round(entry.target.ros_vorp_per_week, 2),
            "reason": bid.reason,
            "caveats": [
                "Bid this once. A losing bid costs nothing -- you pay only if you win -- "
                "so probing low forfeits the player for free and teaches you nothing. "
                "[theory]",
            ],
        }
        payload["recommendation_id"] = d.store.save_recommendation(wk, "bid", payload, bid.reason)
        return payload

    # ---- browse ------------------------------------------------------------------

    @mcp.tool(
        name="ff_my_roster",
        title="List the roster",
        annotations=_read_only(),
        description=(
            "List the owner's current roster with each player's slot, position and NFL "
            "team. A plain listing for a human who wants to look.\n\n"
            "It makes no judgment and carries no projections. For who to start, call "
            "ff_recommend_lineup -- do not rank these players yourself."
        ),
    )
    def ff_my_roster(
        week: Annotated[int | None, Field(description="Defaults to the current week.")] = None,
    ) -> dict[str, Any]:
        wk = _week(week)
        roster = d.yahoo.roster(wk)
        return {
            "week": wk,
            "players": [
                {
                    "name": spot.player.name,
                    "player_id": str(spot.player.id),
                    "slot": spot.slot.value,
                    "position": spot.player.position.value,
                    "nfl_team": spot.player.team,
                    "injury_status": spot.player.injury_status,
                    "undroppable": spot.player.is_undroppable,
                }
                for spot in roster.spots
            ],
        }

    @mcp.tool(
        name="ff_league_transactions",
        title="List league transactions",
        annotations=_read_only(),
        description=(
            "List completed FAAB adds in this league: who bought whom, for how much, in "
            "which week. A plain log for a human who wants to look.\n\n"
            "Yahoo publishes WINNING bids only. The losing bids are not in the API, so "
            "this shows what cleared, never what was offered. [empirical] For what to bid, "
            "call ff_recommend_bid, which models the difference."
        ),
    )
    def ff_league_transactions(
        limit: Annotated[int, Field(description="How many to return.", ge=1, le=100)] = 25,
    ) -> dict[str, Any]:
        bids = d.yahoo.transactions(limit=limit)
        return {
            "transactions": [
                {
                    "team_key": str(b.team_key),
                    "player_id": b.player_id,
                    "position": b.position.value if b.position else None,
                    "winning_bid": b.amount,
                    "week": b.week,
                }
                for b in bids
            ],
            "note": "Winning bids only. Yahoo does not publish losing bids.",
        }

    # ---- preferences -------------------------------------------------------------

    @mcp.tool(
        name="ff_record_preference",
        title="Record a preference",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        ),
        description=(
            "Record something about how the owner wants decisions made: a do-not-drop "
            "player, a risk posture, a roster rule.\n\n"
            "Writes to this app's own database. It changes nothing in Yahoo and moves no "
            "money. Yahoo has no concept of any of this, which is why it is stored here.\n\n"
            "Use it when the owner states a standing preference, not for a one-off "
            "instruction about this week."
        ),
    )
    def ff_record_preference(
        text: Annotated[str, Field(description="The preference, in the owner's own words.")],
        kind: Annotated[
            str,
            Field(description="One of: do_not_drop, risk, roster_rule, note."),
        ] = "note",
    ) -> dict[str, Any]:
        pref = d.store.add_preference(text, kind=kind)
        return {"id": pref.id, "kind": pref.kind, "text": pref.text, "recorded": True}

    @mcp.tool(
        name="ff_list_preferences",
        title="List preferences",
        annotations=_read_only(),
        description=(
            "List the standing preferences the owner has recorded: do-not-drop "
            "players, risk posture, roster rules.\n\n"
            "Read these before making a recommendation the owner will act on. They are "
            "constraints on the advice, not trivia -- a do-not-drop player is not a "
            "drop candidate no matter what the math says."
        ),
    )
    def ff_list_preferences() -> dict[str, Any]:
        return {
            "preferences": [
                {"id": p.id, "kind": p.kind, "text": p.text}
                for p in d.store.list_preferences(active_only=True)
            ]
        }

    # ---- was it right ------------------------------------------------------------

    @mcp.tool(
        name="ff_how_am_i_doing",
        title="Score the advice against what happened",
        annotations=_read_only(),
        description=(
            "Score this app's own advice against what actually happened: how accurate each "
            "projection source has been, and whether the recommended lineups beat simply "
            "starting the highest projections.\n\n"
            "Call this whenever the owner asks whether any of this is working, whether to "
            "trust a recommendation, or which source is better. Report the verdict strings "
            "as written -- they already say when a gap is too small to mean anything, and "
            "a difference in MAE that the verdict calls unseparated is noise, not a "
            "ranking. [empirical] Weekly projection error is around 5 points against means "
            "in the low teens, so most source gaps need most of a season to show up.\n\n"
            "An empty report is the normal state early in a season, not a failure."
        ),
    )
    def ff_how_am_i_doing(
        week: Annotated[
            int | None,
            Field(description="Score one week. Omit for the pooled season, which is stronger."),
        ] = None,
    ) -> dict[str, Any]:
        if week is not None:
            report = calibration.week_report(d.store, week)
            if report is None:
                return {
                    "week": week,
                    "scored": False,
                    "note": (
                        f"No inputs were recorded for week {week}, so there is nothing to "
                        f"score. Weeks are only scorable if the app was running and took a "
                        f"snapshot before the games."
                    ),
                }
            return {
                "week": week,
                "scored": bool(report.sources or report.lineup),
                "note": report.note,
                "sources": [_source_score(s) for s in report.sources],
                "ensemble": _source_score(report.ensemble) if report.ensemble else None,
                "ensemble_versus_each_source": [c.verdict for c in report.versus_ensemble],
                "lineup": _lineup_score(report.lineup),
                "start_sit_calls": _decision_record(report.decisions),
            }

        season = calibration.season_report(d.store)
        lineups = season.lineups
        return {
            "weeks_scored": season.weeks_scored,
            "scored": bool(season.weeks_scored),
            "note": season.note,
            "sources": [_source_score(s) for s in season.sources],
            "ensemble": _source_score(season.ensemble) if season.ensemble else None,
            "ensemble_versus_each_source": [c.verdict for c in season.versus_ensemble],
            "lineups": [_lineup_score(ls) for ls in lineups],
            "start_sit_calls": _decision_record(season.decisions),
            "weeks_ahead_of_the_obvious_lineup": season.weeks_ahead_of_baseline,
            "weeks_with_a_lineup_graded": len(lineups),
            "caveats": [
                "The lineup benchmark is the highest-projection lineup, which is what you "
                "would have started without this app -- not the hindsight-perfect lineup, "
                "which nobody can reach.",
                "Start/sit optimization is worth roughly 1-3 win-probability points a week. "
                "[empirical] Over a handful of weeks that is invisible under the noise, so "
                "a losing record here early is not evidence the math is wrong.",
                "Slots the app called too close are excluded from the start/sit record "
                "rather than graded. They are a refusal to predict, not a prediction.",
            ],
        }

    # ---- the two-step write ------------------------------------------------------

    @mcp.tool(
        name="ff_propose_claim",
        title="Propose a waiver claim",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        ),
        description=(
            "Work out a waiver claim and return the plan plus an approval id. "
            "**This submits nothing and spends nothing.**\n\n"
            "Show the owner the returned plan -- the player, the bid, the budget it "
            "leaves, and anything it would drop -- and wait for them to say yes. Only then "
            "call ff_confirm with the approval id. Never call ff_confirm in the same turn "
            "as this tool: the two steps exist so a human sees the number before any money "
            "moves, and chaining them removes the only safeguard.\n\n"
            "The approval is single use and expires. If it has gone stale, propose again "
            "rather than reusing an old id."
        ),
    )
    def ff_propose_claim(
        player: Annotated[str, Field(description="Player name, or a Yahoo player key.")],
        week: Annotated[int | None, Field(description="Defaults to the current week.")] = None,
    ) -> dict[str, Any]:
        wk = _week(week)
        bundle = load_week(d.yahoo, d.sources, wk, with_opponent=False)
        entry = _find_target(d, bundle, player, wk)
        if entry is None:
            return {"error": f"No claimable free agent matching {player!r}."}

        model = waiver_service.refresh_opponent_model(
            d.yahoo, d.store, d.my_team_key, bundle.settings.num_teams
        )
        bidders, how = waiver_service.estimate_bidders(
            model, entry.target.player.position, wk, bundle.settings.num_teams
        )
        budget = d.yahoo.faab_balance()
        bid = waiver_service.bid_for(
            _with_bidders(entry, bidders),
            week=wk,
            remaining_budget=budget,
            settings=bundle.settings,
        )
        summary = (
            f"Bid ${bid.recommended_bid} on {entry.target.player.name} "
            f"({entry.target.player.position.value}). Leaves "
            f"${budget - bid.recommended_bid} of ${budget}."
        )
        approval = approvals.issue(bid, wk, summary=summary)
        return {
            "approval_id": approval.approval_id,
            "summary": summary,
            "player": entry.target.player.name,
            "player_id": str(entry.target.player.id),
            "bid": bid.recommended_bid,
            "remaining_budget_now": budget,
            "remaining_budget_after": budget - bid.recommended_bid,
            "how_bidders_were_estimated": how,
            "reason": bid.reason,
            "submitted": False,
            "next_step": (
                "Show this to the owner. If they approve, call ff_confirm with the "
                "approval_id. Nothing has been sent to Yahoo."
            ),
            "expires_in_seconds": d.config.approval_ttl_seconds,
        }

    @mcp.tool(
        name="ff_confirm",
        title="Submit an approved claim",
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True
        ),
        description=(
            "Submit a claim the owner has already approved. **This spends money.**\n\n"
            "Only call it after the owner has seen the exact plan from ff_propose_claim "
            "and said yes to it. The approval id is single use, is bound to that one "
            "payload and week, and expires -- an approval for a different plan is not a "
            "weaker approval, it is no approval.\n\n"
            "The remaining budget is re-read from Yahoo immediately before submission and "
            "a bid over it is rejected outright, never quietly reduced. If writes are "
            "disabled or Yahoo has not granted write access, this returns the exact manual "
            "move instead of pretending to have made it."
        ),
    )
    def ff_confirm(
        approval_id: Annotated[
            str, Field(description="The approval_id returned by ff_propose_claim.")
        ],
    ) -> dict[str, Any]:
        # Read the human sentence before consuming the approval, because after a failure
        # the owner still needs to be told which move they were trying to make.
        summary = d.store.approval_summary(approval_id)
        try:
            # Re-read the budget from Yahoo now, not from anything cached at propose time.
            budget = d.yahoo.faab_balance()
            result = submit_claim(
                approvals, executor_for(d.config), approval_id, remaining_budget=budget
            )
        except FFError as exc:
            return {"submitted": False, "move": summary, "error": str(exc)}
        return {
            "submitted": result.ok,
            # The move in the owner's words, not the executor's. Executors speak in player
            # keys, and on the assisted path -- which is the path Yahoo leaves us, see
            # CLAUDE.md section 6 -- this text is the whole product: it is what the owner
            # reads and then types into the Yahoo app. "470.p.31883" is not instructions.
            "move": summary,
            "executor": result.executor,
            "detail": result.detail,
            "manual_url": result.manual_url,
            "transaction_key": str(result.transaction_key) if result.transaction_key else None,
            "note": (
                "Dry run: nothing was sent to Yahoo."
                if result.executor == "dryrun"
                else (
                    "Yahoo's Fantasy Sports API is read-only -- write access is not "
                    "offered to anyone. Make this move yourself at the link above; it "
                    "takes about fifteen seconds. Everything up to the click is done."
                    if result.executor == "assisted"
                    else "Sent to Yahoo."
                )
            ),
        }

    return mcp


# ---- shaping helpers -------------------------------------------------------------


def _sources(statuses: Any) -> list[dict[str, Any]]:
    return [
        {
            "name": s.name,
            "ok": s.ok,
            "required": s.required,
            "age_seconds": None if s.age_seconds is None else round(s.age_seconds),
            "detail": s.detail,
        }
        for s in statuses
    ]


def _with_bidders(entry: Any, bidders: int) -> Any:
    from dataclasses import replace

    return replace(entry, target=replace(entry.target, interested_bidders=bidders))


def _find_target(deps: Deps, bundle: Any, player: str, week: int) -> Any:
    """Locate one free agent on the board by name or key.

    Matching on the board rather than on the raw pool is deliberate: a player who does not
    beat the current replacement has no bid worth making, and saying so is a better answer
    than pricing him.
    """
    needle = player.strip().lower()
    board = waiver_service.build_board(deps.yahoo, deps.sources, bundle, limit=100)
    for entry in board:
        target = entry.target.player
        if needle in target.name.lower() or needle == str(target.id).lower():
            return entry
    return None


def http_app(deps: Deps | None = None, *, authenticate: bool | None = None) -> Any:
    """The ASGI app uvicorn serves. Streamable HTTP at /mcp, which is what Claude expects.

    Auth is on whenever the GitHub credentials are configured. Turning it off is for
    running against fixtures on a laptop; a deployment without them is a public server for
    a private league, so ``make deploy`` checks for them and ``/health`` reports it.
    """
    d = deps if deps is not None else default_deps()
    wants_auth = (
        authenticate
        if authenticate is not None
        else bool(d.config.github_client_id and d.config.allowed_github_login)
    )
    mcp = build_server(d, auth=github_auth(d.config) if wants_auth else None)
    if wants_auth:
        mcp.add_middleware(OnlyTheOwner(d.config.allowed_github_login))
    else:
        log.warning("mcp_unauthenticated", detail="No GitHub credentials; auth is off.")

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request: Any) -> Any:
        """Per-source staleness and whether the door is locked (CLAUDE.md 2.5)."""
        return JSONResponse(
            {
                "ok": True,
                "authenticated": wants_auth,
                "write_executor": d.config.write_executor,
                "write_enabled": d.config.write_enabled,
                "sources": [s.name for s in d.sources],
                "unfinished_writes": len(d.store.unfinished_writes()),
            }
        )

    app = mcp.http_app(transport="http")
    _attach_scheduler(app, d)
    return app


def _attach_scheduler(app: Any, deps: Deps) -> None:
    """Run the weekly jobs inside this process, alongside the MCP endpoint.

    Wrapped around FastMCP's own lifespan rather than bolted on with on_startup: the
    Streamable HTTP transport needs its session manager started, and replacing that
    lifespan would leave every tool call failing on a server that looked healthy.

    Not a separate machine, because a Fly volume attaches to exactly one machine and the
    database lives on it. See ff/api/scheduler.py.
    """
    inner = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(scope: Any) -> AsyncIterator[None]:
        tasks = scheduler.start(deps)
        try:
            async with inner(scope):
                yield
        finally:
            for task in tasks:
                task.cancel()

    app.router.lifespan_context = lifespan


def _source_score(score: Any) -> dict[str, Any]:
    """One forecaster's record, rounded to the precision the numbers actually support."""
    return {
        "source": score.source,
        "forecasts_scored": score.n,
        "mean_absolute_error": round(score.mae, 2),
        "bias": round(score.bias, 2),
        "bias_reading": (
            "runs hot" if score.bias > 0.5 else "runs cold" if score.bias < -0.5 else "unbiased"
        ),
    }


def _lineup_score(score: Any) -> dict[str, Any] | None:
    if score is None:
        return None
    return {
        "week": score.week,
        "points_scored": round(score.recommended, 2),
        "points_if_you_started_the_highest_projections": round(score.baseline, 2),
        "edge": round(score.edge, 2),
        "points_left_on_the_bench": round(score.left_on_bench, 2),
        "starters_with_no_recorded_score": score.missing_players,
    }


def _decision_record(record: Any) -> dict[str, Any] | None:
    """The start/sit record. ``points_gained`` leads, because win-loss hides the size."""
    if record is None:
        return None
    return {
        "verdict": record.verdict,
        "right": record.right,
        "wrong": record.wrong,
        "tied": record.tied,
        "too_close_to_call_not_graded": record.declined,
        "points_gained": round(record.points_gained, 2),
    }
