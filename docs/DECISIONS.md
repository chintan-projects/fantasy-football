# Decision log

One entry per decision that a future reader would otherwise re-litigate. Newest first.
Format: date · decision · why · what would change our mind.

---

## 2026-09-13 · Calibration reports an error bar, and refuses to rank sources without one

`ff_how_am_i_doing` compares projection sources with a paired test and prints "too close to
call" until a gap clears two standard errors over at least 30 forecasts. The obvious
alternative — a table of MAE per source, sorted — was rejected. Weekly projection error is
around 5 points, sources typically sit within a fraction of a point of each other, and a
sorted table invites reading a ranking into noise. The whole purpose of the feature is to
tell the owner what to trust; a report that manufactures confidence defeats it.

Lineup quality is scored against the highest-projection lineup rather than the
hindsight-perfect one. Perfect is unreachable, so scoring against it would report a loss
every week no matter how good the decision was. The highest-projection lineup is the
decision the owner would have made unaided, which is the thing this app has to beat.

**Changes our mind:** a season of data where the sources separate cleanly and the threshold
is just delaying a true finding — or the opposite, a gap that clears two sigma and then
reverses, which would mean the pairing is not removing as much shared noise as assumed.

---

## 2026-09-13 · The front door is an MCP server, not a web app

The PRD's first-listed failure mode is "I stop opening it." A web app on a Sunday morning
competes with the Yahoo app for the same two minutes and loses, because the Yahoo app is
already open. A Claude connector does not have to be visited: the question gets asked in
the place the owner is already typing.

Nothing under this changes. `domain/`, `adapters/`, `services/` and `core/` are untouched;
the decision math is the same math. Only the entry point moved, and the old FastAPI app and
Next.js frontend are demoted rather than deleted (see `frontend/README.md`).

**Changes our mind:** a month of use where the tools go uncalled, or a decision that
genuinely needs a screen — a calibration chart is the obvious candidate, because a table of
projected-versus-actual is worse in prose than in pixels. Either brings the frontend back as
a second surface, not as a replacement.

## 2026-09-13 · MCP tools return decisions, never raw data for the model to reason over

The tempting shape is `ff_get_roster` plus `ff_get_projections`, letting Claude reason to an
answer. It is the wrong shape, and the reason is not taste.

A recommendation the model reasons to is not reproducible, its inputs are never snapshotted,
and nothing about it can be scored later. CLAUDE.md 2.5 calls calibration the single most
important observability requirement in the project: the app must be able to answer "was it
right?". A verdict that exists only in one conversation cannot be graded, so a tool set that
produces those verdicts quietly deletes the ability to know whether any of this works.

So every tool that makes a judgment calls `services/`, persists its input snapshot, and
returns the computed answer with its reasoning. Raw-data tools exist only where a human
genuinely wants to browse: `ff_my_roster` and `ff_league_transactions`, both of which say in
their own descriptions to use the judging tool instead.

**Changes our mind:** nothing short of abandoning calibration, which would be abandoning the
project's stated purpose.

## 2026-09-13 · Spending money takes two tool calls

`ff_propose_claim` prices a bid and issues an approval id. `ff_confirm` spends it. No tool
does both.

A single `ff_submit_claim(player, bid)` would be a tool the model can call on its own
initiative, and CLAUDE.md 5.1 requires a recorded human yes against that exact payload. The
split makes that structural rather than aspirational: the approval is single use, bound to
one payload and one week, expires after six hours, and the remaining budget is re-read from
Yahoo immediately before submission — a bid over it is rejected, never quietly reduced.

The approved payload is stored alongside the approval rather than recomputed at confirm
time. Recomputing would mean the owner confirms a number they never saw.

**Changes our mind:** nothing. This is the invariant the product is built around.

## 2026-09-13 · GitHub OAuth for a server with one user

A shared secret in a header is the obvious answer for an audience of one, and Claude does
not really support it. Its connectors accept OAuth with dynamic client registration, OAuth
with client id metadata documents, or no auth. `static_headers` is beta, is entered by an
*organization administrator* rather than a user, and needs Anthropic approval for any
non-standard header name. Machine-to-machine `client_credentials` is explicitly unsupported:
every connection requires a human to consent. `[empirical]` — Anthropic's connector docs,
read 2026-09-13.

So: FastMCP's `GitHubProvider`, which presents the DCR interface Claude wants over one
pre-registered GitHub OAuth app. GitHub authenticates several hundred million people, so a
second gate refuses any login but the configured one at the first tool call.

**Changes our mind:** `static_headers` leaving beta for individual users. It is a simpler
story and worth switching to.

## 2026-09-13 · Fly, with the scheduler inside the server process

MCP is request-driven. Nothing fires without a tool call, so the Tuesday waiver snapshot and
the Sunday lineup snapshot still need a clock — moving to MCP did not remove that need.

The natural design is a separate scheduled machine sharing the database. Fly does not allow
it: a volume attaches to exactly one machine, and a machine mounts one volume. `[empirical]`
So the jobs run as asyncio tasks in the serving process, off the event loop, and
`auto_stop_machines` is off with `min_machines_running = 1` — a suspended machine runs no
jobs. The cost is a few dollars a month for a machine that is idle most of the week.

**Changes our mind:** outgrowing SQLite. A managed Postgres would let the jobs be their own
machine and let the server sleep. Nothing about the current data volume argues for it.

## 2026-09-12 · Yahoo JSON is parsed key-first, not by position

Yahoo interleaves sub-resources as positional siblings, so `/players/percent_owned` puts the
player at index i and ownership at i+1. Index-based parsing matches the documentation and
fails silently the moment Yahoo adds or moves a field — you get a wrong value, not an error.
A breadth-first key walk either finds what it needs or raises `SchemaDrift` naming the field.

The cost is that two keys with the same name at different depths could collide. In practice
the entity's own fields are merged first and win, and the parsers require the fields that
matter rather than defaulting them.

**Changes our mind:** a recorded response where a key walk picks up the wrong value. There is
none yet, because there is no recorded response — see the next entry.

## 2026-09-12 · Yahoo and FantasyPros fixtures are hand-built, and say so

Every other adapter's fixture is recorded from the live source. Yahoo has no anonymous tier
(a bare GET is a 401) and FantasyPros needs a paid key; neither exists on this machine, so
those two are built from documented shapes.

That is weaker evidence and it is labelled as such in both fixture READMEs and in
PROGRESS.yaml. The mitigation is in the parsers, not the fixtures: every required field is
named, and a miss raises `SchemaDrift` identifying it, so the first live call reports exactly
what is wrong instead of quietly returning an empty result.

**Changes our mind:** a token and an API key. Re-record then, and delete the caveats.

## 2026-09-12 · A player covered by one source gets no projection

Config-level and fetch-level single-source states already raise. The remaining case is one
player whom only one source covers. Blending him anyway would produce a `Projection` whose
epistemic spread is a placeholder rather than measured disagreement — the same type carrying
a materially worse quantity, with nothing downstream able to tell the difference.

So he is returned in `unprojected` and shown without a number. Coverage is reported.

**Changes our mind:** a calibration study showing a single-source projection beats no
projection for the decisions this app actually makes. That is testable in M6.

## 2026-09-12 · FantasyPros rank std-dev is not converted to points

`rank_std` is the spread of 130+ expert *rankings*. Turning it into a points standard
deviation needs a rank-to-points curve, which is decision math, not adapter work. The adapter
returns the raw consensus; `domain.blend` still derives epistemic spread from cross-source
disagreement.

Doing the conversion badly would be worse than not doing it: it would look like a measured
uncertainty and be a guess.

**Changes our mind:** the distribution work in M3, which is where the curve belongs.

---

## 2026-09-13 · Greedy assignment, not ILP, for lineup assembly

Standard roster eligibility is nested (FLEX ⊇ RB∪WR∪TE), which makes feasible lineups the
independent sets of a transversal matroid. Greedy by weight is provably optimal there.
Adding an ILP solver would be a dependency, a build step, and a source of bugs, in exchange
for nothing.

**Changes our mind:** a league rule with a position *limit* ("at most 3 WR"), any non-nested
eligibility constraint, or multi-week bye planning. Any of those breaks the matroid and we
switch to min-cost max-flow.

## 2026-09-13 · SQLite, not Postgres

A season is a few MB. One file, no server, trivial backup, and backtests are a file copy.

**Changes our mind:** the host makes a persistent volume painful, or two processes need to
write concurrently.

## 2026-09-13 · Ensemble projections; a single source is a misconfiguration

A simple average beat individual sources in 63% of head-to-head comparisons. Single-source
config raises a startup error rather than silently degrading.

**Changes our mind:** measured calibration showing one source dominating the blend over a
full season.

## 2026-09-13 · WriteExecutor takes an Approval as a required argument

Safety invariant §5.1 says nothing is written without explicit approval. Enforcing it in the
type signature rather than with a runtime check means there is no code path — including a
future one written in a hurry — that can skip it.

**Changes our mind:** nothing. This one is structural.

## 2026-09-13 · Build the read half first, regardless of Yahoo write access

Yahoo now grants write access only by review. The read half is ~80% of the value and depends
on no approval.

**Changes our mind:** write access is granted early, in which case the order is a convenience
rather than a hedge.

## 2026-09-13 — Sleeper replaces FantasyPros as the second projection source

**Decision.** The default ensemble is ESPN + Sleeper. Both are free. FantasyPros stays
implemented and tested but is no longer configured by default.

**Why.** FantasyPros was chosen as the required source before anyone checked whether a
free second source existed. One does. Sleeper serves Rotowire's weekly projections on
`api.sleeper.com` with no key and no cost, and probing it on 2026-09-13 returned 470
scored players for the current week including all 32 team defenses.

The ensemble argument survives the swap intact, because the two sources are genuinely
independent: ESPN publishes its own projections and Sleeper republishes Rotowire's.
Averaging two views of the same underlying forecast would buy nothing; averaging these
two buys the thing the 63% figure describes.

Measured live, week 2 of 2026: 427 players matched across both sources, 32 of 32
defenses, median absolute disagreement 0.97 points.

**What is lost.** FantasyPros' `rank_std` — the spread of expert opinion in rank units,
and the only direct read on epistemic uncertainty any source offers. Without it, epistemic
spread is estimated from the gap between exactly two numbers, which is a thin estimate
resting on a single comparison. This is the weakest part of the current projection path
and the clearest argument for a third source. `[theory]`

**What is not lost.** Nothing else. FantasyPros' projections were never better than the
pair, only more expensive.

**Reversible.** Set `FF_PROJECTION_SOURCES=["espn","sleeper","fantasypros"]` and supply a
key. The adapter, its fixtures and its tests are untouched.

## 2026-09-13 — An ESPN zero with no stat line is not a projection

**Decision.** `weekly_projection` returns `None` for `appliedTotal == 0.0` when the row
carries no `stats` dict. A zero that does carry a stat line is kept.

**Why.** Found by comparing ESPN against Sleeper live rather than by reading either one's
output alone — 22.4% of ESPN's keys came back as exactly 0.00, and the largest apparent
disagreements were players ESPN had no number for. Brock Bowers, listed OUT, at 0.00
against Sleeper's 15.14.

Blending those produces 7.57: a number that is wrong in both directions, carries no
warning, and would lose a start/sit call outright. It is strictly worse than having no
projection, because the player then falls into `unprojected` and is shown honestly as
uncovered.

After the fix: ESPN's usable keys fell from 577 to 448, every phantom zero disappeared,
and the maximum cross-source disagreement dropped from 15.14 points to 5.89. `[empirical]`

**The distinction.** A backup who genuinely projects to zero has a stat line full of
zeroes. A player with no projection has no stat line at all. That is the test, and it is
ESPN's own encoding rather than a heuristic.
