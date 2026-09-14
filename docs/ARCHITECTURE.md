# Architecture

Read `CLAUDE.md` §2 first. This document says how the principles land in code.

## Shape

```
                    ┌──────────────┐
   Claude   ───────▶│  api/mcp.py  │  ten tools, over Streamable HTTP
   (phone,          └──────┬───────┘  auth.py gates it to one GitHub login
    desktop)               │          scheduler.py runs the three weekly jobs
                    ┌──────▼───────┐
                    │  services/   │  orchestration: fetch → compute → decide
                    └──┬────────┬──┘
            ┌──────────▼──┐  ┌──▼───────────┐
            │  domain/    │  │  adapters/   │  yahoo espn sleeper
            │  pure math  │  │  I/O only    │  nflverse store (SQLite)
            └─────────────┘  └──────────────┘
                    ┌──────────────┐
                    │  core/       │  config logging errors retry cache clock
                    └──────────────┘

   ( frontend/ — Next.js, demoted 2026-09-13. Kept, not built or deployed. )
   ( api/app.py — the FastAPI app it talked to. Same status.               )
```

Dependencies point one way. `domain/` imports nothing but `core/` and the standard library.
CI greps for network imports in `domain/` and a PostToolUse hook blocks them at edit time.

The MCP layer occupies exactly the position the HTTP layer did, and is bound by the same
rule: unpack arguments, call one service, shape the answer. A judgment inside a tool body
is the same defect as business logic in a route handler.

## Tools return decisions, not data

This is the design rule the whole MCP layer exists to enforce, so it goes near the top.

The wrong shape is `ff_get_roster` plus `ff_get_projections`, with the model reasoning to an
answer. It looks flexible and it quietly destroys the project's purpose: a verdict the model
reasoned to has no persisted input snapshot, is not reproducible, and can never be scored
against what actually happened. `CLAUDE.md` §2.5 calls that scoring the single most
important observability requirement here.

So the judging tools each call `services/`, persist the snapshot they decided on, and
return the computed answer with its reasoning attached. `ff_how_am_i_doing` is bound by the
same rule for the same reason: it returns the computed verdict — including "too close to
call" — rather than handing the model a table of errors to average, because a significance
threshold applied by a language model is not a threshold. Two tools return raw data —
`ff_my_roster` and `ff_league_transactions` — because a human sometimes just wants to look,
and both say in their own descriptions to use the judging tool for the decision.

Tool descriptions are documentation that a model reads and acts on, so the honesty rule in
`CLAUDE.md` §3 applies to them: every claim is labelled `[empirical]`, `[theory]` or
`[folk]`, and `tests/test_mcp.py` asserts that the caveats are still there.

## Spending money takes two calls

`ff_propose_claim` prices a bid, writes an approval row, and returns the plan plus an
approval id. It submits nothing. `ff_confirm` spends that approval and only that approval.

The approval is single use (enforced by a conditional `UPDATE`, not by a set in memory),
bound to one payload hash and one week, and expires after six hours. The approved payload is
stored beside it, so confirm submits the number the owner saw rather than re-deriving one
that has since moved. The FAAB balance is re-read from Yahoo immediately before submission
and a bid over it is rejected, never clamped.

The approvals table is in SQLite rather than in memory for a plain reason: propose and
confirm are two separate requests and may not be the same process.

## What Yahoo owns and what we own

Yahoo is the system of record for **state**. This app is the system of record for
**judgment**. Getting that line wrong is how a mirror drifts.

| Yahoo owns — never mirrored | We own — Yahoo has no concept of it |
|---|---|
| `faab_balance` on the team resource | Our blended projections, and the input snapshot at decision time |
| `/league/{key}/transactions` | The reasoning behind each recommendation |
| Rosters, including past weeks via `roster;week=N` | Recommended versus what was actually set |
| `uses_faab` and the waiver rules | Preferences that change the math |
| | The derived opponent model |

The FAAB balance is the sharp case. A local spend log would never see a bid placed from the
Yahoo app, so it would be quietly wrong — and a balance that is quietly wrong is worse than
one that is missing. `adapters/store.py` therefore has no `faab_balance` column, no roster
table and no transactions table, and a test asserts that it still does not.

## Why domain/ is pure

The decision math is the part most likely to be wrong and most expensive to debug. Keeping it
free of I/O means every claim in `fantasy-decision-math` is testable with zero mocks, and a
backtest is a loop over stored snapshots rather than a replay harness.

Practical rule: if a function needs the current time, the week number, or an env var, **it is
passed in as an argument.**

## The three real seams

Per `CLAUDE.md` §2.3 these are the only interfaces that exist up front, because each has a
known second implementation.

### `ProjectionSource`
```python
class ProjectionSource(Protocol):
    name: str
    required: bool
    def weekly(self, week: int, players: list[PlayerId]) -> dict[PlayerId, Projection]: ...
```
ESPN today; FantasyPros next; an own model later. **The ensemble needs ≥2 by design** — a
simple average beat individual sources in 63% of head-to-head comparisons, so a single-source
config is a misconfiguration, not a valid setup.

### `WriteExecutor`
```python
class WriteExecutor(Protocol):
    def set_lineup(self, plan: LineupPlan, approval: Approval) -> WriteResult: ...
    def submit_claim(self, claim: WaiverClaim, approval: Approval) -> WriteResult: ...
    def cancel_claim(self, key: TransactionKey, approval: Approval) -> WriteResult: ...
```
- `YahooApiExecutor` — the real API.
- `AssistedExecutor` — renders the move list and a deep link; the human clicks.
- `DryRunExecutor` — default. Journals intent, sends nothing.

This exists because Yahoo write access may not be granted (`CLAUDE.md` §6). Every executor
takes an `Approval` as a **required argument** — there is no code path that writes without
one. That is the enforcement mechanism for safety invariant §5.1: it is a type error, not a
policy.

### `BidStrategy`
```python
class BidStrategy(Protocol):
    def bid(self, ctx: BidContext) -> BidRecommendation: ...
```
The FAAB math will be rewritten as it is tested against real outcomes. Keeping it swappable
lets a backtest run several strategies over the same season.

Everything else stays concrete until a second caller exists.

## Data flow, one week

```
Sleeper /state/nfl        → current week (never computed from a date)
Yahoo  /league/settings   → roster slots + scoring (cached all season)
Yahoo  /team/roster;week  → who I have
ESPN kona + FantasyPros   → projections  ─┐
FantasyPros std-dev       → epistemic σ   ├─▶ domain.blend → Projection
player game logs          → aleatoric σ  ─┘
ESPN scoreboard           → spread, total → implied team total
nflverse injuries         → status, practice participation
                                           │
                          domain.simulate  ▼   correlated Monte Carlo
                          domain.lineup    →   candidate lineups, ranked by P(win)
                                           │
                          services.explain →   Recommendation + input snapshot
                                           ▼
                          UI → human approves → WriteExecutor → journal
```

## Storage

SQLite to start. One file, no server, trivially backed up, and the whole dataset is a few MB
a season. Postgres only if hosting forces it.

Tables that matter:

- `snapshots` — every input set, keyed by week. Makes backtests replays, not re-fetches.
- `recommendations` — what was recommended, the win-prob delta, and **why**.
- `approvals` — user id, payload hash, created_at. Expire at 6 hours (`CLAUDE.md` §5.4).
- `write_journal` — intent → approval → request → response. Crash recovery reads this.
- `outcomes` — actual points, joined to projections for calibration.

`snapshots` + `outcomes` are what let the app answer "were you right?" — the single most
important observability requirement in the project (`CLAUDE.md` §2.5).

## Scoring the app against reality

`ff_how_am_i_doing` reports two different things, and the difference is what keeps the
report honest.

**Source accuracy** has hundreds of player-weeks behind it, so it gets an error bar. The
comparison is paired — the same players, the same weeks, one source against the other —
because an unpaired comparison's spread is dominated by how hard the week was rather than
by how the sources differ. Below 30 pairs, or within two standard errors, the report says
so and names no winner. Weekly MAE runs near 5 points against means in the low teens
`[empirical]`, so most gaps between sources need most of a season to surface.

**Decision quality** is the question an owner actually asks: when it said start Bowers
over McBride, who scored more? It is reported as a record plus the points those calls were
worth, because a 6-4 record worth two points and a 4-6 record worth thirty are different
seasons and win-loss hides both. Slots the model called too close are counted and **not
graded** — "too close to call" is a real output (`CLAUDE.md` §3), and a real output cannot
also be a prediction. Grading coin flips would fill the record with noise in whichever
direction the coins fell, punishing the model for being honest.

**Lineup quality** has one number per week, so seventeen a season, and no arithmetic makes
that a significance test. It is a record, not a p-value. The benchmark is the
highest-projection lineup, not the hindsight-perfect one: perfect is unreachable by anyone,
so scoring against it would report a large loss every week regardless of decision quality
and teach the owner nothing. The highest-projection lineup is what the owner would have
started without this app, which makes it the thing the app has to beat to be worth opening.

Two consequences shaped the code rather than the report:

- `blend()` used to average the sources and keep only their names. Per-source MAE was
  therefore uncomputable, and not recoverable later — a source cannot be graded on a
  forecast it no longer admits to making. `Projection` now carries `per_source` and the
  snapshot persists it (BUG-012).
- The snapshot carries slot eligibility, because hindsight has to rebuild the lineup that
  was *legal at the time*. Slot rules are a league setting and can be edited mid-season.
- Contested slots persist `start_id` and `over_id` alongside the names. Outcomes are keyed
  by player id, so a call recorded only by name is ungradeable the moment the week ends —
  and two players can share a name.
- Both writers of a lineup recommendation — the tool and the Sunday job — go through
  `api/payloads.py`. They used to shape it independently, and BUG-013 is what that cost:
  two formats, one reader, half a season silently ungraded. A test asserts their keys
  agree rather than asserting any particular key, so a field added to one is added to both.

## Hosting

A custom connector is reached over the public internet, from Anthropic's egress range
`160.79.104.0/21`. There is no local option: cloud deploy is mandatory, not a convenience.
Full walkthrough in `docs/DEPLOY.md`.

- **One Fly machine**, one container, one 1 GB volume at `/data` holding the SQLite database
  and the Yahoo token.
- **The scheduler runs inside that process.** A Fly volume attaches to exactly one machine
  and a machine mounts one volume, so the jobs cannot be a second machine reading the same
  file. `[empirical]` And `auto_stop_machines` is off with `min_machines_running = 1`,
  because a suspended machine runs no jobs. MCP is request-driven and does nothing on its
  own — moving to MCP did not remove the need for a clock.
- **Scheduled work:** Tuesday 20:00 Pacific snapshots the waiver board *before* waivers
  process overnight; Sunday 09:00 Pacific snapshots the lineup inputs while the roster can
  still be changed. Neither notifies anyone and neither writes to Yahoo. They capture the
  inputs at the moment the decision was live, which is what makes scoring it later possible
  at all — grading a 9am decision against a 3pm world measures the wrong thing.
- **Auth is OAuth, even for one user.** Claude's connectors accept OAuth with dynamic client
  registration, OAuth with client id metadata, or nothing. A bearer token is the
  `static_headers` type: beta, entered by an organization administrator, and non-standard
  header names need Anthropic approval. Machine-to-machine `client_credentials` is not
  supported at all — every connection requires a human to consent. `[empirical]` FastMCP's
  `GitHubProvider` gives Claude the DCR interface it wants over one pre-registered GitHub
  OAuth app, and `OnlyTheOwner` refuses every login but the configured one.

Two hard limits worth designing against: a tool result is capped near 150,000 characters and
a tool call times out at 240 seconds. `[empirical]` The board caps at 50 targets for the
first; a 20,000-draw simulation takes a few seconds, so the second has a wide margin.

Note the timing constraint that has not changed: Yahoo load spikes Sunday 11:00–13:00 ET as
lineups lock. Submit well before kickoff with retry budget left.

## Testing

- `domain/` — pure unit tests, no mocks, plus property tests on the optimizer (the greedy
  result must equal a brute-force search over all valid lineups on random rosters).
- `adapters/` — recorded fixtures. **Every adapter has a fixture of a malformed response**,
  because these sources change without notice.
- `services/` — in-memory fakes for adapters.
- `api/mcp.py` — the tools are exercised end to end against `tests/fakes.py`, which replays
  the recorded Yahoo fixtures through the real parsers. The tool *surface* is tested too:
  Claude sees only names, descriptions and annotations, so a tool that stops being read-only
  or a description that drops its caveat is a defect nothing else would catch.
  `scripts/mcp_demo.py` runs the same path and prints it.
- Backtest harness — replay stored snapshots, score strategies against actual outcomes.
  A strategy that has not been backtested is a hypothesis, not a feature.
