# Fantasy Football Copilot

Tells me who to start and what to bid, and why. I approve; it executes.

One user, one league, two decisions a week. Not a DFS optimizer, not a SaaS.

## Start here

1. **`CLAUDE.md`** — the contract. Engineering principles, safety invariants, the honesty
   rule. Read it before changing anything.
2. **`docs/YAHOO_SETUP.md`** — apply for Yahoo API access today. It is the long pole and
   nothing else unblocks it.
3. **`docs/PRD.md`** — what the two screens do and what would make this fail.
4. **`docs/DECISION_MATH.md`** → `.claude/skills/fantasy-decision-math/` — the math, with
   every claim labelled empirical, theory, or folk.

## Quick start

```bash
make setup          # install backend and frontend deps
cp .env.example .env
# fill in Yahoo credentials — see docs/YAHOO_SETUP.md
make auth           # authorize with Yahoo
make leagues        # find your league and team keys, put them in .env
make probe          # does Yahoo actually allow writes? (see below)
make test
make dev            # backend on :8000, frontend on :3000
```

## The one big risk

Yahoo now grants Fantasy API **write access by review only**, and read-only by default.
Writes may simply not be available. So:

- The read half — projections, lineup recommendation, FAAB analysis — depends on nothing
  Yahoo has to approve and is about 80% of the value. Build it first.
- The write half sits behind a `WriteExecutor` interface with a fallback that renders the
  move plus a deep link, so the product still works if writes are refused.
- `make probe` settles which world you are in and writes the answer to
  `.yahoo_write_status`, which is shown at the start of every Claude session.

## What it will and will not do

It will remove error and inconsistency from two weekly decisions, and it will keep score of
whether it is helping.

It will not make you dominant. The best weekly projections explain roughly 3–23% of the
variance in actual points, and perfect start/sit is worth a few percentage points of win
probability a week. FAAB has larger stakes and worse inputs — in the one real dataset,
85–90% of big FAAB spends were graded failures. The app is built to say that out loud rather
than manufacture confidence. See `CLAUDE.md` §3.

## Layout

```
CLAUDE.md            the contract
PROGRESS.yaml        what is done, what is next
BUGS.yaml            defects and known limitations
.claude/             settings, hooks, agents, commands, skills
docs/                architecture, PRD, data sources, Yahoo setup, decision log
backend/src/ff/      core / domain / adapters / services / api
frontend/            Next.js
scripts/             auth, league listing, write probe
```

## Cost

$9/month — a FantasyPros API key for consensus rankings and their std-dev field, which is
the cheapest credible uncertainty input available. Everything else is free. See
`docs/DATA_SOURCES.md`.
