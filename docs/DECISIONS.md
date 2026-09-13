# Decision log

One entry per decision that a future reader would otherwise re-litigate. Newest first.
Format: date · decision · why · what would change our mind.

---

## 2026-09-13 · Greedy assignment, not ILP, for lineup assembly

Standard roster eligibility is nested (FLEX ⊇ RB∪WR∪TE), which makes feasible lineups the
independent sets of a transversal matroid. Greedy by weight is provably optimal there.
Adding an ILP solver would be a dependency, a build step, and a source of bugs, in exchange
for nothing.

**Changes our mind:** a league rule with a position *limit* ("at most 3 WR"), any non-nested
eligibility constraint, or multi-week bye planning. Any of those breaks the matroid and we
switch to min-cost max-flow.

## 2026-09-13 · SQLite, not Postgres

A season is a few MB. One file, no server, trivial backup, and backtests are a file copy.

**Changes our mind:** the host makes a persistent volume painful, or two processes need to
write concurrently.

## 2026-09-13 · Ensemble projections; a single source is a misconfiguration

A simple average beat individual sources in 63% of head-to-head comparisons. Single-source
config raises a startup error rather than silently degrading.

**Changes our mind:** measured calibration showing one source dominating the blend over a
full season.

## 2026-09-13 · WriteExecutor takes an Approval as a required argument

Safety invariant §5.1 says nothing is written without explicit approval. Enforcing it in the
type signature rather than with a runtime check means there is no code path — including a
future one written in a hurry — that can skip it.

**Changes our mind:** nothing. This one is structural.

## 2026-09-13 · Build the read half first, regardless of Yahoo write access

Yahoo now grants write access only by review. The read half is ~80% of the value and depends
on no approval.

**Changes our mind:** write access is granted early, in which case the order is a convenience
rather than a hedge.
