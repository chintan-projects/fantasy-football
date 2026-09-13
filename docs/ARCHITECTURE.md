# Architecture

Read `CLAUDE.md` §2 first. This document says how the principles land in code.

## Shape

```
                    ┌──────────────┐
   browser  ───────▶│  Next.js UI  │  /lineup  /waivers  /history
                    └──────┬───────┘
                           │ typed client generated from OpenAPI
                    ┌──────▼───────┐
                    │  api/        │  FastAPI. Thin. No logic.
                    └──────┬───────┘
                    ┌──────▼───────┐
                    │  services/   │  orchestration: fetch → compute → decide
                    └──┬────────┬──┘
            ┌──────────▼──┐  ┌──▼───────────┐
            │  domain/    │  │  adapters/   │  yahoo espn sleeper
            │  pure math  │  │  I/O only    │  nflverse odds weather
            └─────────────┘  └──────────────┘
                    ┌──────────────┐
                    │  core/       │  config logging errors retry cache clock
                    └──────────────┘
```

Dependencies point one way. `domain/` imports nothing but `core/` and the standard library.
CI greps for network imports in `domain/` and a PostToolUse hook blocks them at edit time.

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

## Hosting

Cloud-hosted so it works from a phone and so scheduled jobs fire without a laptop being awake.

- Backend: one container, Fly.io or Railway. Both do cron.
- Frontend: Vercel, or served by the same container to keep it to one deploy.
- Scheduled work: Sunday 09:00 PT lineup check, Tuesday 18:00 PT waiver analysis, and a
  Sunday 11:15 PT last-call re-check for late inactives.
- Auth: single-user. A long random token in a cookie is enough. Do not build accounts.

Note the timing constraint: Yahoo load spikes Sunday 11:00–13:00 ET as lineups lock. Submit
well before kickoff with retry budget left.

## Testing

- `domain/` — pure unit tests, no mocks, plus property tests on the optimizer (the greedy
  result must equal a brute-force search over all valid lineups on random rosters).
- `adapters/` — recorded fixtures. **Every adapter has a fixture of a malformed response**,
  because these sources change without notice.
- `services/` — in-memory fakes for adapters.
- Backtest harness — replay stored snapshots, score strategies against actual outcomes.
  A strategy that has not been backtested is a hypothesis, not a feature.
