"""Was it right? The math for answering that, with no I/O.

CLAUDE.md 2.5 calls this the single most important observability requirement in the
project, and the reason is in section 3: this is a prediction product built on weak
predictions. A tool that cannot be scored cannot be trusted, and an untrusted
recommendation does not get used.

Two things get scored here and they answer different questions.

**Source accuracy** -- ``score_sources`` -- asks which forecaster to believe. Its unit is
one player-week, it has hundreds of them, and the honest form of the answer is a paired
comparison with an error bar. Weekly MAE is about 5 points against means in the low teens,
so two sources a quarter-point apart are indistinguishable and saying otherwise is the
overclaiming this project exists to avoid.

**Decision quality** -- ``score_decisions`` -- asks whether the individual calls were
right: when it said start Bowers over McBride, who scored more? This is the question an
owner actually asks, and the one that was unanswerable until contested slots were persisted
with player ids. Slots the model declined to call are counted separately and never graded.

**Lineup quality** -- ``score_lineup`` -- asks whether the advice was worth taking. Its
unit is one week, so a season yields seventeen numbers and no amount of arithmetic will
make that a significance test. It is reported as a record, not as a p-value. The benchmark
is deliberately not "the perfect lineup": hindsight-optimal is unreachable by anyone and
comparing against it makes every human look incompetent. The benchmark that means
something is the highest-projection lineup, because that is what the owner would have
started without this app.

Nothing here reads a clock, a file or a network. Errors are paired by construction: every
comparison uses the same players, so a week where everyone underperformed moves both
sides and cancels.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean, stdev

from ff.domain.models import PlayerId, Slot

#: Below this many paired observations, a difference in MAE is not reported as a finding.
#: With per-player-week error around 5 points, thirty pairs leaves a standard error near
#: one point -- larger than most of the gaps between sources. `[theory]`
MINIMUM_PAIRS = 30

#: How many standard errors a difference must clear to be called a difference. Two is the
#: conventional threshold and it is stated here so it can be argued with.
SIGNIFICANCE_SIGMAS = 2.0


@dataclass(frozen=True, slots=True)
class SourceScore:
    """One forecaster's accuracy over some set of player-weeks."""

    source: str
    n: int
    mae: float
    bias: float
    """Mean signed error, projection minus actual. Positive means it runs hot.

    Separated from MAE because they have different fixes. A source with low MAE and high
    bias is well-ranked and badly scaled, and subtracting a constant repairs it. A source
    with high MAE and no bias is just noisy, and nothing repairs that.
    """


@dataclass(frozen=True, slots=True)
class Comparison:
    """Whether two forecasters can actually be told apart on the evidence available."""

    better: str
    worse: str
    mae_difference: float
    standard_error: float
    n: int

    @property
    def separated(self) -> bool:
        if self.n < MINIMUM_PAIRS:
            return False
        if self.standard_error == 0.0:
            # Every pair differed by exactly the same amount. That is the *most* separated
            # evidence can be, not the least -- there is no spread for the ordering to
            # flip within. A constant gap of zero is still no gap.
            return self.mae_difference > 0.0
        return self.mae_difference > SIGNIFICANCE_SIGMAS * self.standard_error

    @property
    def verdict(self) -> str:
        if self.n < MINIMUM_PAIRS:
            return (
                f"Too few weeks to say. {self.n} paired forecasts; {MINIMUM_PAIRS} is the "
                f"minimum before a gap this size means anything."
            )
        if self.mae_difference == 0.0:
            return f"Identical over {self.n} forecasts. Nothing separates them."
        if not self.separated:
            return (
                f"Too close to call. {self.better} is ahead by {self.mae_difference:.2f} "
                f"points of MAE, and the error bar on that gap is "
                f"{self.standard_error:.2f}, so the ordering could flip next week."
            )
        return (
            f"{self.better} beats {self.worse} by {self.mae_difference:.2f} points of MAE "
            f"over {self.n} forecasts (standard error {self.standard_error:.2f})."
        )


@dataclass(frozen=True, slots=True)
class LineupScore:
    """One week of hindsight on one lineup.

    ``recommended`` and ``baseline`` are actual points scored. ``baseline`` is the lineup
    the owner would have set by starting the highest projections, which is the decision
    this app has to beat to be worth opening.
    """

    week: int
    recommended: float
    baseline: float
    best_possible: float
    scored_players: int
    missing_players: int

    @property
    def edge(self) -> float:
        """Points gained over just starting the highest projections. Often negative."""
        return self.recommended - self.baseline

    @property
    def left_on_bench(self) -> float:
        return self.best_possible - self.recommended


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """The record on individual start/sit calls -- the question the owner actually asks.

    ``declined`` counts the slots the model refused to call. Those are deliberately not
    graded and not counted against anything. CLAUDE.md section 3 makes "too close to call"
    a real output, and a real output cannot also be scored as a prediction: grading
    coin-flips would fill the record with noise in whichever direction the coins landed,
    and would quietly punish the model for being honest.
    """

    right: int
    wrong: int
    tied: int
    declined: int
    points_gained: float
    """Actual points of the starters chosen minus the alternatives, over graded calls.

    This is the headline, not the record. A 6-4 record worth +2 points is a rounding
    error; a 4-6 record worth +30 is a good season with unlucky small calls. Win-loss
    hides both.
    """

    @property
    def graded(self) -> int:
        return self.right + self.wrong + self.tied

    @property
    def verdict(self) -> str:
        if self.graded == 0:
            return "No start/sit calls have been graded yet" + (
                f"; {self.declined} were too close to call." if self.declined else "."
            )
        return (
            f"{self.right}-{self.wrong}"
            + (f"-{self.tied}" if self.tied else "")
            + f" on calls it was willing to make, worth {self.points_gained:+.1f} points. "
            + f"{self.declined} more were too close to call and are not graded."
        )


def score_decisions(
    decisions: list[tuple[PlayerId, PlayerId | None, bool]],
    actual: dict[PlayerId, float],
) -> DecisionRecord:
    """Grade contested-slot calls. Each entry is (started, benched, was_confident).

    A call is skipped, not counted wrong, when there was no alternative or when either
    player has no recorded score. A benched player with no row did not play, which makes
    the comparison meaningless rather than favourable -- counting it as a win would reward
    the model for starting anyone over an inactive player, which is not a judgment it made.
    """
    right = wrong = tied = declined = 0
    gained = 0.0
    for started, benched, confident in decisions:
        if not confident:
            declined += 1
            continue
        if benched is None or started not in actual or benched not in actual:
            continue
        margin = actual[started] - actual[benched]
        gained += margin
        if margin > 0:
            right += 1
        elif margin < 0:
            wrong += 1
        else:
            tied += 1
    return DecisionRecord(
        right=right, wrong=wrong, tied=tied, declined=declined, points_gained=gained
    )


def score_sources(
    forecasts: dict[str, dict[PlayerId, float]],
    actual: dict[PlayerId, float],
) -> list[SourceScore]:
    """MAE and bias per source, over the players that source projected *and* that played.

    Each source is scored on its own coverage rather than on the intersection of all of
    them. Restricting to the intersection would silently reward a source for projecting
    only easy players, and coverage is a real difference between sources worth seeing.
    Use ``compare`` for head-to-head, which does pair them.
    """
    scores: list[SourceScore] = []
    for source, projected in sorted(forecasts.items()):
        errors = [projected[pid] - actual[pid] for pid in projected if pid in actual]
        if not errors:
            continue
        scores.append(
            SourceScore(
                source=source,
                n=len(errors),
                mae=fmean(abs(e) for e in errors),
                bias=fmean(errors),
            )
        )
    return sorted(scores, key=lambda s: s.mae)


def compare(
    forecasts: dict[str, dict[PlayerId, float]],
    actual: dict[PlayerId, float],
    left: str,
    right: str,
) -> Comparison | None:
    """Head-to-head on the player-weeks both sources called, or None if there are none.

    Paired on purpose. The alternative -- comparing two independently computed MAEs -- has
    a standard error dominated by how hard the week was rather than by how the sources
    differ, which is how a real difference gets buried and a spurious one gets published.
    """
    if left not in forecasts or right not in forecasts:
        return None
    shared = [pid for pid in forecasts[left] if pid in forecasts[right] and pid in actual]
    if not shared:
        return None

    differences = [
        abs(forecasts[left][pid] - actual[pid]) - abs(forecasts[right][pid] - actual[pid])
        for pid in shared
    ]
    mean_difference = fmean(differences)
    # Standard error of the paired mean. One observation has no spread, and neither does a
    # set of identical forecasts; both are reported as unseparated rather than as certain.
    spread = stdev(differences) if len(differences) > 1 else 0.0
    standard_error = spread / (len(differences) ** 0.5) if spread else 0.0

    # A negative mean difference means `left` had the smaller error.
    better, worse = (left, right) if mean_difference < 0 else (right, left)
    return Comparison(
        better=better,
        worse=worse,
        mae_difference=abs(mean_difference),
        standard_error=standard_error,
        n=len(shared),
    )


def best_lineup(
    eligibility: dict[PlayerId, frozenset[Slot]],
    points: dict[PlayerId, float],
    slots: tuple[Slot, ...],
) -> dict[PlayerId, Slot]:
    """Fill the slots to maximize total points, greedily.

    Greedy is optimal here for the same reason it is optimal in ``domain.lineup``: standard
    roster slot-eligibility sets are nested, which makes this a matroid. The one difference
    is that this runs on points already scored, so there is no uncertainty to simulate --
    it is arithmetic, not a decision.

    Scarce slots are filled first. A FLEX filled before RB can strand a running back who
    was the only body left for the required slot.
    """
    remaining = dict(points)
    assignment: dict[PlayerId, Slot] = {}
    for slot in sorted(slots, key=lambda s: len(_fillers(s, eligibility, remaining))):
        candidates = _fillers(slot, eligibility, remaining)
        if not candidates:
            continue
        winner = max(candidates, key=lambda pid: remaining[pid])
        assignment[winner] = slot
        del remaining[winner]
    return assignment


def _fillers(
    slot: Slot,
    eligibility: dict[PlayerId, frozenset[Slot]],
    available: dict[PlayerId, float],
) -> list[PlayerId]:
    return [pid for pid in available if slot in eligibility.get(pid, frozenset())]


def score_lineup(
    week: int,
    recommended: dict[PlayerId, Slot],
    projections: dict[PlayerId, float],
    eligibility: dict[PlayerId, frozenset[Slot]],
    slots: tuple[Slot, ...],
    actual: dict[PlayerId, float],
) -> LineupScore:
    """Grade one week's lineup against what the owner would have done, and against perfect.

    A starter with no actual is counted as zero and reported in ``missing_players``. That
    is the truthful reading: a player with no row in the stats file did not play, and a
    lineup slot filled by a player who did not play scored nothing. But it is also how a
    name-matching failure would look, so the count is surfaced rather than buried.
    """
    got = {pid: actual.get(pid, 0.0) for pid in eligibility}
    missing = sum(1 for pid in recommended if pid not in actual)

    baseline = best_lineup(eligibility, projections, slots)
    optimal = best_lineup(eligibility, got, slots)
    return LineupScore(
        week=week,
        recommended=sum(got[pid] for pid in recommended if pid in got),
        baseline=sum(got[pid] for pid in baseline if pid in got),
        best_possible=sum(got[pid] for pid in optimal if pid in got),
        scored_players=sum(1 for pid in recommended if pid in actual),
        missing_players=missing,
    )
