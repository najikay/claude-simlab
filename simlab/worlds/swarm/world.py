"""Obstacles and room layouts for the swarm lab.

Obstacles are circles (``pillars``) and axis-aligned boxes (``walls``) in the arena. They give the
worlds their first vocabulary beyond an empty square: closed rooms, corridors, pillars. Agents sense
an obstacle within ``sense_range`` and are pushed away from it; an agent that would end up inside
one is projected back to its edge (a ``hit``). With ``comms='los'`` a radio link is blocked when
the straight line between two agents crosses a wall (line of sight).

What this is not: no physics (an agent stops at a wall, nothing bounces), no doors that open, no
terrain. Fidelity beyond that belongs in Gazebo.
"""

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

LAYOUTS = ["none", "pillars", "rooms", "corridor"]
PLANS_DIR = Path(__file__).resolve().parents[2] / "plans"  # the plans shipped with the lab
EXTRA_PLAN_DIRS: list[Path] = []  # the person's own plan folders (``--plans-dir`` on the simulator)


def plan_dirs() -> list[Path]:
    """The person's own plan folders first, then the shipped ones."""
    return [d for d in EXTRA_PLAN_DIRS if d.is_dir()] + [PLANS_DIR]


WALL, DOOR = "#", "D"


def plan_names() -> list[str]:
    """Every floor plan (own folders and shipped), usable as ``plan:<name>``."""
    return sorted({p.stem for d in plan_dirs() if d.is_dir() for p in d.glob("*.txt")})


def parse_plan(text: str) -> list[str]:
    """Grid rows of a plan: comment lines (``# `` followed by text) and blank lines dropped."""
    # a comment is "# " followed by text beyond the plan characters ("# office: four rooms");
    # "#  ....#" is a wall row that happens to hold spaces, and a typo row is an error, not a comment
    rows = [
        ln.rstrip("\n")
        for ln in text.splitlines()
        if ln.strip() and not (ln.startswith("# ") and any(c not in "#.D " for c in ln))
    ]
    bad = sorted({c for r in rows for c in r if c not in "#.D "})
    if bad:
        raise ValueError(f"plan: only # . D and space are allowed; found {''.join(bad)!r}")
    if not rows:
        raise ValueError("an empty plan")
    width = max(len(r) for r in rows)
    return [r.ljust(width, ".") for r in rows]


def plan_boxes(rows: list[str], arena: float) -> list[tuple[float, float, float, float]]:
    """Wall cells as boxes in arena metres, runs merged first along rows, then down columns.

    Row 0 is the top of the plan; the arena's y grows upward, so the plan is flipped.
    """
    h, w = len(rows), len(rows[0])
    cell = arena / max(h, w)
    runs: list[tuple[int, int, int]] = []  # (col, row, length) of horizontal wall runs
    for r, row in enumerate(rows):
        c = 0
        while c < w:
            if row[c] == WALL:
                start = c
                while c < w and row[c] == WALL:
                    c += 1
                runs.append((start, r, c - start))
            else:
                c += 1
    # stack runs that sit exactly below each other into one box
    merged: list[list[int]] = []  # [col, row, length, rows]
    for col, row, length in runs:
        for m in merged:
            if m[0] == col and m[2] == length and m[1] + m[3] == row:
                m[3] += 1
                break
        else:
            merged.append([col, row, length, 1])
    return [(col * cell, (h - row - n) * cell, length * cell, n * cell) for col, row, length, n in merged]


@dataclass
class Obstacles:
    """Circles ``(cx, cy, r)`` and boxes ``(x, y, w, h)`` in arena metres."""

    circles: list[tuple[float, float, float]] = field(default_factory=list)
    boxes: list[tuple[float, float, float, float]] = field(default_factory=list)
    layout: str = "none"

    def __bool__(self) -> bool:
        """Return True when there is anything in the arena."""
        return bool(self.circles or self.boxes)

    # -- construction -----------------------------------------------------------
    @classmethod
    def layout_named(cls, name: str, arena: float) -> "Obstacles":
        """Build a named layout scaled to the arena: pillars, rooms (four, with doors), corridor."""
        a = arena
        if name == "pillars":
            r = a / 14
            pts = [(a / 3, a / 3), (2 * a / 3, a / 3), (a / 3, 2 * a / 3), (2 * a / 3, 2 * a / 3)]
            return cls(circles=[(x, y, r) for x, y in pts], layout=name)
        if name == "rooms":
            t, g = a / 40, a / 8  # wall thickness, door width
            half = a / 2
            boxes = [
                (
                    half - t / 2,
                    0.0,
                    t,
                    half - g / 2,
                ),  # vertical wall, lower half, door at the centre
                (half - t / 2, half + g / 2, t, half - g / 2),  # vertical wall, upper half
                (0.0, half - t / 2, half - g / 2, t),  # horizontal wall, left, door at the centre
                (half + g / 2, half - t / 2, half - g / 2, t),  # horizontal wall, right
            ]
            return cls(boxes=boxes, layout=name)
        if name == "corridor":
            w = a / 6  # corridor width, running left to right across the middle
            boxes = [
                (a / 4, 0.0, a / 2, a / 2 - w / 2),
                (a / 4, a / 2 + w / 2, a / 2, a / 2 - w / 2),
            ]
            return cls(boxes=boxes, layout=name)
        if name in ("none", "", None):
            return cls()
        if name.startswith("plan:"):
            return cls.from_plan_file(name[5:], arena)
        known = LAYOUTS + [f"plan:{n}" for n in plan_names()]
        raise ValueError(f"unknown layout {name!r}; one of {known}")

    @classmethod
    def from_plan(cls, text: str, arena: float, name: str = "plan") -> "Obstacles":
        """Walls from an ASCII floor plan: ``#`` wall, ``D`` door (open), anything else free."""
        return cls(boxes=plan_boxes(parse_plan(text), arena), layout=name)

    @classmethod
    def from_plan_file(cls, name: str, arena: float) -> "Obstacles":
        """``simlab/plans/<name>.txt`` (or a path to a .txt file) scaled to the arena."""
        if name.endswith(".txt"):
            path = Path(name)
        else:
            path = next(
                (d / f"{name}.txt" for d in plan_dirs() if (d / f"{name}.txt").is_file()),
                PLANS_DIR / f"{name}.txt",
            )
        if not path.is_file():
            raise ValueError(f"unknown plan {name!r}; known plans: {plan_names()}")
        return cls.from_plan(path.read_text(encoding="utf-8"), arena, f"plan:{path.stem}")

    @classmethod
    def from_json(cls, text: str) -> "Obstacles":
        """``{"circles": [[cx, cy, r], ...], "boxes": [[x, y, w, h], ...]}``."""
        d = json.loads(text) if text.strip() else {}
        return cls(
            circles=[tuple(map(float, c)) for c in d.get("circles") or []],  # type: ignore[misc]
            boxes=[tuple(map(float, b)) for b in d.get("boxes") or []],  # type: ignore[misc]
            layout=str(d.get("layout") or "custom"),
        )

    def to_dict(self) -> dict:
        """For ``obstacles.json`` in the run folder (the viewers draw it)."""
        return {"layout": self.layout, "circles": list(self.circles), "boxes": list(self.boxes)}

    def save(self, path: Path) -> None:
        """Write ``obstacles.json``."""
        path.write_text(json.dumps(self.to_dict(), indent=1), encoding="utf-8")

    # -- geometry ----------------------------------------------------------------
    def signed_distance(self, p: np.ndarray, arena: float | None = None) -> tuple[float, np.ndarray]:
        """Return the distance to the nearest obstacle edge (negative inside) and the way out."""
        best, out = math.inf, np.zeros(2)
        for cx, cy, r in self.circles:
            v = p - np.array([cx, cy])
            n = float(np.linalg.norm(v))
            d = n - r
            if d < best:
                best, out = d, (v / n if n > 1e-9 else np.array([1.0, 0.0]))
        for x, y, w, h in self.boxes:
            dx = max(x - p[0], 0.0, p[0] - (x + w))
            dy = max(y - p[1], 0.0, p[1] - (y + h))
            if dx > 0 or dy > 0:  # outside: direction from the nearest point on the box
                nx = min(max(p[0], x), x + w)
                ny = min(max(p[1], y), y + h)
                v = p - np.array([nx, ny])
                d = float(np.linalg.norm(v))
                if d < best:
                    best, out = d, v / max(d, 1e-9)
            else:  # inside: the nearest edge, pointing out through it (never the arena edge)
                cands = [
                    (p[0] - x, np.array([-1.0, 0.0]), arena is None or x > 0),
                    (x + w - p[0], np.array([1.0, 0.0]), arena is None or x + w < arena),
                    (p[1] - y, np.array([0.0, -1.0]), arena is None or y > 0),
                    (y + h - p[1], np.array([0.0, 1.0]), arena is None or y + h < arena),
                ]
                usable = [c for c in cands if c[2]] or cands
                d, v, _ = min(usable, key=lambda c: c[0])
                if -d < best:
                    best, out = -d, v
        return best, out

    def push_out(
        self, p: np.ndarray, margin: float = 0.05, arena: float | None = None
    ) -> tuple[np.ndarray, bool]:
        """Project a point inside an obstacle back to its edge plus a margin; True when it moved.

        With ``arena`` given, a wall on the arena boundary is never the exit (the point would
        leave the arena and be clipped straight back into the wall).
        """
        d, out = self.signed_distance(p, arena)
        if d >= margin:
            return p, False
        return p + out * (margin - d), True

    def repulsion(self, p: np.ndarray, sense_range: float) -> np.ndarray:
        """Return a velocity pushing away from any obstacle closer than ``sense_range`` (1/d²)."""
        d, out = self.signed_distance(p)
        if d >= sense_range:
            return np.zeros(2)
        return out * (1.0 / max(d, 0.1) ** 2)

    def blocks(self, a: np.ndarray, b: np.ndarray) -> bool:
        """Return True when the segment a→b crosses a circle or a box (no line of sight)."""
        for cx, cy, r in self.circles:
            if _segment_circle(a, b, np.array([cx, cy]), r):
                return True
        return any(_segment_box(a, b, (x, y, x + w, y + h)) for x, y, w, h in self.boxes)


def _segment_circle(a: np.ndarray, b: np.ndarray, c: np.ndarray, r: float) -> bool:
    ab = b - a
    n = float(ab @ ab)
    t = 0.0 if n < 1e-12 else float(np.clip(((c - a) @ ab) / n, 0.0, 1.0))
    closest = a + t * ab
    return float(np.linalg.norm(closest - c)) < r


def _segment_box(a: np.ndarray, b: np.ndarray, box: tuple[float, float, float, float]) -> bool:
    """Liang-Barsky clip: True when the segment intersects the axis-aligned box (x0, y0, x1, y1)."""
    x0, y0, x1, y1 = box
    dx, dy = float(b[0] - a[0]), float(b[1] - a[1])
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
        if abs(p) < 1e-12:
            if q < 0:
                return False
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return False
    return True
