---
description: Analyze this week's waiver targets and size the bids
---

Work the waiver wire for this week.

1. Pull free agents and waivers, plus Sleeper trending adds. **Normalize trending adds by
   availability** — raw counts under-rank already-popular breakouts.
2. For each plausible target, estimate rest-of-season points over my *current* replacement at
   that slot. Say how confident you are; for a two-game breakout the honest answer is "barely
   any signal."
3. Estimate `n` = managers who actually want him: the right roster hole, bench space, and
   budget. **Not league size.** Read the rosters. This is the highest-value input.
4. Run `domain.faab.recommend_bid` and show the whole breakdown: reservation value,
   competition shading, winner's curse shading, option value.
5. Report remaining budget, bid periods left, and whether I am on track to spend it all.
   Leftover FAAB at season end is a strict error.
6. Flag anything where the recommendation disagrees with popular advice, and say why.
