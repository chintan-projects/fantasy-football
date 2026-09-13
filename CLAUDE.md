# Fantasy Football Copilot

A Yahoo Fantasy Football integrator that recommends the starting lineup and the
waiver/FAAB moves that maximize weekly win probability. Owner approves; the app executes.

Read this file before doing anything. It is the contract.

---

## 1. What this app is

**One sentence:** every Sunday morning it tells me who to start and why, and every
Tuesday night it tells me what to bid on whom out of my $100 FAAB — and I click approve.

**Success test:** I stop opening the Yahoo app to make decisions. I open this instead,
read one screen, click approve, and I am done in under two minutes.

**Autonomy model:** recommend, human approves. Nothing is written to Yahoo without an
explicit approval action. This is not a preference; it is an architectural invariant.
See §5.

**Non-goals.** Not a DFS optimizer. Not a multi-league SaaS. Not a chatbot. Not a
league-mate-facing product. One user, one league at a time, one decision per screen.

---

## 2. Engineering principles

Six principles govern this codebase. They are listed in priority order. When two
conflict, the higher one wins, and the decision gets a line in `docs/DECISIONS.md`.

### 2.0 Simplicity first (the tiebreaker)

The other six principles all push toward more structure. This one pushes back, and it
goes first because it is the one most often lost.

- Build the smallest thing that delivers the decision. Ship it. Then generalize **only
  after a second real caller exists.**
- Do not add an abstraction for a hypothetical. Two concrete implementations earn an
  interface; one does not.
- Do not build a plugin system for a plugin that does not exist yet.
- A working recommendation on screen this week beats a beautiful architecture next month.

If a change adds a layer, a config knob, or a base class, the PR description must name
the concrete second caller that justifies it. If it cannot, delete the layer.

### 2.1 Code reusability

- Shared logic lives in `backend/src/ff/core/` (config, logging, errors, retry, cache)
  and `backend/src/ff/domain/` (the types everything speaks).
- No copy-paste between adapters. If two providers need the same normalization, it goes
  in `adapters/_common.py`.
- The frontend and backend share types via generated OpenAPI client — hand-written
  duplicate TypeScript interfaces are a defect.
- **Anti-rule:** do not extract a helper used once. Reuse is discovered, not designed.

### 2.2 Modularity

Strict layering. Dependencies point one direction only:

```
api  →  services  →  domain  ←  adapters
                       ↑
                     core (everything may use)
```

- `domain/` — pure data + pure functions. **No I/O, no network, no clock, no env vars.**
  It must be testable with zero mocks. This is where the decision math lives.
- `adapters/` — one module per external system (yahoo, espn, sleeper, nflverse, odds,
  weather). Each converts foreign shapes into `domain` types at its boundary and never
  leaks a provider-specific dict upward.
- `services/` — orchestration. Fetch via adapters, compute via domain, decide.
- `api/` — HTTP only. Thin. No business logic in a route handler, ever.

Test of the boundary: `grep -r "import requests\|httpx" src/ff/domain/` must return
nothing. CI enforces it.

### 2.3 Extensibility

The parts that will genuinely change are known, so only these get seams:

| Seam | Why it is real |
|---|---|
| `ProjectionSource` | ESPN today, FantasyPros tomorrow, own model later. Ensemble needs ≥2. |
| `WriteExecutor` | Yahoo write API may be unavailable (see §6). Fallback path is required. |
| `BidStrategy` | The FAAB math will be rewritten as it gets tested against real outcomes. |

Everything else stays concrete until §2.0's second-caller test is met. Providers register
themselves in a registry; adding one is a new file plus a one-line registration, never an
edit to a switch statement scattered across the codebase.

### 2.4 Robustness

Every external source in this app is either undocumented, rate-limited, or both.
Assume each one is down right now.

- **Degrade, never crash.** Missing a weather feed drops a small adjustment; it does not
  drop the recommendation. Every source declares whether it is `required` or `optional`.
  The UI shows which inputs were unavailable and what that does to confidence.
- **Check `status_code` before parsing.** Yahoo returns HTTP 999 with an HTML body when
  throttling. A blind `.json()` raises outside your error handling.
- **Retry with backoff and jitter** on 999 / connection reset / `RemoteDisconnected`.
  Serialize Yahoo calls; never parallelize them — throttling is per app ID.
- **Refresh the Yahoo token proactively at 55 minutes**, not reactively on 401.
- **Writes are idempotent and journaled.** Every write records intent → approval →
  request → response in `write_journal`. A crash mid-transaction must be recoverable by
  reading that journal, not by guessing.
- **Cache aggressively** with per-source TTL. League settings change ~never in a season.
  Sleeper's full player list is ~5 MB and must be fetched at most once daily.

### 2.5 Observability

- **Structured JSON logs only.** No `print`. Every log line carries `request_id`,
  `source`, `duration_ms`.
- **Decisions are explainable or they are worthless.** Every recommendation persists its
  full input snapshot and the reason it won: projections used, distributions, the
  computed win probability delta, which sources were stale. A recommendation the user
  cannot interrogate will not be trusted, and an untrusted recommendation is not used.
- **Track calibration.** Store every projection and every actual outcome. Weekly, compute
  MAE and hit rate per source. The app must be able to answer "were you right?" — if it
  cannot, we have no way to know it is working. This is the single most important
  observability requirement in the project.
- Health endpoint reports per-source last-success time and staleness.

### 2.6 Consistency

- Python: `ruff` + `black` (line length 100) + `mypy --strict` on `domain/` and
  `services/`. TypeScript: `strict: true`, no `any`.
- One error type family (`ff.core.errors`), one config object (`ff.core.config`), one
  logger factory. Never a second way to do an existing thing.
- Naming is shared across the stack: a `PlayerId` is a `PlayerId` in Python, in
  TypeScript, and in the database.
- Money is integer dollars. Time is UTC internally, US/Pacific at the edges. Week numbers
  come from Sleeper's `/state/nfl`, never computed from a date.

---

## 3. The intellectual honesty rule

This is a prediction product built on weak predictions. Overclaiming is the main way it
fails the user.

Known and non-negotiable:

- The best weekly projections explain roughly **3–23% of variance** in actual points,
  depending on position (RB best, QB/WR/TE often single digits). MAE is ~5 points.
- Therefore **a projection gap under ~2 points is noise.** The UI must say "too close to
  call," not manufacture a winner.
- Start/sit optimization moves win probability by roughly **1–3 points per week**. It is
  real and it is small. Do not market it as more.
- FAAB has larger stakes and worse inputs. Roughly **85–90% of big FAAB spends
  ($100+ on a $1000 budget) were graded failures** in the one real dataset available.
- Every number the app shows carries an uncertainty band or it does not get shown.

Rules that follow:

1. No recommendation without a stated confidence and the reason.
2. When the model cannot separate two options, say so and stop.
3. Never present a folk heuristic as an empirical finding. `docs/DECISION_MATH.md`
   labels every claim `[empirical]`, `[theory]`, or `[folk]`.
4. Backtest before believing. A strategy that has not been run against past weeks is a
   hypothesis, not a feature.

---

## 4. The decision math (summary; full detail in docs/DECISION_MATH.md)

**Start/sit.** Maximize P(win), not expected points. With `X` = candidate's points and
`D` = (rest of my starters) − (opponent's total), you win iff `X + D > 0`, so rank by
`E_D[S_X(−D)]` — the survival function of X averaged over the deficit distribution.
Consequences: as a **heavy favorite** take the floor; as a **heavy underdog** take the
ceiling even at a lower mean; in a **close matchup** expected points is the right answer.
Implement by Monte Carlo over correlated player distributions.

**Lineup assembly.** For a standard roster the slot-eligibility sets are nested
(FLEX ⊇ RB∪WR∪TE), which makes the problem a matroid — **greedy by projection is provably
optimal** for expected points. Do not import an ILP solver. The hard part is statistical,
not combinatorial: typically only 1–2 slots are contested, so enumerate the handful of
candidate lineups exhaustively and simulate each.

**FAAB.** It is a first-price sealed-bid auction with **common values** and a hard budget.

```
bid = value − winner's_curse_shading − option_value(λ_t) × dollars_committed
```

- Shade for `n` = managers who *actually want this player* (roster hole + budget), not
  league size. Equilibrium shading ≈ `(n−1)/n`.
- Shade **further** for the winner's curse: values are common, signals are noisy, and
  winning means you had the highest noisy estimate. The 85% bust rate is this effect.
- `λ_t` (shadow price of a reserved dollar) declines to ~0 by Week 12. **Unspent FAAB at
  season end is a strict error.** Keep ~$15–20 reserved against a late RB1 injury.
- Bid your reservation value once. Probing low is incoherent — a losing bid costs nothing.

---

## 5. Safety invariants (never violate)

1. **No write to Yahoo without a recorded, explicit user approval** referencing that exact
   payload. Not a config flag. Not "auto mode." An approval row.
2. **FAAB spend cap enforced server-side**, checked immediately before submission against
   live remaining budget. A bid exceeding remaining budget is rejected, not clamped.
3. **Never drop a player** on the do-not-drop list or with `is_undroppable`.
4. **Approvals expire.** An approval older than 6 hours, or created for a different game
   week, is void. Re-approve.
5. **Dry-run is the default.** `FF_WRITE_ENABLED=false` unless explicitly set.
6. Secrets live only in `.env` / the host secret store. Never logged, never committed,
   never rendered in an error. `.env` is in `.gitignore` and in the Claude deny list.

---

## 6. The known unknown: Yahoo write access

As of 2026-09-13 Yahoo's developer portal states the Fantasy Sports API is
**read-only by default**, with write access granted only by application review
(https://sports.yahoo.com/developer/access/). The write endpoints remain fully
documented and library support exists, but no recent confirmation of a successful write
was found.

This is the single biggest risk to the product. Handle it:

1. Apply for read/write access immediately; state single-league personal use in the notes.
2. Enable Read/Write on the app in YDN **and** request the `fspt-w` scope. Both are
   required; a mismatch silently yields a read-only token.
3. Build behind the `WriteExecutor` interface from day one, with two implementations:
   - `YahooApiExecutor` — the real thing.
   - `AssistedExecutor` — produces a copy-paste-ready move list plus a deep link to the
     Yahoo page, so the product still works if writes are refused.
4. A `scripts/probe_write.py` smoke test proves which path is live. Run it before
   trusting anything in the write layer.

Never let the write question block the read + recommendation half of the app. That half
is ~80% of the value and depends on nothing Yahoo has to approve.

---

## 7. Working agreements

- **Plain English in everything a human reads** — UI copy, PR descriptions, docs, commit
  messages. Short sentences, plain nouns, real numbers. See the `plain-english` skill.
- Tell me when I am wrong. Steel-man first, then fact-check, then say what breaks. Do not
  agree to be agreeable.
- Every behavior change ships with a test. A bug fix starts with a failing regression test.
- Track work in `PROGRESS.yaml`, defects in `BUGS.yaml`. A `TODO` in code without a
  `BUGS.yaml` entry is a defect.
- Before any network call to a source, read `docs/DATA_SOURCES.md` — several are
  undocumented and fragile, and the rules for each are written down there.
- Probe the data, not the documentation. Several upstream docs are known to be stale.

---

## 8. Layout

```
backend/src/ff/
  core/       config, logging, errors, retry, cache, clock
  domain/     pure types + decision math (no I/O — enforced in CI)
  adapters/   yahoo/ espn/ sleeper/ nflverse/ odds/ weather/
  services/   projections, lineup, faab, approvals, calibration
  api/        FastAPI routers
frontend/     Next.js — /lineup, /waivers, /history
docs/         ARCHITECTURE.md PRD.md DECISION_MATH.md DATA_SOURCES.md YAHOO_SETUP.md DECISIONS.md
```

Commands: `make setup` `make dev` `make test` `make check` `make probe`
