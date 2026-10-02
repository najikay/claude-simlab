"""Trajectory evaluation : ATE / RPE in NumPy, no evo, no ROS.

The SVD (Kabsch/Umeyama without
scale) alignment and the timestamp association. Files are TUM format::

    timestamp tx ty tz qx qy qz qw

Everything here is deterministic and tested against analytic cases.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


def read_tum(path: Path | str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(timestamps [N], positions [N,3], quaternions [N,4]); comment lines start with ``#``."""
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.replace(",", " ").split()
        if len(parts) < 4:
            continue
        vals = [float(x) for x in parts[:8]]
        while len(vals) < 8:
            vals.append(0.0 if len(vals) < 7 else 1.0)
        rows.append(vals)
    if not rows:
        raise ValueError(f"no poses in {path}")
    a = np.array(rows, dtype=float)
    return a[:, 0], a[:, 1:4], a[:, 4:8]


def associate(t_a: np.ndarray, t_b: np.ndarray, max_diff: float = 0.02) -> tuple[np.ndarray, np.ndarray]:
    """Index pairs (ia, ib) whose timestamps are within ``max_diff`` (greedy, one-to-one)."""
    ib_all = np.searchsorted(t_b, t_a)
    pairs = []
    used: set[int] = set()
    for ia, j in enumerate(ib_all):
        cands = [k for k in (j - 1, j) if 0 <= k < len(t_b) and k not in used]
        if not cands:
            continue
        k = min(cands, key=lambda k: abs(t_b[k] - t_a[ia]))
        if abs(t_b[k] - t_a[ia]) <= max_diff:
            pairs.append((ia, k))
            used.add(k)
    if not pairs:
        raise ValueError("no timestamp matches; check the time base of the two files")
    p = np.array(pairs)
    return p[:, 0], p[:, 1]


def align_svd(est: np.ndarray, gt: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Rigid alignment est → gt (rotation R, translation t, aligned est). No scale."""
    mu_e, mu_g = est.mean(axis=0), gt.mean(axis=0)
    w = (est - mu_e).T @ (gt - mu_g)
    u, _, vt = np.linalg.svd(w)
    r = vt.T @ u.T
    if np.linalg.det(r) < 0:
        vt[2, :] *= -1
        r = vt.T @ u.T
    t = mu_g - r @ mu_e
    return r, t, est @ r.T + t


def ate(est: np.ndarray, gt: np.ndarray, align: bool = True) -> dict:
    """Absolute trajectory error statistics (metres)."""
    aligned = align_svd(est, gt)[2] if align else est
    err = np.linalg.norm(aligned - gt, axis=1)
    return {
        "rmse": float(np.sqrt(np.mean(err**2))),
        "mean": float(err.mean()),
        "median": float(np.median(err)),
        "max": float(err.max()),
        "n": len(err),
    }


def rpe(est: np.ndarray, gt: np.ndarray, delta: int = 1) -> dict:
    """Relative pose error on translation over ``delta`` frames (metres per step)."""
    if len(est) <= delta:
        raise ValueError("trajectory shorter than delta")
    d_e = est[delta:] - est[:-delta]
    d_g = gt[delta:] - gt[:-delta]
    err = np.linalg.norm(d_e, axis=1) - np.linalg.norm(d_g, axis=1)
    return {
        "rmse": float(np.sqrt(np.mean(err**2))),
        "mean": float(np.abs(err).mean()),
        "max": float(np.abs(err).max()),
        "delta": delta,
    }


def path_length(p: np.ndarray) -> float:
    """Total travelled distance."""
    return float(np.linalg.norm(np.diff(p, axis=0), axis=1).sum())


def evaluate(est_path: Path | str, gt_path: Path | str, max_diff: float = 0.02) -> dict:
    """Everything a run report needs, from two TUM files."""
    t_e, p_e, _ = read_tum(est_path)
    t_g, p_g, _ = read_tum(gt_path)
    ie, ig = associate(t_e, t_g, max_diff)
    e, g = p_e[ie], p_g[ig]
    a = ate(e, g)
    length = path_length(g)
    return {
        "ate": a,
        "rpe_1": rpe(e, g, 1),
        "rpe_10": rpe(e, g, 10) if len(e) > 10 else None,
        "matched": len(ie),
        "est_poses": len(t_e),
        "gt_poses": len(t_g),
        "gt_length_m": length,
        "drift_pct": float(100.0 * a["rmse"] / length) if length > 0 else None,
    }


def trajectory_svg(est_path: Path | str, gt_path: Path | str, size: int = 520) -> str:
    """Top-down XY plot (ground truth vs aligned estimate) as a standalone SVG string."""
    t_e, p_e, _ = read_tum(est_path)
    t_g, p_g, _ = read_tum(gt_path)
    ie, ig = associate(t_e, t_g)
    e, g = p_e[ie], p_g[ig]
    aligned = align_svd(e, g)[2]
    pts = np.vstack([g[:, :2], aligned[:, :2]])
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    span = float(max(hi - lo)) or 1.0
    pad = 24

    def xy(p: np.ndarray) -> str:
        x = pad + (p[:, 0] - lo[0]) / span * (size - 2 * pad)
        y = size - pad - (p[:, 1] - lo[1]) / span * (size - 2 * pad)
        return " ".join(f"{a:.1f},{b:.1f}" for a, b in zip(x, y, strict=True))

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">'
        f'<rect width="100%" height="100%" fill="white"/>'
        f'<polyline points="{xy(g)}" fill="none" stroke="#2563eb" stroke-width="2"/>'
        f'<polyline points="{xy(aligned)}" fill="none" stroke="#dc2626" stroke-width="1.5" stroke-dasharray="4 3"/>'
        f'<text x="{pad}" y="16" font-size="12" fill="#2563eb">ground truth</text>'
        f'<text x="{pad + 100}" y="16" font-size="12" fill="#dc2626">estimate (aligned)</text>'
        f'<text x="{pad}" y="{size - 6}" font-size="11" fill="#666">span {span:.2f} m</text>'
        "</svg>"
    )
