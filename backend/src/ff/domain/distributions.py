"""Player-week outcome distributions and correlated sampling.

Point estimates are weak here — the best weekly projections explain 3-23% of variance.
The remaining edge is in the shape of the distribution, because the objective (win
probability) is non-linear and the distribution is what the non-linearity acts on.
See .claude/skills/fantasy-decision-math, section 0.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ff.domain.models import PlayerId, Position, Projection

#: Correlation priors by relationship. Individual pairs are NOT estimable -- a specific
#: QB-WR pair shares a handful of games, so a precise per-pair number is fitted noise.
#: Use archetype-level priors and stop. See fantasy-decision-math section 4.
SAME_TEAM_CORRELATION: dict[tuple[Position, Position], float] = {
    (Position.QB, Position.WR): 0.35,
    (Position.QB, Position.TE): 0.25,
    (Position.QB, Position.RB): 0.05,
    (Position.WR, Position.WR): -0.10,
    (Position.RB, Position.RB): -0.30,
    (Position.WR, Position.TE): -0.05,
}

#: Opposing players in the same game: game script links them negatively. This narrows the
#: margin distribution, which helps a favorite and hurts an underdog. Almost no consumer
#: tool models it.
OPPOSING_CORRELATION: dict[tuple[Position, Position], float] = {
    (Position.RB, Position.RB): -0.20,
    (Position.QB, Position.QB): 0.15,
    (Position.WR, Position.WR): 0.10,
}


def _lookup(table: dict[tuple[Position, Position], float], a: Position, b: Position) -> float:
    return table.get((a, b), table.get((b, a), 0.0))


@dataclass(frozen=True, slots=True)
class PlayerContext:
    """Everything the sampler needs about one player. Passed in, never fetched."""

    player_id: PlayerId
    position: Position
    nfl_team: str
    opponent_team: str
    projection: Projection


def correlation_matrix(contexts: list[PlayerContext]) -> np.ndarray:
    """Build a correlation matrix from archetype priors, repaired to be valid.

    Priors assembled pairwise are not guaranteed positive semi-definite, so we project
    onto the nearest valid matrix by clipping negative eigenvalues. Without this the
    Cholesky factorization in ``simulate`` fails on perfectly reasonable rosters.
    """
    n = len(contexts)
    m = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = contexts[i], contexts[j]
            if a.nfl_team == b.nfl_team:
                rho = _lookup(SAME_TEAM_CORRELATION, a.position, b.position)
            elif a.nfl_team == b.opponent_team:
                rho = _lookup(OPPOSING_CORRELATION, a.position, b.position)
            else:
                rho = 0.0
            m[i, j] = m[j, i] = rho

    eigenvalues, eigenvectors = np.linalg.eigh(m)
    if eigenvalues.min() < 1e-8:
        m = eigenvectors @ np.diag(np.clip(eigenvalues, 1e-8, None)) @ eigenvectors.T
        d = np.sqrt(np.diag(m))
        m = m / np.outer(d, d)
        np.fill_diagonal(m, 1.0)
    return m


def _gamma_params(mean: float, sd: float) -> tuple[float, float]:
    """Gamma shape and scale from a mean and sd, guarded against degenerate inputs."""
    mean = max(mean, 1e-6)
    sd = max(sd, 1e-6)
    shape = (mean / sd) ** 2
    scale = sd**2 / mean
    return shape, scale


def simulate(
    contexts: list[PlayerContext],
    draws: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Draw correlated fantasy point outcomes. Returns shape ``(draws, len(contexts))``.

    Marginals are zero-inflated Gamma: ``p_zero * delta_0 + (1 - p_zero) * Gamma``.
    Plain Gamma is the standard choice (non-negative, right-skewed, two parameters) but
    mishandles injury exits and true goose-eggs, so the zero component is explicit.

    Correlation is imposed with a Gaussian copula: sample correlated normals, map to
    uniforms, then through each player's marginal quantile function. This keeps the
    marginals exactly right while carrying the dependence structure.
    """
    if not contexts:
        return np.zeros((draws, 0))

    n = len(contexts)
    corr = correlation_matrix(contexts)
    chol = np.linalg.cholesky(corr)
    normals = rng.standard_normal((draws, n)) @ chol.T

    # Normal CDF without scipy: Phi(z) = (1 + erf(z / sqrt(2))) / 2
    from math import erf, sqrt

    erf_vec = np.vectorize(erf)
    uniforms = 0.5 * (1.0 + erf_vec(normals / sqrt(2.0)))
    uniforms = np.clip(uniforms, 1e-9, 1 - 1e-9)

    out = np.empty((draws, n))
    for i, ctx in enumerate(contexts):
        proj = ctx.projection
        p_zero = min(max(proj.p_zero, 0.0), 0.95)
        # Conditional mean: the unconditional mean already includes the zero mass.
        cond_mean = proj.mean / (1.0 - p_zero) if p_zero < 1.0 else 0.0
        shape, scale = _gamma_params(cond_mean, proj.total_sd)

        u = uniforms[:, i]
        is_zero = u < p_zero
        rescaled = np.clip((u - p_zero) / max(1.0 - p_zero, 1e-9), 1e-9, 1 - 1e-9)
        # Gamma quantile via the gamma distribution's inverse CDF, approximated by
        # sampling-free Wilson-Hilferty. Accurate enough at the precision this problem
        # supports (see fantasy-decision-math section 4) and avoids a scipy dependency.
        z = np.sqrt(2.0) * _erfinv(2.0 * rescaled - 1.0)
        wh = shape * (1.0 - 1.0 / (9.0 * shape) + z / (3.0 * np.sqrt(shape))) ** 3
        values = np.clip(wh, 0.0, None) * scale
        out[:, i] = np.where(is_zero, 0.0, values)
    return out


def _erfinv(y: np.ndarray) -> np.ndarray:
    """Inverse error function, Giles' rational approximation. ~1e-7 absolute error."""
    w = -np.log(np.clip((1.0 - y) * (1.0 + y), 1e-300, None))
    small = w < 5.0
    ws = np.where(small, w - 2.5, np.sqrt(np.where(small, 5.0, w)) - 3.0)

    coeffs_small = [
        2.81022636e-08,
        3.43273939e-07,
        -3.5233877e-06,
        -4.39150654e-06,
        0.00021858087,
        -0.00125372503,
        -0.00417768164,
        0.246640727,
        1.50140941,
    ]
    coeffs_large = [
        -0.000200214257,
        0.000100950558,
        0.00134934322,
        -0.00367342844,
        0.00573950773,
        -0.0076224613,
        0.00943887047,
        1.00167406,
        2.83297682,
    ]
    p_small = np.zeros_like(ws)
    for c in coeffs_small:
        p_small = p_small * ws + c
    p_large = np.zeros_like(ws)
    for c in coeffs_large:
        p_large = p_large * ws + c
    result: np.ndarray = np.where(small, p_small, p_large) * y
    return result
