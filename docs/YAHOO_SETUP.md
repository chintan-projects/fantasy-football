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
| API Permissions | **Fantasy Sports** → **Read/Write** |

Two things people get wrong:

- **The redirect URI must be HTTPS.** Plain `http://localhost` is rejected. `oob` is still
  documented but the registration form wants a real URI and the maintained libraries have
  moved on — register the HTTPS one.
- **Checking Read/Write on the app is separate from requesting the `fspt-w` scope in the
  authorize URL.** Both are required. If they disagree you get a read-only token with **no
  error message** — writes just fail later with a 401 that looks like an expired token.

Save the **Client ID** and **Client Secret**.

## Step 2 — Configure

```bash
cp .env.example .env
```

Fill in:
```
YAHOO_CLIENT_ID=...
YAHOO_CLIENT_SECRET=...
YAHOO_REDIRECT_URI=https://localhost:8080/callback
FF_WRITE_ENABLED=false          # leave false until the probe passes
```

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

## Step 4 — Find your league

```bash
make leagues
```

Prints every league your account is in with its key. Put yours in `.env`:

```
YAHOO_LEAGUE_KEY=...
YAHOO_TEAM_KEY=...
```

**Never hardcode the game id.** `461` is the 2025 season. The app resolves the current one at
runtime via `/game/nfl`.

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
