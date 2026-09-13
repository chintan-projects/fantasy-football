# Data sources

Verified live 2026-09-13 (NFL Week 1). Total cost of the recommended stack: **$9/month.**

Rule: **probe the data, not the documentation.** Several upstream docs here are known to be
stale — nflverse's own schedule page claims there is no injury data for recent seasons, and
the release assets prove otherwise.

## Recommended stack

| Source | Used for | Auth | Cost | Reliability |
|---|---|---|---|---|
| nflverse `games.csv` | historical spreads/totals/roof/temp/wind | none | $0 | high |
| ESPN scoreboard | live spread, total, weather, indoor flag | none | $0 | **fragile** |
| ESPN `kona_player_info` | weekly projections, ownership, ADP, auction value | none | $0 | **fragile** |
| nflverse injuries | report status + practice participation | none | $0 | high |
| nflverse snap counts | usage/role | none | $0 | high (lags ~1 day) |
| Sleeper `/state/nfl` | canonical current week | none | $0 | high |
| Sleeper trending | FAAB demand signal | none | $0 | high |
| FantasyPros | ECR + **std-dev** (uncertainty input) | `x-api-key` | **$8.99/mo** | high |
| Open-Meteo | wind for outdoor games only | none | $0 | high |

## Details

### nflverse — use `nflreadpy`, not `nfl_data_py`

`nfl_data_py` is **archived and deprecated**: *"No further maintenance or updates are
planned."* Use **`nflreadpy`** (`pip install nflreadpy`). It returns **Polars**, not pandas —
call `.to_pandas()` if you need pandas.

Data lives as GitHub release assets (parquet / csv.gz) at
https://github.com/nflverse/nflverse-data/releases. No auth, no published rate limit,
caching built into nflreadpy.

**License is ambiguous — flag it.** The *packages* are MIT. The `nflverse-data` repo has
**no data license file**. Community norm is attribution. Do not assume redistribution rights.
Personal use is fine; do not publish the raw data.

In-season cadence:

| Dataset | Cadence |
|---|---|
| Play-by-play, player/team stats | nightly + intra-gameday; **Thursday is cleanest** (post stat correction) |
| Schedules / games | every 5 minutes |
| Rosters, depth charts, PFR advanced | daily 07:00 UTC |
| Snap counts (PFR) | 4×/day, 00/06/12/18 UTC |
| Next Gen Stats | nightly 3–5am ET |
| **Participation (snap-level)** | **post-season only — useless in-season** |

**Injuries: the docs are wrong.** Verified directly — `injuries_2026.parquet` returns 200.
Columns include `report_primary_injury`, `report_status`, and crucially
`practice_primary_injury`, `practice_secondary_injury`, `practice_status` (Full/Limited/DNP).
Coverage is partial (182 rows in Week 1 vs a typical 400–600) — treat as incomplete.

### nflverse `games.csv` — free historical odds and weather

`https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv` (2.2 MB, updated
every 5 min). Columns include `spread_line, total_line, moneylines, over/under odds, roof,
surface, temp, wind, div_game, away_rest, home_rest, stadium, referee`.

**This means you need no paid odds API to backtest against closing totals.** These are
closing/consensus lines, one row per game — a modeling input, not a live-edge tool.

### ESPN — free but unlicensed and unversioned

Scoreboard (one call gets odds + weather + venue):
```
GET https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard
```
Returns `competitions[].odds[]` (DraftKings: `spread`, `overUnder`), `event.weather`
(`{displayValue, temperature, conditionId}`, AccuWeather-sourced), and
`competitions[].venue.indoor`.

Fantasy projections:
```
GET https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/2026/segments/0/leaguedefaults/3?view=kona_player_info
Header: X-Fantasy-Filter: {"players":{"limit":2000}}
```
The decoder ring: **`statSourceId: 0` = actual, `statSourceId: 1` = ESPN projection.**
`statSplitTypeId: 1` = single week. `appliedTotal` is the fantasy-scored value.
`leaguedefaults/3` = standard PPR. The `X-Fantasy-Filter` header is **mandatory** to get past
the default result cap.

**Treat all ESPN endpoints as a scrape.** Undocumented, unversioned, no contract, no
deprecation notice. ESPN has silently changed hosts before (`fantasy.espn.com` →
`lm-api-reads.fantasy.espn.com`) and tightened caps. Wrap every call, **validate the schema
on every fetch**, cache aggressively, keep a fallback. **Never put ESPN on the critical path.**
You have no license — personal use only, no redistribution.

### Sleeper — the clearest terms of any source

Base `https://api.sleeper.app/v1`, docs at https://docs.sleeper.com/. No auth.

- `/state/nfl` → `{"week":1,"season":"2026",...}`. **Use this as the week oracle.** Never
  compute the week from a date.
- `/players/nfl/trending/add?lookback_hours=24&limit=25`
- `/players/nfl` is ~5 MB — Sleeper explicitly says cache it yourself and fetch **at most
  once daily**.

**Rate limit: stay under 1000 calls/minute.** License: *"Free to use for non-commercial
purposes."* Explicitly permissive for this use, explicitly restrictive beyond it.

**Analytical caveat on trending adds:** `count` is raw adds, not a rate, and is not
normalized by availability. A player rostered in 60% of leagues has a mechanically smaller
add pool than one rostered in 5%. **Normalize by `(1 − rostered%)`** or you will
systematically under-rank already-popular breakouts. It is also a *lagging* consensus signal
— by the time a player trends, your FAAB competitors have seen the same news.

### FantasyPros — $8.99/mo, worth it for one field

Base `https://api.fantasypros.com/public/v2/json`, auth `x-api-key`. Keys at
`secure.fantasypros.com/api-keys/request`.

| Tier | Cost | Notes |
|---|---|---|
| Free | $0 | **sample data only** — not a usable free source |
| Premium | **$8.99/mo** | production keys, personal/non-commercial only |
| Commercial | custom | redistribution rights, SLA |

Endpoints: `/{sport}/{season}/consensus-rankings` (ECR + ADP from 130+ experts **with tiers
and best/worst/std-dev**), `/nfl/{season}/projections`, `/{sport}/injuries`, `/{sport}/news`.

**The `std-dev` field is the reason to pay.** It is the cheapest credible proxy for
player-level epistemic uncertainty available anywhere. Note it measures *expert
disagreement*, not week-to-week volatility — see `fantasy-decision-math` §1.

### The Odds API — probably skip it

Free tier is **500 credits/month**, then $30/mo (20K). **Credits ≠ requests:**
`cost = markets × regions`, and historical carries a **10× multiplier**. So 500 credits ≈ 83
calls/month at 3 markets × 2 regions — about 20 a week.

Since ESPN's scoreboard gives the same spread and total for free, **this is only worth paying
for if you need multi-book line shopping, line movement over time, or player props.** For
team totals driving projections, skip it.

### Open-Meteo — weather, no key

```
GET https://api.open-meteo.com/v1/forecast?latitude=..&longitude=..
    &hourly=temperature_2m,wind_speed_10m,precipitation_probability
    &temperature_unit=fahrenheit&wind_speed_unit=mph
```
Free for non-commercial, CC-BY-4.0. You supply stadium lat/lon. **Gate on `roof` ∈ {dome,
closed} and skip the call entirely.** And per the decision math: below 20 mph wind, do nothing
with the result.

## Reliability grading

**Reliable:** nflverse releases (automated, versioned, years of uptime), Sleeper (documented,
versioned, explicit limits and ToS), Open-Meteo, FantasyPros (you have a contract).

**Fragile:** everything ESPN. See above.

**Fragile one level down:** nflverse's *upstream* dependencies. Snap counts depend on Pro
Football Reference's publishing schedule, FTN charting on FTN's. When PFR changes a page,
snap counts stall.

**Structurally missing:** in-season snap-level participation. Weekly snap *counts* are the
substitute and they lag ~1 day.

## Rules for this codebase

1. Every adapter declares `required` or `optional`. Optional sources degrade silently into a
   confidence note in the UI; they never fail a recommendation.
2. Every adapter validates its response schema on **every** fetch, not at startup.
3. Per-source TTL cache with the source's real cadence. Never refetch faster than upstream
   updates.
4. Record `last_success_at` per source and surface staleness in `/health` and in the UI.
5. Personal use only. Do not redistribute ESPN or nflverse data.
