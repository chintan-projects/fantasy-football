# Deploying the MCP server

Claude's custom connectors reach the server over the public internet, from Anthropic's
egress range `160.79.104.0/21`. There is no tunnel and no local option — a connector needs
a real HTTPS URL, so this has to be hosted somewhere. `[empirical]` Anthropic's connector
documentation, read 2026-09-13.

Fly, not Railway. The deciding detail is that this app keeps state on disk and runs its own
scheduler: Fly gives a persistent volume on the cheapest paid tier, and the machine can be
pinned running. Railway's volumes work too, but its sleep behaviour is the thing that would
quietly break the Tuesday job.

---

## Once, before the first deploy

### 1. A GitHub OAuth app

The server is for one person and still needs OAuth, because Claude supports OAuth with
dynamic client registration, OAuth with client id metadata, or no auth at all. A bearer
token is the `static_headers` type, which is in beta and is entered by an organization
administrator rather than by a user, and pure machine-to-machine `client_credentials` is
not supported — every connection requires a human to consent. `[empirical]`

At <https://github.com/settings/developers> → New OAuth App:

| Field                      | Value                                      |
| -------------------------- | ------------------------------------------ |
| Application name           | anything                                   |
| Homepage URL               | `https://ff-copilot.fly.dev`               |
| Authorization callback URL | `https://ff-copilot.fly.dev/auth/callback` |

Keep the client id and the generated secret.

### 2. Create the app and its volume

```bash
fly launch --no-deploy --name ff-copilot --region sjc
```

```bash
fly volumes create ff_data --size 1 --region sjc
```

One gigabyte is far more than SQLite needs here — a season of snapshots is measured in
megabytes — but it is the smallest Fly sells.

### 3. Secrets

Never in `fly.toml`; that file is committed.

```bash
fly secrets set FF_YAHOO_CLIENT_ID=... FF_YAHOO_CLIENT_SECRET=... FF_YAHOO_LEAGUE_KEY=... FF_YAHOO_TEAM_KEY=...
```

```bash
fly secrets set FF_GITHUB_CLIENT_ID=... FF_GITHUB_CLIENT_SECRET=... FF_ALLOWED_GITHUB_LOGIN=your-github-login FF_MCP_BASE_URL=https://ff-copilot.fly.dev
```

`FF_ALLOWED_GITHUB_LOGIN` is the second gate. GitHub will authenticate anyone; this is what
makes the server yours. Without it the process refuses to start with auth enabled.

### 4. The Yahoo token

The token is obtained by a browser redirect to `localhost`, so it is created on a laptop and
copied up:

```bash
make auth
```

```bash
fly ssh sftp put backend/.tokens/yahoo.json /data/yahoo.json --app ff-copilot
```

**Use `sftp put`, not `ssh console -C "tee ..."`.** `tee` writes to the file *and* to
stdout, so that form prints the access token and the refresh token to the terminal, where
they land in scrollback and shell history. It happened once here, on 2026-09-13, and cost a
token rotation. The access token expires in an hour; the refresh token does not, and mints
new ones until the app is de-authorized in Yahoo account security.

It refreshes itself in place after that. It lives on the volume, not in the image.

### If a token does leak

1. Yahoo account security → Apps connected to your account → remove the app. This is what
   actually kills the refresh token; waiting for expiry does not.
2. `make auth` on the laptop, then `sftp put` the new file.
3. Regenerating the client secret at developer.yahoo.com/apps invalidates everything issued
   under it, and then `.env` and the Fly secret both need updating.

---

## Deploy

```bash
fly deploy
```

```bash
curl -s https://ff-copilot.fly.dev/health
```

`authenticated` must be `true`. If it is `false` the GitHub credentials did not reach the
machine and the server is open to anyone with the URL.

---

## Connect Claude

Settings → Connectors → Add custom connector, with:

```
https://ff-copilot.fly.dev/mcp
```

Claude registers itself through dynamic client registration, sends you to GitHub, and comes
back. Any login other than `FF_ALLOWED_GITHUB_LOGIN` gets through GitHub and is then refused
at the first tool call.

The connector works on web, Desktop, Cowork and mobile, on all plans. `[empirical]`

**If it ends at "Client Not Registered".** The server is advertising
`client_id_metadata_document_supported` again. That makes Claude identify itself with a URL
instead of registering, which forces this server to fetch
`https://claude.ai/oauth/mcp-oauth-client-metadata` — and from a Fly datacenter IP that
returns Cloudflare's bot challenge, not JSON. `enable_cimd=False` in
`backend/src/ff/api/auth.py` is what keeps it off. Check with:

```bash
curl -s https://ff-copilot.fly.dev/.well-known/oauth-authorization-server | grep -c client_id_metadata
```

Zero is correct. Do not fix this by sending a browser user agent.

---

## What runs on its own

MCP is request-driven: nothing here fires without a tool call. The two jobs that must
happen on a clock run inside the same process.

| When (Pacific) | Job             | What it does                                                             |
| -------------- | --------------- | ------------------------------------------------------------------------ |
| Tuesday 06:00  | actuals ingest  | What everyone scored last week, from nflverse. After Monday night       |
| Tuesday 20:00  | waiver snapshot | Board plus opponent model, captured **before** waivers process overnight |
| Sunday 09:00   | lineup snapshot | Lineup inputs, captured while the roster can still be changed            |

None of them notifies anyone and none writes to Yahoo. They exist so that a recommendation
can be scored later against the world as it was when the decision was live — grading a 9am
decision against a 3pm world measures the wrong thing.

They are in-process rather than on their own machine because a Fly volume attaches to one
machine only, and the database is on the volume. `[empirical]` That is also why
`auto_stop_machines = false`: a suspended machine runs no jobs.

---

## Limits worth knowing

| Limit             | Value               | Consequence here                                                         |
| ----------------- | ------------------- | ------------------------------------------------------------------------ |
| Tool result size  | ~150,000 characters | `ff_waiver_board` caps at 50 targets; nothing returns a full player pool |
| Tool call timeout | 240 seconds         | A 20,000-draw simulation takes a few seconds; the margin is wide         |
| Anthropic egress  | `160.79.104.0/21`   | Only relevant if a firewall is added later                               |

`[empirical]` — all three from Anthropic's connector documentation, 2026-09-13.

---

## Cost

A `shared-cpu-1x` machine pinned running with a 1 GB volume is a few dollars a month. There
is no free tier that keeps a machine up, and a machine that sleeps does not run Tuesday's
job.

---

## Rolling back

```bash
fly releases
```

```bash
fly deploy --image registry.fly.io/ff-copilot@<digest>
```

The volume is not touched by a rollback. Schema changes are additive and guarded by
`SCHEMA_VERSION` in `backend/src/ff/adapters/store.py`; an older image reading a newer
database will ignore columns it does not know about.
