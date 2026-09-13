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

## Decision log

Decisions that resolve a conflict between principles, or that pick one of these methods over
another, get a dated entry in `DECISIONS.md`.
