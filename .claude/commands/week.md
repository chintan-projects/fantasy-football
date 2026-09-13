---
description: Run this week's recommendation end to end and report what it says
---

Produce this week's recommendation and show your work.

1. Get the current week from Sleeper's `/state/nfl`. Never compute it from a date.
2. Fetch the roster, matchup, projections and injury data. Report which sources answered and
   which were stale.
3. Run `services.recommend`.
4. Report, in plain English:
   - the recommended lineup and the win probability **with its band**
   - each contested call, the reason, and the win-probability delta
   - every call that is **too close to call** — do not manufacture a winner
   - what was missing and what that does to confidence
5. Do not write anything to Yahoo. Recommendation only.
