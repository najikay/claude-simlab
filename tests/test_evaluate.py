"""ATE/RPE against analytic cases: a rigid transform is removed by alignment, a known offset survives."""

import math

import numpy as np
import pytest

from simlab.evaluate import align_svd, associate, ate, evaluate, path_length, read_tum, rpe


def circle(n: int, r: float = 2.0) -> np.ndarray:
    t = np.linspace(0, 2 * math.pi, n)
    return np.stack([r * np.cos(t), r * np.sin(t), np.zeros(n)], axis=1)


def test_alignment_removes_a_rigid_transform_and_keeps_an_offset():
    gt = circle(50)
    th = 0.7
    rot = np.array([[math.cos(th), -math.sin(th), 0], [math.sin(th), math.cos(th), 0], [0, 0, 1]])
    moved = gt @ rot.T + np.array([3.0, -1.0, 0.5])
    assert ate(moved, gt)["rmse"] == pytest.approx(0.0, abs=1e-9)
    assert ate(moved, gt, align=False)["rmse"] > 1.0
    r, _t, aligned = align_svd(moved, gt)
    assert np.allclose(aligned, gt, atol=1e-9) and abs(np.linalg.det(r) - 1) < 1e-9
    noisy = gt + np.array([0.3, 0.0, 0.0]) * np.where(np.arange(50) % 2 == 0, 1, -1)[:, None]
    assert ate(noisy, gt)["rmse"] == pytest.approx(0.3, abs=1e-6)
    assert rpe(gt * 1.1, gt, 1)["rmse"] == pytest.approx(0.1 * rpe(gt, gt * 0, 1)["rmse"], rel=1e-9)
    with pytest.raises(ValueError, match="shorter"):
        rpe(gt[:3], gt[:3], 5)
    assert path_length(circle(1000)) == pytest.approx(4 * math.pi, rel=1e-4)


def test_association_is_one_to_one_within_tolerance():
    ia, ib = associate(np.array([0.0, 1.0, 2.0, 3.0]), np.array([0.01, 1.02, 2.5, 3.0]), 0.05)
    assert list(ia) == [0, 1, 3] and list(ib) == [0, 1, 3]
    with pytest.raises(ValueError, match="no timestamp matches"):
        associate(np.array([0.0, 1.0]), np.array([10.0, 11.0]), 0.05)


def test_evaluate_reads_tum_files_with_comments_commas_and_short_rows(tmp_path):
    gt = tmp_path / "gt.tum"
    est = tmp_path / "est.tum"
    rows = [f"{i * 0.1:.3f} {math.cos(i / 5):.5f} {math.sin(i / 5):.5f} 0 0 0 0 1" for i in range(40)]
    gt.write_text("# timestamp tx ty tz qx qy qz qw\n" + "\n".join(rows) + "\n", encoding="utf-8")
    est.write_text(
        "\n".join(r.replace(" ", ",", 3).split(",")[0] + " " + " ".join(r.split()[1:4]) for r in rows),
        encoding="utf-8",
    )
    t, p, q = read_tum(est)
    assert len(t) == 40 and p.shape == (40, 3) and q[0].tolist() == [0.0, 0.0, 0.0, 1.0]
    r = evaluate(est, gt, max_diff=0.02)
    assert (
        r["matched"] == 40
        and r["ate"]["rmse"] == pytest.approx(0.0, abs=1e-9)
        and r["drift_pct"] == pytest.approx(0.0, abs=1e-9)
    )
    assert r["rpe_10"]["delta"] == 10
    (tmp_path / "empty.tum").write_text("# only a comment\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no poses"):
        read_tum(tmp_path / "empty.tum")
