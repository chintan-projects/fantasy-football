# The web frontend is no longer the product

As of 2026-09-13 this app's front door is an MCP server. Claude connects to it and answers
"who should I start?" in the place the owner already is — phone, desktop, wherever Claude
already is — rather than at a URL that has to be remembered and visited.

That is not a technology preference. The PRD names one failure mode above all others:
**"I stop opening it."** A screen that has to be visited on a Sunday morning competes with
the Yahoo app for the same two minutes and loses. A screen that does not have to be visited
does not.

## This code is kept, not deleted

Two reasons, both concrete:

1. **The generated OpenAPI client and the shared types still describe the same domain.** If
   a view of history or a calibration chart is ever wanted, this is the starting point, and
   nothing here is wrong — only unvisited.
2. **A judgment that reverses is a judgment worth being able to reverse cheaply.** Deleting
   it would be a bet that the connector approach works, made before it has been used for a
   single week. `docs/DECISIONS.md` records what would change our mind.

## What this means in practice

- It is **not** built, tested or deployed by default. The Docker image does not include it
  and `fly.toml` does not serve it.
- `make dev` still runs it against the FastAPI app in `backend/src/ff/api/app.py` if you
  want to look at it.
- It is **not** kept in step with new work. Expect it to describe an older shape of the
  recommendation payload. Treat it as an artifact, not as documentation.

The thing to read instead is `docs/DEPLOY.md`, then `backend/src/ff/api/mcp.py` — the nine
tools and their descriptions are the user interface now.
