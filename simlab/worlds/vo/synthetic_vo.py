"""Synthetic visual-odometry run: a ground-truth path plus a drifting, noisy estimate.

Exists so the Sim Lab contract (run → eval → report) works end to end with no simulator,
no dataset and no install. Replace it with a real algorithm by pointing an experiment YAML
at another command that writes the same two TUM files into the run folder.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path


def main() -> int:
    """Write traj_gt.tum and traj_est.tum into --out."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--noise", type=float, default=0.05, help="per-pose position noise (m)")
    ap.add_argument("--drift", type=float, default=0.002, help="heading drift per step (rad)")
    ap.add_argument("--steps", type=int, default=400)
    ap.add_argument("--shape", choices=["circle", "lemniscate"], default="lemniscate")
    ap.add_argument(
        "--noise-model",
        dest="noise_model",
        choices=["gaussian", "drift", "scale", "outliers", "mixed"],
        default="mixed",
        help="gaussian: white position noise only; drift: heading drift only; scale: step-length scale error; outliers: occasional jumps; mixed: all",
    )
    ap.add_argument(
        "--scale-err",
        dest="scale_err",
        type=float,
        default=0.02,
        help="relative step-length error for scale/mixed",
    )
    ap.add_argument(
        "--outlier-rate",
        dest="outlier_rate",
        type=float,
        default=0.01,
        help="fraction of steps with a 10x jump for outliers/mixed",
    )
    ap.add_argument(
        "--rate",
        type=float,
        default=0.0,
        help="steps per second to emit (0 = as fast as possible); >0 paces a run for a live viewer",
    )
    ap.add_argument(
        "--dropout-rate",
        dest="dropout_rate",
        type=float,
        default=0.0,
        help="sensor dropout: probability per step that a dropout window starts (estimate poses missing)",
    )
    ap.add_argument(
        "--dropout-len",
        dest="dropout_len",
        type=int,
        default=15,
        help="sensor dropout: length of a window in steps",
    )
    ap.add_argument(
        "--latency",
        type=int,
        default=0,
        help="sensor latency: the estimate is stamped this many steps late (timestamps shifted)",
    )
    a = ap.parse_args()
    use_gauss = a.noise_model in ("gaussian", "mixed")
    use_drift = a.noise_model in ("drift", "mixed")
    use_scale = a.noise_model in ("scale", "mixed")
    use_outl = a.noise_model in ("outliers", "mixed")
    rnd = random.Random(a.seed)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    gt, est = [], []
    heading_err = 0.0
    ex, ey = 0.0, 0.0
    px, py = 0.0, 0.0
    f_gt = (out / "traj_gt.tum").open("w", encoding="utf-8")
    f_est = (out / "traj_est.tum").open("w", encoding="utf-8")
    for f in (f_gt, f_est):
        f.write("# timestamp tx ty tz qx qy qz qw\n")
    import time as _time

    dropped_until = -1
    dropped = 0
    for i in range(a.steps):
        t = i / 30.0
        s = 2 * math.pi * i / a.steps
        if a.shape == "circle":
            x, y = 5 * math.cos(s), 5 * math.sin(s)
        else:
            x, y = 6 * math.sin(s), 3 * math.sin(2 * s)
        z = 0.2 * math.sin(3 * s)
        gt.append((t, x, y, z))
        # the estimate integrates the true step rotated by an accumulating heading error
        dx, dy = x - px, y - py
        if use_drift:
            heading_err += a.drift + rnd.gauss(0, a.drift / 2)
        c, sn = math.cos(heading_err), math.sin(heading_err)
        scale = 1.0 + (rnd.gauss(a.scale_err, a.scale_err / 4) if use_scale else 0.0)
        gx, gy = (rnd.gauss(0, a.noise), rnd.gauss(0, a.noise)) if use_gauss else (0.0, 0.0)
        ex += (dx * c - dy * sn) * scale + gx
        ey += (dx * sn + dy * c) * scale + gy
        if use_outl and rnd.random() < a.outlier_rate:
            ex += rnd.gauss(0, 10 * a.noise)
            ey += rnd.gauss(0, 10 * a.noise)
        ez = z + (rnd.gauss(0, a.noise / 2) if use_gauss else 0.0)
        if a.dropout_rate > 0 and i > dropped_until and rnd.random() < a.dropout_rate:
            dropped_until = i + a.dropout_len
        in_dropout = i <= dropped_until
        t_est = (i + a.latency) / 30.0  # latency: the pose arrives with a later stamp
        f_gt.write(f"{t:.4f} {x:.5f} {y:.5f} {z:.5f} 0 0 0 1\n")
        if in_dropout:
            dropped += 1
        else:
            est.append((t_est, ex, ey, ez))
            f_est.write(f"{t_est:.4f} {ex:.5f} {ey:.5f} {ez:.5f} 0 0 0 1\n")
        if a.rate > 0:
            f_gt.flush()
            f_est.flush()
            _time.sleep(1.0 / a.rate)
        px, py = x, y
        if i % 100 == 0:
            print(f"step {i}/{a.steps}", flush=True)
    f_gt.close()
    f_est.close()
    (out / "algo_info.json").write_text(
        json.dumps(
            {
                "algorithm": "synthetic-vo",
                "noise_model": a.noise_model,
                "seed": a.seed,
                "noise": a.noise,
                "drift": a.drift,
                "scale_err": a.scale_err,
                "outlier_rate": a.outlier_rate,
                "steps": a.steps,
                "shape": a.shape,
                "poses": len(gt),
                "dropout_rate": a.dropout_rate,
                "dropout_len": a.dropout_len,
                "dropped_poses": dropped,
                "latency_steps": a.latency,
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    print("done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
