# PRD — Fantasy Football Copilot

One user. One league. Two decisions a week.

## The job

Sunday morning I want to know who to start and why, in under two minutes. Tuesday night I
want to know what to bid on whom out of my $100 FAAB, and why, and then approve it.

Today that means opening four tabs, reading three articles that disagree, and guessing. The
guessing is not the problem — the *inconsistency* is. I make a careful decision one week and
a lazy one the next, and I cannot tell afterwards which kind of week it was.

**Success:** I stop opening the Yahoo app to decide. Two minutes, one question, say yes.
**Second success, and the one that actually matters:** at the end of the season the app can
tell me whether its recommendations beat what I would have done. If it cannot, it did not work.

## Users

Me. That is the whole list. Design accordingly: no accounts, no onboarding, no multi-tenancy,
no settings page for things I will set once.

## Scope

### V1 — the read half (ships first, depends on no Yahoo approval)

1. **Lineup recommendation.** Current roster, projections blended across sources, per-player
   distributions, Monte Carlo against my actual opponent's actual lineup. Output: the
   recommended lineup, the win probability, and the delta versus what is currently set.
2. **Explain every call.** For each contested slot: the two candidates, their means and
   spreads, which source said what, the win-prob difference, and **"too close to call" when
   the gap is inside the noise.**
3. **Waiver board.** Available players ranked by rest-of-season VORP against my current
   replacement at that slot, with the trending-add demand signal normalized by availability.
4. **FAAB bid recommendation.** For each target: a reservation value, the estimated number of
   genuinely interested bidders, the winner's-curse and option-value shading, and one number
   to bid. Plus what it does to my remaining budget curve.
5. **Health and staleness.** Which sources answered, which were stale, what that does to
   confidence.

### V1.5 — the write half (gated on Yahoo approval)

6. **Approve and submit.** One button per recommendation. Server-side budget check
   immediately before submission. Journal everything.
7. **Assisted fallback.** If writes are refused, a copy-paste move list plus deep links.

### V2 — the part that makes it trustworthy

8. **Calibration dashboard.** Projection MAE per source per week. Recommendation hit rate.
   "If I had followed every recommendation, my record would be X." **This is not a nice-to-
   have.** Without it there is no evidence the app helps, and an unevaluated recommender is
   indistinguishable from a horoscope.
9. **Backtest harness.** Replay past weeks against alternative strategies.

### Explicitly out of scope

Trades. Draft assistance. Multiple leagues. DFS. League-mate-facing anything. Chat. Push
notifications beyond one weekly email. Mobile app — the web page works on a phone.

## The two conversations

Changed 2026-09-13. This used to specify two screens. It now specifies two conversations,
for the reason written at the top of "What would make this fail": the failure mode ranked
first is "I stop opening it", and a screen has to be opened. See `docs/DECISIONS.md`.

The surface is a set of MCP tools. What the owner types is ordinary English; what Claude
gets back is a computed decision it is told to report rather than re-derive.

### Sunday morning

> **me:** who should I start this week?

`ff_recommend_lineup` runs the simulation and returns the plan. A real answer, taken from
`make mcp-demo` against the recorded fixtures:

```
Week 2  ·  win probability 69.6%  (band 57.8 – 81.5)  ·  projected 112.6

  Starters   QB  Jayden Daniels 19.4   RB  Bijan Robinson 12.7   RB  Tyrone Tracy 9.7
             WR  Jordan Addison 16.0   WR  Malik Nabers 15.1   TE  Brock Bowers 5.5
             W/R/T  Rome Odunze 14.6   K  Jake Bates 9.6   DEF  Denver 10.0

  Contested calls
  ─────────────────────────────────────────────────────────────────────────
  RB      start Tyrone Tracy over Jahmyr Gibbs
          higher projection (9.7 vs 8.7); worth 1.9% win probability     [lean]
  TE      start Brock Bowers over Trey McBride
          you are favored, so the safer floor is worth more than the
          upside; worth 0.7% win probability                             [lean]
  W/R/T   start Rome Odunze over Amon-Ra St. Brown
          higher projection (14.6 vs 13.2); worth 1.1% win probability   [lean]
  ─────────────────────────────────────────────────────────────────────────
  Sources: fixture-a ✓ 60s  fixture-b ✓ 60s
  Weekly projections explain roughly 3-23% of the variance in actual points.
```

A slot the simulation cannot separate comes back as `too_close_to_call` with the gap and the
reason. That is the most common honest answer and it is a feature. A slot that was never
simulated is **left out entirely** rather than reported as a dead heat — reporting it as a
tie is a claim, and it is the claim most likely to be wrong (BUG-008).

### Tuesday night

> **me:** anything worth picking up?

`ff_waiver_board` ranks the pool by rest-of-season value over the owner's *own* current
replacement, naming the player each add would displace. Then:

> **me:** what should I bid on Tyjae Spears?

`ff_recommend_bid` returns one number with the whole shading breakdown: the equilibrium
`(n-1)/n` term, the winner's-curse term, the option value of a dollar held back, the bidder
count, and a sentence saying how that count was derived and which way it errs. The remaining
budget is read live from Yahoo on every call.

> **me:** ok, claim him

`ff_propose_claim` returns the plan and an approval id and submits nothing. Claude shows the
plan. Only after a yes does `ff_confirm` spend the approval. No single tool does both — see
"Non-negotiable product rules" below and `CLAUDE.md` §5.

### What is still a screen

Nothing, today. The calibration view in V2 is the honest candidate: a table of projected
versus actual across a season is worse in prose than in pixels. `frontend/` is kept, not
deleted, for that reason.

## Non-negotiable product rules

These come from `CLAUDE.md` §3 and are product requirements, not engineering preferences.

1. **No recommendation without a reason and a confidence.**
2. **"Too close to call" is a valid, common output.** A gap under ~2 points is noise. The app
   must not manufacture a winner to look decisive.
3. **Every number carries an uncertainty band.** A bare "54% win probability" is a lie; "54%,
   band 55–70%" is the truth.
4. **No folk heuristic presented as a finding.** If the evidence is "pundits say so," the UI
   says "widely believed, untested."
5. **Nothing is written to Yahoo without an explicit approval.** In tool terms: no single
   tool both prices a bid and submits it. `ff_propose_claim` returns a plan and an approval
   id; `ff_confirm` spends it; the approval is single use, bound to one payload and week,
   and expires in six hours.
6. **The tools report, they do not re-derive.** A recommendation restated more confidently
   than the tool stated it is a misquote, and the server's instructions say so.

## What would make this fail

Ranked by likelihood, honestly:

1. **I stop opening it.** Most likely failure by a wide margin, and the reason the product
   moved to an MCP server on 2026-09-13: there is now nothing to open. The question gets
   asked wherever Claude already is. That is a mitigation, not a cure — a tool that goes
   uncalled for a month fails exactly as a tab that goes unvisited does, and the honest
   read of that is in `docs/DECISIONS.md` under "what would change our mind".
2. **I do not trust it, because it sounds more certain than it is.** Mitigation: rules 1–4
   above. Overclaiming is the fastest way to lose a user who knows the domain.
3. **Yahoo never grants write access**, so it stays a read-only advisor. Mitigation: the read
   half is ~80% of the value and the assisted path covers the rest. Annoying, not fatal.
4. **ESPN silently changes an endpoint mid-season** and projections go stale without anyone
   noticing. Mitigation: schema validation on every fetch, staleness surfaced in the UI,
   `required` vs `optional` per source.
5. **It works and it does not matter.** The real ceiling on start/sit is 1–3 points of win
   probability a week. The app should say so rather than pretend otherwise — and then earn
   its keep on consistency and on FAAB, where the stakes are larger.

## Measuring it

| Question | Measure |
|---|---|
| Am I using it? | Weeks where a recommendation was computed before kickoff (the tools log every call) |
| Do I trust it? | Approval rate on recommended changes |
| Is it right? | Projection MAE per source; recommendation hit rate vs. the lineup I had set |
| Did it pay? | Simulated season record following every recommendation vs. actual |
| Is FAAB working? | Realized points-over-replacement per FAAB dollar spent |

The last two take a full season to answer. Build the logging for them in week one, because
they cannot be reconstructed later.
