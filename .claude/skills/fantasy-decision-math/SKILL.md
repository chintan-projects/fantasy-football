---
name: fantasy-decision-math
description: "The decision math for season-long fantasy football — win-probability-maximizing start/sit instead of expected points, why lineup assembly is a matroid and greedy is provably optimal, Monte Carlo over correlated distributions, and FAAB bidding as a budget-constrained first-price common-value auction. Use before writing or reviewing any optimizer, projection blend, bid sizer, or UI copy that states a recommendation. Every claim is labelled empirical, theory, or folk."
triggers:
  - "start sit"
  - "lineup optimizer"
  - "faab"
  - "bid"
  - "win probability"
  - "projection"
  - "waiver"
---

# Fantasy decision math

Claims are labelled **[empirical]** (measured), **[theory]** (derivable), **[folk]**
(widely repeated, unvalidated). Never present folk as empirical.

## 0. The fact that governs everything

**[empirical]** Fantasy Football Analytics, 11 seasons of projections (Sept 2026): the best
weekly projections explain only **~3% to ~23% of variance** in actual points. RB is the most
predictable (~23% R²); **QB, WR and TE are often single digits.** MAE: TE ~3.8, WR ~4.9,
RB ~5.1, QB ~6.2.

**[empirical]** A simple average of all sources beat individual sources in **63%** of
head-to-head comparisons.

Three consequences, and they are the whole design:

1. **Ensemble the projections.** Free, and the highest-ROI modeling decision available.
   Never hand-pick a source.
2. **A projection gap under ~2 points is noise.** With MAE ~5, most start/sit "decisions"
   are coin flips. The UI must be able to say "too close to call."
3. **The remaining edge is in the distribution, not the point estimate** — not because
   distributions predict better (they do not) but because the objective is non-linear and
   the distribution is what the non-linearity acts on.

## 1. Start/sit: maximize P(win), not points

**[theory]** Let `X` = the contested player's points, `D` = (your other starters) −
(opponent total). You win iff `X + D > 0`:

```
P(win) = E_D[ S_X(−D) ]        S_X = survival function of X
```

Rank by `E_D[S_X(−D)]`, not by `E[X]`. This one expression generates every correct piece of
folk wisdom and kills the wrong ones:

- **Heavy favorite** — D sits well above 0, so you evaluate `S_X` at strongly negative
  thresholds where nearly anyone clears. Only the left tail separates candidates.
  **Take the floor. Minimize bust probability.**
- **Heavy underdog** — you evaluate far out in the right tail. Only ceiling matters.
  **Take the higher-variance player even at a materially lower mean.**
- **Close matchup** — D's density is roughly flat near 0, so `E_D[S_X(−D)]` is approximately
  affine in `E[X]`. **Maximizing expected points IS maximizing win probability.**

**So expected points is correct in the middle and wrong only at the tails** — right maybe
85–90% of weeks. Variance-chasing is a tail correction, not a strategy. Applying it every
week makes you worse.

### Size the effect honestly

**[theory + empirical]** A full PPR lineup total has SD ~25–35; the margin `D` is a
difference of two such totals, SD ~35–45. A single-slot swap moves your mean ~2 and your SD
~3, which moves P(win) by **1–3 percentage points**. A well-chosen tail-case variance play
rarely exceeds ~5.

**Start/sit optimization is real and it is small.** Over 14 weeks, playing it perfectly is
worth a fraction of a win. Waiver acquisition and draft-day construction dominate it by an
order of magnitude. Allocate effort — and UI claims — accordingly.

### Getting distributions

**[theory]** Fit per-player distributions, not point estimates. Gamma (non-negative,
right-skewed, two parameters) is the standard choice — Nathan Braun's Fantasy Math fits
Gamma to historical ECR means and SDs. Gamma mishandles zero-inflation from injury exits
and true goose-eggs; a mixture `P(zero)·δ₀ + (1−P(zero))·Gamma` fits reality better and is
easy to fit.

**Two kinds of uncertainty, and only one is what "ceiling/floor" means:**

- *Epistemic* — we do not know his role. Proxy: FantasyPros ECR **std-dev across 130+
  experts**. Cheap and available.
- *Aleatoric* — TDs are Poisson-ish, week to week. This is boom/bust. Estimate from the
  player's own game log plus a position-level prior.

**[empirical]** FFA says this outright about its own uncertainty metric: *"This metric does
not account for the variance in production on a game-to-game basis."* Expert disagreement
is **not** volatility. Do not substitute one for the other. Shrink hard — 16 games gives a
variance estimate with roughly ±20% precision.

### Correlation

**[theory]** Correlation does not change your lineup's mean. It changes its **variance**.
QB–WR1 same-team runs ~+0.3 to +0.5.

- **Stack when you are an underdog** (inflate variance), **de-stack when favored**.
- **Check whether you and your opponent both have players in the same game.** Your RB and
  their WR are *negatively* correlated through game script, which *narrows* the margin
  distribution — good when favored, bad when an underdog. Essentially no consumer tool
  models this. It is free edge if you are already simulating.

**Honest caveat:** this matters far less in season-long than DFS, because you can only
correlate what you already roster. A handful of weeks per season, ~1–2 points of win
probability. Real, tiny.

### Vegas and weather

**[empirical]** Implied team total = `total/2 − spread/2`. The betting market aggregates
injury, weather and role information faster and more accurately than any projection source.
Use it as a prior on game environment; prefer it to your own game-script guesses.

**[empirical]** Weather is overrated. Brian Burke: wind below 15 mph has minimal effect on
passing. Fantasy Life, 2018–22: **only 48 of 1,311 games (3.7%) had winds over 20 mph**; at
20+ mph PROE drops 2.8pp, CPOE 1.6pp, total plays 62.6 → 60.9. Conclusion: *"good passers
stayed good."*

**So: gate on `roof`, check wind, and if it is under 20 mph do nothing.** And note — if you
are already using implied team totals, a manual weather adjustment **double-counts**, since
the market has priced it in.

## 2. Lineup assembly: it is a sort, not an optimizer

**[theory]** Standard roster (1QB/2RB/2WR/1TE/1FLEX/1K/1DST) is a max-weight bipartite
matching. The general solution is Hungarian O(n³) or ILP. **You do not need either.**

Eligibility sets are **nested/laminar** — FLEX ⊇ (RB ∪ WR ∪ TE), everything else is a single
position. That makes feasible lineups the independent sets of a **transversal matroid**, and
on a matroid **greedy by weight is optimal**. Fill each dedicated slot with your best
eligible player, then put the best remaining RB/WR/TE in FLEX. That is the optimum.

**For expected-points maximization in a standard league, the optimizer everyone sells you is
a sort.** Superflex does not break this either — QB in flex is still nested.

Greedy genuinely breaks when: position *limits* exist ("at most 3 WR"); a **salary cap**
(DFS — multi-dimensional knapsack, NP-hard, ILP is correct); multi-week bye planning;
or any non-nested eligibility ("one of these two must start."). None apply here today.

### Where simulation actually earns its place

**[theory]** Greedy/Hungarian/ILP all optimize a **linear, separable** objective `Σ E[Xᵢ]`.
The real objective `P(Σ Xᵢ > Y)` is **neither linear nor separable** — it depends on the
joint distribution. **No matching algorithm can optimize it.**

The correct method is Monte Carlo over correlated distributions. But note where the
difficulty is: **the combinatorics are trivial and the statistics are everything.** In a
real week only 1–2 slots are contested, so there are 3–8 candidate lineups. **Enumerate them
exhaustively and simulate each.** Reaching for an ILP here is a category error.

**[empirical]** Hunter, Vielma & Zaman, *Picking Winners in DFS Using Integer Programming*
(arXiv:1604.01455) — the transferable idea is that **variance enters as a constraint, not
the objective**: "maximize E[points] subject to SD ≥ v." That turns an intractable noisy
objective into something solvable, and the knob `v` maps directly onto favorite/underdog.
Their top-heavy payoff ≈ your underdog case; double-up ≈ your favorite case.

## 3. FAAB with $100

### The evidence base is bad — say so

**[folk]** There is **no peer-reviewed literature on FAAB bidding.** The public corpus is
folk wisdom with a data veneer. 4for4's per-position budget percentages derive from an
analysis of *what pundits recommended*, not what won — circular. FantasyPros' "spend 30–40%
early, FAAB depreciates like a new car" is anecdotal with zero supporting data.

### The one real dataset

**[empirical]** FTN's 2024 FAAB Primer, NFFC Primetime ($1,000 budgets):

- **Week 2A (Wednesday) was the highest-spend period of the season: $91/manager** — ~9% of
  budget in one bid period. Spending is heavily front-loaded.
- 28 bid periods over 14 weeks → $71/week average on $1,000, i.e. **~$7/week on $100.**
- They graded **35 players bought for $100+ (≥10% of budget) in 10+ leagues.** Roughly
  **10–15% were good acquisitions.** The rest were failures — Joshua Palmer $494, Emari
  Demercado $427, Zach Evans $366, all graded F.

**An 85–90% failure rate on big FAAB spends is the most important number in this section.**
The aggregate market systematically overpays — exactly what auction theory predicts.

### Three layers of theory

**[theory] Layer 1 — private-value bid shading.** First-price sealed-bid, n risk-neutral
bidders, i.i.d. values ~U[0,1]: the symmetric BNE is `b(v) = ((n−1)/n)·v` (Vickrey 1961;
Riley & Samuelson 1981).

**The near-universal error: n is not your league size.** n is the number of managers who
*actually want this player* — right roster hole, bench space, and budget. If 3 of 12 teams
need an RB2, shade to **67%**, not 11/12 ≈ 92%. Reading the league's rosters to estimate n
is worth more than any bid-percentage table ever published.

**[theory] Layer 2 — the winner's curse. This is the big one and almost nobody applies it.**
FAAB values are **common, not private** — everyone read the same beat report and saw the same
snap count. With common values and noisy signals, *conditional on winning you held the
highest of n noisy estimates*, which is biased upward. **Shade further, and shade more as n
grows.** This predicts FTN's 85% bust rate exactly. If your process is "value the player,
then bid near that value," you are structurally overpaying.

**[theory] Layer 3 — the budget constraint.** FAAB is not one auction. It is a **sequential
multi-unit auction under a hard budget** — a dynamic program. The right object is the
**shadow price of a FAAB dollar**, `λ_t` = expected surplus forgone on all future
acquisitions:

```
bid_t = value − winner's_curse_correction − λ_t × dollars_committed
```

`λ_t` declines monotonically; by Week 13 `λ ≈ 0`, so **bid full value late.** **Leftover
FAAB at season end is pure waste — treat it as a strict error.**

Counterintuitive implication: **shading should be heaviest early**, when option value is
greatest — contradicting the popular "spend 30–40% early." The offsetting force is that an
early add accrues more weeks of production and the supply of league-winners is genuinely
front-loaded (Weeks 2–5, when injuries and role changes create surplus). These roughly
cancel. **Net: FTN's front-loaded spending curve is approximately rational; the individual
bid sizes at the peak are not.** The market gets timing right and pricing wrong.

### "0 or overbid" — steelmanned then corrected

**[theory]** The defensible core: **a losing bid costs you nothing.** You pay only when you
win. So "probing low to see what he goes for" is incoherent — you learn the price either
way, and a low bid on a player you want forfeits him for free. The only cost of a high bid
is the *conditional* cost of winning at that price.

**Correct rule: bid your reservation value net of curse and option-value shading, once.
Never bid an amount you would regret either winning OR losing at.**

That single number *looks* bimodal across a season (many $0–2, a few $25+) because
reservation values are bimodal — most waiver players are near-replacement. **The bimodality
is an output, not a strategy.** People who adopt "0 or overbid" as a *rule* overbid on tier-2
players, which is precisely the FTN failure mode.

**[folk]** Odd-dollar tiebreaks ("bid $37 not $35") are real only where ties break by waiver
priority. Check the league rules. Costs $2, occasionally wins a player. Not strategy.

### VORP sizing — the right frame with the worst inputs

Convert to a common currency. Rest-of-season VORP = Σ over remaining weeks of (his projected
points − your current replacement at that slot). Then:

```
Δwin% per week ≈ Φ(ΔVORP_weekly / σ_margin) − 0.5      σ_margin ≈ 35–45
```

Worked: +4.0 pts/week over replacement → `Φ(4/40) − 0.5 ≈ +4.0pp` per week → over 12 weeks
~+0.5 expected wins, worth roughly 8–12pp of playoff probability in a 12-team league.

**And here is the honesty:** the input — "+4.0 pts/week for 12 weeks" — is exactly the
quantity you cannot estimate. Weekly projections explain 3–23% of variance *for established
players with known roles*. For a breakout with a two-game sample and an unclear role you
have essentially no signal. **The VORP estimate for the player you most want to bid on is
the least reliable number in the entire model.** The framework is right; the inputs are
near-noise. That is the whole story of FAAB.

### Operating rules for $100

1. Estimate **n = interested bidders**, not league size. Read rosters. Highest-value five
   minutes of the week.
2. Shade hard for the winner's curse. **Being the second-highest bidder on a bust is a win.**
3. **Never let budget expire.** λ → 0 by Week 12. Spend it.
4. Bid the reservation value once. Do not probe.
5. Reserve ~$15–20 against a Week 10–12 injury to your own RB1 — the one case where a
   dollar's late shadow price stays high, because replacement level can collapse without
   warning.
6. Treat every published bid-percentage table as a prior with enormous error bars,
   including the ones quoted here.

## 4. Genuinely unknowable — do not pretend otherwise

- **Player weekly variance is barely estimable.** ~16 games gives ±20% precision, and
  mid-season role changes break stationarity. Every ceiling/floor number is a shrunk guess.
- **Pairwise correlations are not estimable per player.** Use position/archetype priors
  (QB–WR1 ≈ +0.35, QB–TE ≈ +0.25, RB–opposing-RB ≈ −0.2) and stop. A precise correlation for
  a specific pair is fitted noise.
- **The ceiling on weekly projection accuracy is ~23% R², for RBs.** Single digits elsewhere.
  No data source fixes this. More data improves calibration, not accuracy.
- **Breakout valuation — the exact input FAAB needs most — has essentially no signal.** Two
  games of usage and a coach quote. FTN's 10–15% hit rate is not managers being dumb.
- **Practice participation → performance is unquantified** in any free dataset. You get
  Full/Limited/DNP labels; the in-season snap-level data that would calibrate them publishes
  post-season only. "Friday DNP is bad" is folk.
- **Your opponent's players are exactly as uncertain as yours.** Errors compound across both
  sides of the margin. A "63% win probability" output should be read as "somewhere in 55–70%."

**Meta-point: the analytical ceiling in season-long fantasy is low, and most of it sits in
acquisition rather than weekly lineup setting.** Build the pipeline because it is cheap and
removes error, not because it will make you dominant. Say that in the product, too.

## Sources

FFA 11-season projection analysis (2026) · Hunter/Vielma/Zaman arXiv:1604.01455 ·
Becker & Sun JQAS 12(1) 2016 · Nathan Braun, Fantasy Math · FTN 2024 FAAB Primer (NFFC) ·
4for4 FAAB guide · Advanced Football Analytics (Burke), weather effects on passing ·
Fantasy Life, does wind matter · Vickrey (1961) · Riley & Samuelson (1981)
