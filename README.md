# Fantasy Football Copilot

Tells me who to start and what to bid, and why. I approve; it executes.

It is an **MCP server**. Claude connects to it as a custom remote connector, so the question
gets asked wherever Claude already is rather than at a URL that has to be remembered. Ten
tools. The ones that judge return a computed decision with its reasoning attached; only two
return raw data, for when a human just wants to look. That split is the whole design —
a recommendation nothing snapshotted is a recommendation nothing can ever score.

One user, one league, two decisions a week. Not a DFS optimizer, not a SaaS.

## Start here

1. **`CLAUDE.md`** — the contract. Engineering principles, safety invariants, the honesty
   rule. Read it before changing anything.
2. **`docs/YAHOO_SETUP.md`** — apply for Yahoo API access today. It is the long pole and
   nothing else unblocks it.
3. **`docs/PRD.md`** — the three conversations it is for, and what would make this fail.
4. **`docs/DEPLOY.md`** — how it gets in front of Claude.
5. **`docs/DECISION_MATH.md`** → `.claude/skills/fantasy-decision-math/` — the math, with
   every claim labelled empirical, theory, or folk.

## Quick start

```bash
make setup          # install dependencies
cp .env.example .env
# fill in Yahoo credentials — see docs/YAHOO_SETUP.md
make auth           # authorize with Yahoo
make leagues        # find your league and team keys, put them in .env
make probe          # have writes become available yet? (no — see below)
make test
make mcp-demo       # run all ten tools against fixtures, no network, no Yahoo needed
make mcp            # serve the MCP server locally on :8000/mcp
```

Then `docs/DEPLOY.md` to put it on Fly and connect it to Claude.

## The one big risk, now settled against us

Yahoo's access page says it plainly: **the Fantasy Sports API is read-only. Write access is
not available at this time** — not to anyone, not by review. Read 2026-09-14.

- The read half — projections, lineup recommendation, FAAB analysis, calibration — needs
  only read access, and it is about 80% of the value.
- The last step is a tap. The app does the whole decision and hands back the exact move
  plus a deep link into the Yahoo app; you make it yourself. That is the `AssistedExecutor`
  path and it is the shipping path, not a fallback.
- `YahooApiExecutor` stays in the tree, unproven. `make probe` is now a monitor for the day
  Yahoo changes its mind, not a gate on anything.

## What it will and will not do

It will remove error and inconsistency from two weekly decisions, and it will keep score of
whether it is helping. Ask it `has any of this actually been right?` and it reports the
start/sit record, whether its lineups beat simply starting the highest projections, and how
accurate each projection source has been — and it refuses to rank two sources until the
evidence separates them.

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
frontend/            Next.js — demoted 2026-09-13, kept not deleted
scripts/             auth, league listing, write probe, tool demo
Dockerfile fly.toml  the deployed MCP server
```

## Cost

A few dollars a month, all of it hosting: one `shared-cpu-1x` Fly machine pinned running
with a 1 GB volume. It has to stay up, because a suspended machine does not run Tuesday
night's snapshot.

Every data source is free. This used to say $9/month for a FantasyPros API key; that was
dropped once Sleeper turned out to serve Rotowire's projections for nothing, which is both
cheaper and genuinely independent of ESPN — which is what the ensemble actually needs. See
`docs/DATA_SOURCES.md`.
