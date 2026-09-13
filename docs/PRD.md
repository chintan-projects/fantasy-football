# PRD — Fantasy Football Copilot

One user. One league. Two decisions a week.

## The job

Sunday morning I want to know who to start and why, in under two minutes. Tuesday night I
want to know what to bid on whom out of my $100 FAAB, and why, and then approve it.

Today that means opening four tabs, reading three articles that disagree, and guessing. The
guessing is not the problem — the *inconsistency* is. I make a careful decision one week and
a lazy one the next, and I cannot tell afterwards which kind of week it was.

**Success:** I stop opening the Yahoo app to decide. Two minutes, one screen, click approve.
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

## The two screens

### `/lineup`

```
Week 3  ·  vs. Team Rocket  ·  projected 118.4 – 112.1  ·  win probability 54%  (55–70% band)

  ✓ Lineup is already optimal.            [ or ]   2 changes recommended  [ Review ]

  Contested calls
  ──────────────────────────────────────────────────────────────────────
  FLEX    Chase Brown        14.2  ±5.1     →  start      +2.1% win prob
          Rome Odunze        13.8  ±6.4         bench     upside play; you are favored
                                                          so the floor is worth more
  WR2     Jaylen Waddle      11.9  ±4.8     too close to call — 0.4 pt gap is inside
          Khalil Shakir      11.5  ±4.6     the noise. Either is fine. Keeping current.
  ──────────────────────────────────────────────────────────────────────
  Sources: ESPN ✓  FantasyPros ✓  nflverse injuries ✓  ESPN odds ⚠ 4h stale
```

The "too close to call" row is a feature, not a failure. It is the most common honest answer.

### `/waivers`

```
FAAB  $73 of $100 remaining  ·  Week 3  ·  10 bid periods left  ·  $7/period at par

  Target            Pos  RoS VORP   Interested   Reservation   Bid    Why
  ──────────────────────────────────────────────────────────────────────────
  Tyjae Spears      RB    +3.8/wk   ~4 of 12     $31           $19    Role change is real
                                                                     (snap share 41→68%).
                                                                     Shaded for 4 bidders
                                                                     and common-value curse.
  Jalen McMillan    WR    +1.1/wk   ~2 of 12     $6            $3     Marginal. Fine to lose.
  ──────────────────────────────────────────────────────────────────────────
  If both land you have $51 for 10 periods. Budget is not the constraint; conviction is.
  Reserve $15–20 for a late-season RB1 injury.
```

## Non-negotiable product rules

These come from `CLAUDE.md` §3 and are product requirements, not engineering preferences.

1. **No recommendation without a reason and a confidence.**
2. **"Too close to call" is a valid, common output.** A gap under ~2 points is noise. The app
   must not manufacture a winner to look decisive.
3. **Every number carries an uncertainty band.** A bare "54% win probability" is a lie; "54%,
   band 55–70%" is the truth.
4. **No folk heuristic presented as a finding.** If the evidence is "pundits say so," the UI
   says "widely believed, untested."
5. **Nothing is written to Yahoo without an explicit approval.**

## What would make this fail

Ranked by likelihood, honestly:

1. **I stop opening it.** Most likely failure by a wide margin. Mitigation: the weekly email
   contains the actual recommendation, not a link to go read one.
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
| Am I using it? | Weeks where a recommendation was viewed before kickoff |
| Do I trust it? | Approval rate on recommended changes |
| Is it right? | Projection MAE per source; recommendation hit rate vs. the lineup I had set |
| Did it pay? | Simulated season record following every recommendation vs. actual |
| Is FAAB working? | Realized points-over-replacement per FAAB dollar spent |

The last two take a full season to answer. Build the logging for them in week one, because
they cannot be reconstructed later.
