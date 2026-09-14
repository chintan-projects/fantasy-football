# Yahoo setup — do this first

Roughly 20 minutes, plus an unknown wait for Yahoo's review.

## Step 0 — Apply for API access (do this today, it gates everything)

Yahoo reviews every application, and **read access is the only thing on offer**: the access
page states "Write access is not available at this time." `[empirical]`, read 2026-09-14.
Apply for read anyway — it gates the entire product — but do not spend the notes field
arguing for writes. See `CLAUDE.md` §6 for what that means for the design.

1. Go to **https://sports.yahoo.com/developer/access/**
2. Fill in the form. What matters:
   - **Product description** — be specific. "A personal tool for one fantasy football league
     that recommends my weekly starting lineup and my FAAB waiver bids, and submits the moves
     I approve."
   - **Intended user base** — say **personal, single league, one user**.
   - **Expected users** — Small (<1,000).
   - **Notes** — this is the field that decides it. Say exactly which data you read and
     why: "I read my own league's settings, my roster, free agents, the transaction log and
     my FAAB balance, to recommend my weekly lineup and my waiver bids. One league, one
     user, no third-party data access, no redistribution." "
3. Yahoo's warning is real: *"incomplete or insufficiently detailed submissions cannot be
   evaluated and will be closed without further correspondence."* Do not write two sentences.

**Do not wait on this before building.** The read half of the app — projections, lineup
recommendation, FAAB analysis — needs nothing Yahoo has to approve. See `CLAUDE.md` §6.

## Step 1 — Create the app

https://developer.yahoo.com/apps/create/

| Field | Value |
|---|---|
| Application Name | `fantasy-football-copilot` |
| Application Type | **Installed Application** |
| Redirect URI | `https://localhost:8080/callback` |
| API Permissions | Whatever is offered — see below |

**There is no Fantasy Sports checkbox.** Probed on a real app 2026-09-14: the API
Permissions list offers OpenID Connect Permissions (Email, Profile) and nothing else. Every
guide written before this says to tick Fantasy Sports and choose Read/Write; that option is
not there. Creating the app gets you a client id and secret that authenticate fine and are
refused by every Fantasy endpoint with `additional_authorization_required` until the access
application in Step 0 is approved. That approval is the whole gate. `[empirical]`

Two more things people get wrong:

- **The redirect URI must be HTTPS.** Plain `http://localhost` is rejected. `oob` is still
  documented but the registration form wants a real URI and the maintained libraries have
  moved on — register the HTTPS one.
- **Do not send a scope.** Yahoo rejects every explicit `fspt-*` scope string and takes
  permissions from the app instead. `FF_YAHOO_SCOPE` defaults to empty; leave it there.
  Older guides tell you to request `fspt-w` and they are wrong.

Save the **Client ID** and **Client Secret**.

## Step 2 — Configure

```bash
cp .env.example .env
```

Fill in:
```
FF_YAHOO_CLIENT_ID=...
FF_YAHOO_CLIENT_SECRET=...
FF_YAHOO_REDIRECT_URI=https://localhost:8080/callback
FF_WRITE_ENABLED=false          # leave false; writes are not on offer, see CLAUDE.md 6
```

**Every variable takes the `FF_` prefix.** `Settings` sets `env_prefix="FF_"`, so a bare
`YAHOO_CLIENT_ID` is read by nothing, leaves the client id empty, and fails later at the
authorize step rather than at startup. This page said the unprefixed names until
2026-09-14.

`.env` is gitignored and blocked from tool reads. Keep it that way.

## Step 3 — Authorize

```bash
make auth
```

This starts a local HTTPS listener on port 8080 with a self-signed cert, opens the Yahoo
consent page, and writes the token to `.tokens/yahoo.json`. Your browser will warn about the
self-signed certificate — that is expected; proceed.

The access token lives **one hour**. The app refreshes proactively at 55 minutes. The refresh
token is long-lived and survives a password change. `redirect_uri` is required on the refresh
call too and must match exactly.

## Step 3b — The access application, and why the notes field is the whole thing

There is no self-serve path left. An app's API Permissions list offers OpenID Connect only;
there is no Fantasy Sports entry to tick, and creating another app does not help. Verified
against the real app on 2026-09-14. Approval on the application is the entire gate.

The form asks three things: expected users, your Client ID, and notes. The first two take a
second. The notes field is the whole application, because of what the page says about how
submissions are judged:

> Each application is reviewed by the Yahoo Fantasy Sports team, and given current request
> volume, incomplete or insufficiently detailed submissions cannot be evaluated and will be
> closed without further correspondence.

`[empirical]` — https://sports.yahoo.com/developer/access/, read 2026-09-14. Two
consequences. A thin submission is not queued, it is closed. And silence carries no
information: it looks identical to a pending review. No timeframe is published, and no
public report of a completed approval could be found — the open questions asking for one
(uberfastman/yfpy#84, derekrbreese/fantasy-football-mcp-public#18) are unanswered.

The page also says what it wants named: the product being built, the Yahoo Fantasy Sports
data required, and the intended user base, "including where access is limited to personal or
single league use". That last clause is the one favourable thing here — single-league
personal use is a category Yahoo asks you to declare, not one it asks you to apologise for.

**Expected users:** Small (<1,000). The honest number is 1.

**Notes — copy this, and change nothing that is not true:**

```
Product: a personal assistant for one Yahoo fantasy football team. It reads my league
once or twice a week and answers two questions: which players to start on Sunday, and
what to bid on whom out of my FAAB budget on Tuesday. It runs as an MCP server that only
I connect to, from Claude.

Users: one. Me. One league, one team. It is not distributed, not published, not sold, and
has no sign-up. There is no second user to add without my own credentials.

Data required, read only:
  GET /game/nfl                                      resolve the current season's game id
  GET /users;use_login=1/.../leagues                 find my own league
  GET /league/{key}/settings                         roster slots and scoring rules
  GET /league/{key}/players                          free agents, for waiver analysis
  GET /league/{key}/transactions;type=add            past winning FAAB bids in my league
  GET /team/{key}                                    my FAAB balance
  GET /team/{key}/roster;week={n}                    my roster
  GET /team/{key}/matchups;weeks={n}                 my opponent that week

Write access: not requested. I make the moves myself in the Yahoo app; the tool gives me
the decision and a link.

Rate and handling: calls are serialized behind a single lock, paced at one per second,
and cached per endpoint -- league settings for a week, rosters for minutes. A normal week
is a few dozen requests. Nothing is redistributed, republished or shared; the data is
read, used to compute one answer for me, and stored only on a machine I own.
```

Three things this is doing on purpose. It names the endpoints rather than saying "league
data", so the reviewer does not have to guess at scope. It declines write access in
writing, which removes the reason to think about it. And it says the rate limiting is
already built, which it is — see `adapters/yahoo/client.py`.

If you already submitted something thinner, submit this. A closed application is not
reopened by waiting, and re-applying costs nothing.

## Step 4 — Find your league

Two numbers, and you can read both off your own team page without the API:

```
https://football.fantasysports.yahoo.com/f1/123456/3
                                            ^^^^^^ ^
                                            league team
```

```
FF_YAHOO_LEAGUE_KEY=123456
FF_YAHOO_TEAM_KEY=3
```

**Never hardcode the game id.** `461` is the 2025 season, `470` is 2026, and it rolls over
every year. The app resolves the current one at runtime via `/game/nfl` and composes both
keys from it. Full keys (`470.l.123456` and `470.l.123456.t.3`) are still accepted, but the
bare numbers are the better value because they cannot go stale.

Once the API is reachable, `make leagues` prints every league on the account with its key —
useful for confirming you read the URL right, not required to configure anything. It needs
read access, so before approval it answers `additional_authorization_required`; the browser
URL above does not.

## Step 5 — Probe write access

This is the moment of truth.

```bash
make probe
```

It performs the smallest possible reversible write (a roster PUT that sets your lineup to
exactly what it already is) and reports one of:

- `WRITE OK` — writes work. Set `FF_WRITE_ENABLED=true` when you are ready to use the
  approval flow.
- `WRITE DENIED (401/403)` — the token is read-only. Check that Read/Write is enabled on the
  app *and* that the authorize URL requested `fspt-w`. Re-authorize. If it still fails, your
  access application has not been approved yet.
- `THROTTLED (999)` — try again in a few minutes.

The result is written to `.yahoo_write_status` and shown at the start of every Claude session.

## If write access is refused

The app still works. `AssistedExecutor` produces a ranked move list with the exact bid
amounts and a deep link to the right Yahoo page, so the analysis is done for you and only the
clicking is manual. Switch with:

```
FF_WRITE_EXECUTOR=assisted
```

Re-apply for write access when you have usage to point to.

## Troubleshooting

| Symptom | Cause |
|---|---|
| 401 immediately after a fresh authorize | Read/Write not enabled on the app in YDN |
| 401 after ~1 hour of working fine | Token expired; refresh is broken — check `redirect_uri` matches on the refresh call |
| 401 only on writes, reads fine | Read-only token. `fspt-w` scope and app permission disagree. |
| HTTP 999, HTML body | Throttled. Back off seconds, not milliseconds. Serialize your calls. |
| `JSONDecodeError` on a Yahoo response | You parsed before checking `status_code`. 999 returns HTML. |
| Empty pending waiver list | You must pass `team_key` — pending claims are invisible in the plain transactions list. |
