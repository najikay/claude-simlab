"""A small Bayesian optimiser for the parameter search (``strategy: bayes``).

Sequential model-based search with a Gaussian process (RBF kernel) fitted on the points tried so
far and expected improvement over a pool of candidates. NumPy only, meant for the lab's sizes: a
handful of knobs, tens of runs. Categorical values (strings) are one-hot encoded; numbers are
scaled to [0, 1] over the pool. Nothing here is exotic: it is the textbook GP-EI loop, and its
job is to reach a good point in fewer runs than a grid when each run costs minutes.
"""

from __future__ import annotations

import random
from typing import Any

import numpy as np


class Encoder:
    """Map a candidate dict to a numeric vector over the pool's keys."""

    def __init__(self, pool: list[dict[str, Any]]) -> None:
        """Learn the keys, the numeric ranges and the categorical values from the pool."""
        self.keys = sorted({k for p in pool for k in p})
        self.cats: dict[str, list[str]] = {}
        self.lo: dict[str, float] = {}
        self.hi: dict[str, float] = {}
        for k in self.keys:
            vals = [p[k] for p in pool if k in p]
            if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
                self.lo[k], self.hi[k] = float(min(vals)), float(max(vals))
            else:
                self.cats[k] = sorted({str(v) for v in vals})

    def encode(self, p: dict[str, Any]) -> np.ndarray:
        """Numbers to [0, 1], categoricals one-hot."""
        out: list[float] = []
        for k in self.keys:
            if k in self.cats:
                out += [1.0 if str(p.get(k)) == c else 0.0 for c in self.cats[k]]
            else:
                lo, hi = self.lo[k], self.hi[k]
                v = float(p.get(k, lo))
                out.append((v - lo) / (hi - lo) if hi > lo else 0.0)
        return np.array(out, dtype=float)


def _kernel(a: np.ndarray, b: np.ndarray, length: float) -> np.ndarray:
    d = ((a[:, None, :] - b[None, :, :]) ** 2).sum(axis=2)
    return np.exp(-0.5 * d / (length * length))


def expected_improvement(
    x_seen: np.ndarray,
    y_seen: np.ndarray,
    x_new: np.ndarray,
    length: float = 0.35,
    noise: float = 1e-4,
) -> np.ndarray:
    """EI for minimisation of a standardised objective at ``x_new`` given the points seen."""
    mu_y, sd_y = float(y_seen.mean()), float(y_seen.std() or 1.0)
    y = (y_seen - mu_y) / sd_y
    k = _kernel(x_seen, x_seen, length) + noise * np.eye(len(x_seen))
    k_inv = np.linalg.inv(k)
    ks = _kernel(x_new, x_seen, length)
    mu = ks @ k_inv @ y
    var = np.clip(1.0 - np.einsum("ij,jk,ik->i", ks, k_inv, ks), 1e-9, None)
    sd = np.sqrt(var)
    best = float(y.min())
    z = (best - mu) / sd
    # Φ and φ of the standard normal without SciPy
    phi = np.exp(-0.5 * z * z) / np.sqrt(2 * np.pi)
    cdf = 0.5 * (1 + _erf(z / np.sqrt(2)))
    return (best - mu) * cdf + sd * phi


def _erf(x: np.ndarray) -> np.ndarray:
    # Abramowitz-Stegun 7.1.26, |error| < 1.5e-7
    s = np.sign(x)
    a = np.abs(x)
    t = 1.0 / (1.0 + 0.3275911 * a)
    poly = t * (0.254829592 + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429))))
    return s * (1.0 - poly * np.exp(-a * a))


class BayesProposer:
    """Pick the next point from a candidate pool: random at first, then by expected improvement."""

    def __init__(
        self,
        pool: list[dict[str, Any]],
        budget: int,
        minimize: bool = True,
        n_init: int = 3,
        seed: int = 7,
    ) -> None:
        """Remember the pool and the budget; the first points are drawn at random."""
        self.pool = list(pool)
        self.budget = min(budget, len(self.pool))
        self.minimize = minimize
        self.n_init = min(n_init, self.budget)
        self.rnd = random.Random(seed)
        self.enc = Encoder(self.pool)
        self.seen: list[tuple[dict[str, Any], float]] = []
        self.tried: list[dict[str, Any]] = []
        order = list(range(len(self.pool)))
        self.rnd.shuffle(order)
        self._init_order = order

    def next(self) -> dict[str, Any] | None:
        """Return the next candidate, or None when the budget is spent."""
        if len(self.tried) >= self.budget:
            return None
        remaining = [p for p in self.pool if p not in self.tried]
        if not remaining:
            return None
        if len(self.tried) < self.n_init or len(self.seen) < 2:  # a GP needs two points
            pick = next((self.pool[i] for i in self._init_order if self.pool[i] in remaining), remaining[0])
        else:
            x_seen = np.array([self.enc.encode(p) for p, _ in self.seen])
            y_seen = np.array([s if self.minimize else -s for _, s in self.seen])
            x_new = np.array([self.enc.encode(p) for p in remaining])
            ei = expected_improvement(x_seen, y_seen, x_new)
            pick = remaining[int(np.argmax(ei))]
        self.tried.append(pick)
        return pick

    def tell(self, point: dict[str, Any], score: float | None) -> None:
        """Report a result (None = the run failed; the point stays tried but unscored)."""
        if score is not None:
            self.seen.append((point, float(score)))
