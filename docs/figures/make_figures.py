"""Draw the README figures from real runs (PIL only): swarm paths over the office plan, and VO estimate vs truth.

python3 docs/figures/make_figures.py   # runs two quick experiments in a temporary lab home
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from simlab.lab import Lab

OUT = ROOT / "docs" / "figures"
PALETTE = [
    (56, 189, 248),
    (34, 197, 94),
    (251, 191, 36),
    (244, 114, 182),
    (167, 139, 250),
    (248, 113, 113),
    (45, 212, 191),
    (163, 230, 53),
]


def swarm_figure(lab: Lab) -> Path:
    m = lab.run("swarm-coverage", {"ticks": 300, "agents": 8}, variant="office-los")
    assert m["status"] == "done", m["error"]
    d = Path(lab.get(m["run_id"])["dir"])
    arena = 40.0
    S = 900
    img = Image.new("RGB", (S, S), (15, 23, 42))
    dr = ImageDraw.Draw(img)
    sc = lambda v: int(v / arena * (S - 60)) + 30
    for i in range(0, 41, 4):
        dr.line([(sc(i), sc(0)), (sc(i), sc(arena))], fill=(30, 41, 59), width=1)
        dr.line([(sc(0), sc(i)), (sc(arena), sc(i))], fill=(30, 41, 59), width=1)
    ob = json.loads((d / "obstacles.json").read_text(encoding="utf-8"))
    for x, y, w, h in ob.get("boxes", []):
        dr.rectangle((sc(x), S - sc(y + h), sc(x + w), S - sc(y)), fill=(100, 116, 139))
    paths: dict[int, list] = {}
    for line in (d / "agents.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        paths.setdefault(r["id"], []).append((sc(r["x"]), S - sc(r["y"])))
    for i, pts in paths.items():
        col = PALETTE[i % len(PALETTE)]
        if len(pts) > 1:
            dr.line(pts, fill=col, width=3)
        x, y = pts[-1]
        dr.ellipse((x - 8, y - 8, x + 8, y + 8), fill=col, outline=(15, 23, 42), width=2)
    h = m["headline"]
    dr.text(
        (30, 8),
        f"swarm-coverage · office-los · 8 agents · 300 ticks · coverage {h['coverage_pct']} % · blocked links {h.get('blocked_links_pct', '?')} %",
        fill=(226, 232, 240),
    )
    out = OUT / "swarm-office.png"
    img.save(out, optimize=True)
    return out


def vo_figure(lab: Lab) -> Path:
    m = lab.run("synthetic-vo", {"steps": 400}, variant="drift")
    assert m["status"] == "done", m["error"]
    d = Path(lab.get(m["run_id"])["dir"])

    def read(p: Path) -> list[tuple[float, float]]:
        pts = []
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or not line.strip():
                continue
            f = line.split()
            pts.append((float(f[1]), float(f[2])))
        return pts

    gt, est = read(d / "traj_gt.tum"), read(d / "traj_est.tum")
    xs = [p[0] for p in gt + est]
    ys = [p[1] for p in gt + est]
    lo, hi = min(min(xs), min(ys)) - 0.5, max(max(xs), max(ys)) + 0.5
    S = 900
    img = Image.new("RGB", (S, S), (15, 23, 42))
    dr = ImageDraw.Draw(img)
    sc = lambda v: int((v - lo) / (hi - lo) * (S - 60)) + 30
    dr.line([(sc(x), S - sc(y)) for x, y in gt], fill=(148, 163, 184), width=4)
    dr.line([(sc(x), S - sc(y)) for x, y in est], fill=(56, 189, 248), width=3)
    h = m["headline"]
    dr.text(
        (30, 8),
        f"synthetic-vo · drift · truth (grey) vs estimate (blue) · ATE {h['ate_rmse_m']} m · drift {h['drift_pct']} %",
        fill=(226, 232, 240),
    )
    out = OUT / "vo-drift.png"
    img.save(out, optimize=True)
    return out


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        lab = Lab(home=Path(td), python=sys.executable)
        print(swarm_figure(lab))
        print(vo_figure(lab))


if __name__ == "__main__":
    main()
