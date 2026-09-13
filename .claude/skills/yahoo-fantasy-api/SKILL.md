---
name: yahoo-fantasy-api
description: "Verified reference for the Yahoo Fantasy Sports API — OAuth2, the nested-array JSON quirk, read endpoints, and the exact XML for roster PUTs, add/drop POSTs, FAAB waiver claims, bid edits and cancels. Use whenever writing or debugging any code that talks to fantasysports.yahooapis.com, choosing a Python client library, handling 401/999 throttling, or reasoning about whether write access is available. Facts verified 2026-09-13."
triggers:
  - "yahoo fantasy"
  - "fantasysports.yahooapis"
  - "faab_bid"
  - "waiver claim"
  - "set lineup"
  - "yahoo oauth"
---

# Yahoo Fantasy Sports API

Verified 2026-09-13. Docs moved: `developer.yahoo.com/fantasysports/guide/` now redirects to
**https://sports.yahoo.com/developer/docs/**.

## 0. Read this first — write access is gated

Yahoo states on https://sports.yahoo.com/developer/access/:

> "The Yahoo Fantasy Sports API currently provides read access only. Write access is not
> available at this time. Each application is reviewed by the Yahoo Fantasy Sports team."

> "Access is read-only by default. If your use case is unique and requires read/write
> access, please include additional details in the notes section below."

The write endpoints are still fully documented and library support ships, but **no recent
confirmation of a successful write was found.** Treat every write path as unproven until
`scripts/probe_write.py` succeeds against a real token. Design so the read half never
depends on the answer.

## 1. OAuth 2.0

| | |
|---|---|
| Authorize | `https://api.login.yahoo.com/oauth2/request_auth` |
| Token | `https://api.login.yahoo.com/oauth2/get_token` (POST) |
| Client auth | HTTP Basic, `base64(client_id:client_secret)` |
| Access token life | **3600s**. Refresh proactively at 3540s. |
| Refresh token | Long-lived, survives password change |

- `redirect_uri` is **required on the refresh call too**, and must match the authorize call.
  This is the most common integration bug.
- Scopes: `fspt-r` (read), `fspt-w` (read/write).
- **Scope in the URL is not enough.** Read/Write must also be enabled on the app itself in
  YDN. A mismatch silently yields a read-only token with no error.
- `oob` is still documented and is still `yahoo-oauth`'s default, but registration requires
  a redirect URI and Yahoo rejects plain `http://localhost`. Register
  `https://localhost:8080` and run a local HTTPS listener. Keep `oob` as fallback only.
- App type: "Installed Application". API Permissions → Fantasy Sports → Read/Write.

## 2. Base URL and shapes

Base: `https://fantasysports.yahooapis.com/fantasy/v2`. **Everything requires OAuth** —
there is no anonymous tier any more (verified: bare GET returns 401).

```
League key   {game_id}.l.{league_id}            461.l.1000
Team key     {game_id}.l.{league_id}.t.{id}     461.l.1000.t.1
Player key   {game_id}.p.{player_id}            461.p.30121
Waiver claim {game_key}.l.{league_id}.w.c.{id}  461.l.1000.w.c.2_6461
```

**Never hardcode the game_id.** `461` is 2025. Resolve at runtime:
`GET /game/nfl` or `GET /games;game_codes=nfl;seasons=2026`. Yahoo translates a game_code
to the numeric id on parse.

URI grammar: params are **semicolon-delimited** and follow the resource key —
`/league/461.l.1000;out=settings/teams;team_keys=...`. `;out=` pulls in one extra level of
sub-resources and cannot be chained or parameterized.

### JSON is a trap

`?format=json` works but is **undocumented** (a real `?query` param, not `;matrix`). Keep an
XML fallback. The JSON is a mechanical XML transliteration:

1. Repeated elements become objects keyed by `"0"`, `"1"`, … plus a `"count"` sibling — not arrays.
2. One entity is split across a list of single-key dicts you must merge.
3. **Sub-resources are interleaved as positional siblings.** Requesting
   `/players/percent_owned` puts player data at index `i` and ownership at `i+1`, so you
   step by 2.

Parse the XML instead where you can — it is unambiguous. Write responses are XML anyway.

## 3. Reads

```
GET /league/{lk}/settings        # roster_positions, stat_modifiers, uses_faab, waiver_type
GET /league/{lk}/scoreboard;week={w}
GET /team/{tk}/roster;week={w}   # NFL uses week, not date
GET /team/{tk}/matchups;weeks=1,3,6
GET /player/{pk}/percent_owned
GET /league/{lk}/players;player_keys={pk}/ownership
GET /game/nfl/stat_categories    # stat_id -> name
```

**Check `uses_faab` before ever sending a `faab_bid`.**

Free agents — all filters are league-context only:

```
GET /league/{lk}/players;start=0;count=25;status=FA;position=RB;sort=PTS;sort_type=week;sort_week=2/percent_owned?format=json
```

`status`: `A` available / `FA` free agents / `W` waivers / `T` taken / `K` keepers.
`sort`: `{stat_id}` | `NAME` | `OR` | `AR` | `PTS`.
**Pagination caps at 25 per page.** Page with `start`.

## 4. Writes — exact payloads

Header `Content-Type: application/xml`. PUT → **200**, POST → **201**. Responses are XML.

### Set lineup
`PUT /team/{team_key}/roster` — the week goes in the **body**, not the URL. Atomic: an
invalid move rejects the whole request and changes nothing. Players you omit stay put.

```xml
<?xml version="1.0"?>
<fantasy_content>
  <roster>
    <coverage_type>week</coverage_type>
    <week>13</week>
    <players>
      <player><player_key>461.p.8332</player_key><position>WR</position></player>
      <player><player_key>461.p.1423</player_key><position>BN</position></player>
    </players>
  </roster>
</fantasy_content>
```

Position strings must match `/settings` `roster_positions` exactly: `QB RB WR TE W/R/T K DEF BN IR`.

### Add / drop
`POST /league/{league_key}/transactions`

```xml
<fantasy_content><transaction>
  <type>add</type>
  <player><player_key>{pk}</player_key><transaction_data>
    <type>add</type><destination_team_key>{tk}</destination_team_key>
  </transaction_data></player>
</transaction></fantasy_content>
```

Drop uses `<source_team_key>`. The `<players>` wrapper appears **only** in the two-player
`add/drop` form.

### FAAB waiver claim
Same endpoint. There is no separate creation type — POST a normal add or add/drop and
Yahoo converts it to a claim if the player is on waivers.

```xml
<?xml version='1.0'?>
<fantasy_content><transaction>
  <type>add/drop</type>
  <faab_bid>25</faab_bid>
  <players>
    <player><player_key>461.p.5484</player_key><transaction_data>
      <type>add</type><destination_team_key>461.l.1000.t.6</destination_team_key>
    </transaction_data></player>
    <player><player_key>461.p.6327</player_key><transaction_data>
      <type>drop</type><source_team_key>461.l.1000.t.6</source_team_key>
    </transaction_data></player>
  </players>
</transaction></fantasy_content>
```

Two things to get right:

- **`<faab_bid>` is a direct child of `<transaction>`**, a sibling of `<type>`, and must
  come **before** `<players>`. It is not inside `transaction_data`.
- **Yahoo's own published example is buggy** — it uses `destination_team_key` on the drop
  leg. Use **`source_team_key`**. Working library code agrees.

FAAB also works on a bare add with a single unwrapped `<player>`.

### Edit a pending claim
`PUT /transaction/{transaction_key}` — only `waiver` and `pending_trade` accept PUT.

```xml
<?xml version='1.0'?>
<fantasy_content><transaction>
  <transaction_key>461.l.1000.w.c.2_6093</transaction_key>
  <type>waiver</type>
  <waiver_priority>1</waiver_priority>
  <faab_bid>20</faab_bid>
</transaction></fantasy_content>
```

### Cancel a pending claim
`DELETE /transaction/{transaction_key}`, no body.

### Finding your pending claims — the discoverability trap

Yahoo, verbatim: *"Pending transactions will not show up if you simply ask for all of the
transactions in the league, because they can only be seen by certain teams."*

```
GET /league/{lk}/transactions;types=waiver,pending_trade;team_key={tk}
```

`waiver` and `pending_trade` are valid **only** with `team_key` also supplied. There is no
other way to get a `w.c.` key. Full lifecycle: POST claim → GET with team_key to find the
key → PUT to adjust → DELETE to cancel.

## 5. Errors and throttling

Yahoo publishes **no numeric rate limit**, only a discretionary throttling policy.

- **HTTP 999** = throttled. The body is often an **HTML** page, not JSON — a blind
  `.json()` raises outside your error handling. **Check `status_code` before parsing.**
  No `Retry-After`. Throttling is keyed to your **app/client ID**, not the user token.
- `RemoteDisconnected` / connection reset under heavy looping — same retryable class.
- **401/403**: distinguish expired token (body contains `token_expired` or `oauth_problem`
  → refresh and retry once) from genuinely unauthorized (wrong scope, write on a read-only
  token → do not retry).

Client policy: serialize all Yahoo calls, never parallelize. Exponential backoff with
jitter, seconds not milliseconds. Refresh at 55 min. Cache settings/roster_positions/
stat_categories for the season. Use `;out=` to batch. The response XML carries a
`refresh_rate` attribute — use it as a TTL hint.

**NFL timing:** load spikes Sunday 11:00–13:00 ET as lineups lock. Submit well before
kickoff with retry budget to spare.

## 6. Python libraries

| Package | Latest | Released | Writes | FAAB |
|---|---|---|---|---|
| **`yahoo_fantasy_api`** (spilchen) | 2.12.3 | 2026-04-03 | yes | **yes** |
| `yahoofantasy` | 1.4.9 | 2025-10-18 | no | no |
| `yfpy` | 17.0.0 | 2025-09-14 | no | no |
| `yahoo-oauth` | 2.1.1 | 2024-12-17 | auth only | — |

**`yahoo_fantasy_api` is the only viable choice for writes** and the only one with FAAB.
Gaps you must fill yourself with raw calls on the same authenticated session:

1. **No DELETE anywhere** — you cannot cancel a claim through the library.
2. **No public method to edit a pending claim's bid** — `yhandler.put_transaction()` exists
   but only trade paths are wired up.
3. **Claim discovery is half-built** — call `yhandler.get_team_transactions()` directly;
   the public `League.transactions()` only documents `add,drop,commish,trade`.
4. Free-agent paging hardcoded to 25 and silently drops `status == "NA"` players.
5. Depends on `objectpath`, an old lightly-maintained package.

Pattern: use the library for the 90% it covers, keep a thin raw-HTTP escape hatch on
`sc.session` for the three things it cannot do.

## 7. Unverified — do not assume

- Whether writes function at all in 2026 (see §0). **Test first.**
- The 2026 NFL `game_id`. Resolve at runtime.
- Whether `oob` still works end to end.
- Any numeric rate limit.
- Whether pre-existing Read/Write apps are grandfathered past the access gate.
