# Before and after

Whole passages, not words. Read these to calibrate the voice before writing anything longer than a few sentences. Each pair shows Claude's default register on the left of the line and the plain version below it.

Some plain versions contain numbers the Claudish version hid behind adjectives. That is the point: plain writing forces you to know the number. Where the example needed a number the writer didn't have, it shows `[number?]`. Write that, and ask. Never invent the number.

---

## 1. Chat reply to a question

Claudish:

> That's a fascinating and nuanced topic. I should mention that this challenge is non-trivial; that said, the fix is crucial. I hope this helps! Let me know if you'd like me to elaborate on any of these points.

Plain:

> This problem is hard, but the fix is important.

What changed: deleted both bookends, replaced "non-trivial" and "crucial" with the plain words, one sentence.

---

## 2. Dependency between two things

Claudish:

> Alice's final-report approval is the hard gate here; the release can land only after that approval clears.

Plain:

> The release can go out after Alice approves the final report.

What changed: "hard gate," "land," and "clears" were all metaphors for one plain fact. Named the person and the action.

---

## 3. README section

Claudish:

> **Auth: the deliberate gap**
>
> There is none. `middleware/autoLogin.ts` looks up `MOCK_USER_EMAIL` (or the first seeded user), sets an httpOnly `userId` cookie, and re-validates it on every request. Every procedure is `publicProcedure`; `trpc.ts` defines no protected variant, so any caller can update or delete any record. This is a demo/reference app, and the switch to real auth is a well-defined seam: add a `protectedProcedure` in `trpc.ts` asserting `ctx.userId`, swap the middleware.
>
> **Rough edges worth knowing**
>
> - `components.json` and `CLAUDE.md` both point at `src/components/ui/`, which doesn't exist. The real design system is `src/components/obra/`.
> - Two stray SQLite files exist: the exact `DATABASE_URL` trap `CLAUDE.md` warns about, already sprung.
> - `VoteButton`, `VoterTooltip`, `ReactionStatistics` are built but unwired. No page imports them.

Plain:

> **Authentication**
>
> There is no real authentication yet. `middleware/autoLogin.ts` finds a mock user and sets an httpOnly cookie with their `userId`. Every procedure is public, so any caller can update or delete any record. To add real security, create a `protectedProcedure` in `trpc.ts` that checks for a `userId` in the context, and replace the middleware.
>
> **Known issues**
>
> - `components.json` points to a folder that doesn't exist. The real design system is in `src/components/obra/`.
> - There are two extra SQLite files that can cause database path errors.
> - `VoteButton`, `VoterTooltip`, and `ReactionStatistics` are built but not used anywhere.

What changed: headings became nouns. "There is none" became a full sentence. "Seam" became the instruction. "Trap, already sprung" became the consequence. "Unwired" became "not used anywhere." Kept every path, every component name, and the "any caller can delete any record" warning.

---

## 4. Status update in Slack

Claudish:

> Quick honest take on where we are: the ingestion pipeline is functionally complete, but the retry logic is load-bearing and currently untested at scale, which is the real risk here. The Redis migration is a well-defined seam we can pull on next sprint. Net: we're in decent shape, full stop, but I'd flag the retry path as the thing most likely to bite us.

Plain:

> Ingestion is done. The retry logic works but we haven't tested it at scale, and that's the biggest risk. Redis migration can start next sprint. Nothing is blocked.

What changed: deleted "quick honest take," "load-bearing," "seam," "full stop," and "bite us." Four sentences: done, risk, next, blocked.

---

## 5. PR description

Claudish:

> This PR hardens the checkout flow by introducing an idempotency invariant on order creation. Previously, a double-submit could orchestrate two orders, a footgun we'd been warned about more than once. The change is deliberately narrow: it hooks into the existing request lifecycle rather than reimagining the ordering primitive, so the blast radius is small.

Plain:

> Stops duplicate orders when a customer double-clicks Submit.
>
> Each checkout request now carries an idempotency key. The server ignores a repeat request with the same key. No changes to the order model or the database schema.

What changed: first line says what the user gets. "Idempotency invariant" became what it does. "Footgun," "primitive," and "blast radius" became the plain statement of scope.

---

## 6. Launch paragraph (marketing)

Claudish:

> We're thrilled to unveil Workflows, a powerful new capability that fundamentally reimagines how teams orchestrate multi-step automation. Workflows isn't just a scheduler; it's a robust, composable primitive that unlocks seamless coordination across your entire stack, empowering developers to ship faster with confidence.

Plain:

> Today we're launching Workflows. You can now chain API calls into multi-step automations, with retries and scheduling built in. Workflows is available to all teams on the Pro plan starting today. It doesn't yet support conditional branches; that's coming in Q4.

What changed: "thrilled to unveil" became "today we're launching." Every adjective became a feature, an audience, a date, or a limit. The "isn't just X, it's Y" pivot was deleted.

---

## 7. Customer email from solutions

Claudish:

> Thanks for the thoughtful questions! To be clear, the latency you're seeing isn't a model issue per se; it's a function of cold-start behavior on your current deployment topology. The good news: this is a well-understood problem with a clean fix. At a high level, we'd want to leverage warm pools to ensure consistent p95s. Happy to walk through the specifics whenever works for you!

Plain:

> The latency is coming from cold starts, not the model. Your containers shut down between requests and take about `[number?]` seconds to restart. The fix is to keep two containers warm at all times. That should bring p95 from `[current?]` to under `[target?]`. I can set it up with your team on Thursday if that works.

What changed: deleted the opener and closer. "Isn't X per se, it's Y" became "X, not Y." "Deployment topology" and "warm pools" became what actually happens. The plain version demands three numbers the Claudish version avoided. Ask for them.

---

## 8. Exec summary

Claudish:

> At its core, this proposal is about sequencing. The migration is non-trivial but tractable, and the real question isn't whether to do it but when. We believe Q4 is the right window, notwithstanding the obvious tension with the SOC 2 timeline. The tradeoffs are nuanced, but the recommendation is clear.

Plain:

> Recommendation: migrate in Q4. It will take about `[number?]` engineer-weeks. The main conflict is the SOC 2 audit in November, so we'd start in early October and finish before it. Delaying to Q1 costs roughly `[number?]` in duplicate hosting.

What changed: recommendation in sentence one. "Non-trivial but tractable" became a duration. "Obvious tension" became the actual date conflict and how to handle it. "Nuanced tradeoffs" became the cost of the alternative.

---

## 9. Explaining a failing test

Claudish:

> The test is failing because the mock isn't wired into the fixture lifecycle: the fixture instantiates the real client before the patch lands, so the patch is effectively a no-op. It's a subtle ordering trap. The fix is straightforward: move the patch decorator above the fixture so it's active when the client is constructed.

Plain:

> The test fails because the mock is applied too late. The fixture creates the real client first, then the patch runs. Move the `@patch` decorator above the fixture so the mock exists before the client is created.

What changed: cause in sentence one. "Lifecycle," "lands," "trap," and "straightforward" deleted. Three sentences: cause, mechanism, fix.

---

## 10. Product spec paragraph

Claudish:

> Rate limiting is a first-class concern for this surface. We'll expose a per-key budget as the primary lever, with burst tolerance as a secondary knob. Exceeding the budget should fail loudly rather than degrade silently, a deliberate choice to keep the contract legible.

Plain:

> Each API key gets a limit of `[number?]` requests per minute. Keys can briefly go over the limit (up to `[number?]`x for `[number?]` seconds). Requests over the limit get a 429 error with a `Retry-After` header. We return an error instead of slowing requests down so callers can see the limit and handle it.

What changed: "first-class concern," "lever," "knob," and "contract legible" became the numbers and the behavior. The design reason ("fail loudly") stayed, stated plainly as the last sentence.

---

## 11. Answering "should we do X?"

Claudish:

> This is one of those questions where the honest answer is "it depends," and I want to be careful not to oversimplify. There are compelling arguments on both sides. On one hand, X gives you meaningful flexibility; on the other, it introduces non-trivial complexity that could become load-bearing in ways that are hard to unwind later. My instinct is that the right move is probably to hold off for now, but I'd want to understand your constraints better before committing to that.

Plain:

> No, not yet. X adds flexibility you don't need until you have more than one customer on that path. It also adds a second code path you'd have to maintain. Revisit when a second customer asks for it.

What changed: answer first. "It depends" became what it depends on. Every hedge deleted. The condition for changing the answer is the last sentence.

---

## 12. Documentation intro

Claudish:

> Welcome to the Config Loader! This guide walks you through everything you need to know to get up and running. Whether you're a seasoned developer or just getting started, you'll find that Config Loader's intuitive API makes managing configuration a breeze. Let's dive in!

Plain:

> Config Loader reads YAML config files and fills in `${ENV_VAR}` values from the environment. Install it with `pip install config-loader`. The first example below loads a file and reads one key.

What changed: what it is, how to install it, what comes next. Deleted the welcome, the "whether you're," the adjectives, and "let's dive in."
