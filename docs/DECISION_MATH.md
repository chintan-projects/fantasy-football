# Decision math

The full treatment lives in the project skill at
`.claude/skills/fantasy-decision-math/SKILL.md` so Claude loads it automatically when working
on the optimizer, the bid sizer, or any UI copy that states a recommendation.

Read it before changing anything in `backend/src/ff/domain/`.

Every claim there is labelled:

- **[empirical]** — measured, with a source
- **[theory]** — derivable from stated assumptions
- **[folk]** — widely repeated, unvalidated

The four-line summary:

1. Maximize `P(win)`, not expected points. Rank by `E_D[S_X(−D)]`. Floor when favored,
   ceiling when an underdog, expected points when close — which is most weeks.
2. Lineup assembly on a standard roster is a **matroid**, so greedy by projection is provably
   optimal. Do not import an ILP solver. The hard part is statistical, not combinatorial.
3. FAAB is a **budget-constrained first-price common-value auction**. Shade for the number of
   genuinely interested bidders, shade again for the winner's curse, and subtract the option
   value of a reserved dollar — which goes to zero by Week 12.
4. The best weekly projections explain **3–23% of variance.** Everything above is a small
   correction to a mostly-random process. Build it anyway, because it removes error and
   inconsistency. Do not claim more.

## Player ratings: what usage is allowed to do

`ff_compare_players`, the waiver board and the lineup now carry a usage block (snap,
target and carry share, and their trend) next to each projection. What backs it:

- Usage shares are steadier week to week than fantasy points, so a change in share is an
  earlier signal of a role change than a change in points. **[empirical]**, from public
  fantasy research. Not re-measured in this app's data, which has four weeks of one season.
- The role names and their thresholds (25% of targets is a "lead target"; 65% of snaps is a
  "workhorse") are **[folk]**. Readable conventions, not fitted.
- The trend thresholds (5 points of target share, 10 of snap share, last two games against
  the games before) are **[folk]**.

So usage changes no projection, no win probability and no bid. It is the evidence shown
next to them. It is offered as a lean only when the projection gap is already inside the
2-point noise band, and that lean is labelled **[theory]** because it has not been
backtested. When the calibration job has a season of snapshots, test it: among too-close
calls, did the rising-usage player outscore the other more than half the time?

Floor and ceiling are the 10th and 90th percentile of the same zero-inflated Gamma the
simulator draws from. Their width comes mostly from the position prior, so they describe
the spread of outcomes at a position more than one player's personal volatility.
**[theory]**

## Decision log

Decisions that resolve a conflict between principles, or that pick one of these methods over
another, get a dated entry in `DECISIONS.md`.
