import math

import numpy as np
from conftest import load_script


def test_media_presets_and_drag():
    d = load_script("simlab/worlds/swarm/dynamics.py", "swarm_dynamics")
    air, water, fog = d.medium_named("air"), d.medium_named("water"), d.medium_named("fog")
    assert air.drag == 0 and water.drag > 0 and water.comm_range_scale < 1 and fog.sense_scale < 1
    v = d.apply_drag(np.array([2.0, 0.0]), water, 0.1)
    assert 0 < v[0] < 2.0
    assert set(d.MEDIA) == {"air", "water", "fog"} and set(d.VEHICLES) >= {
        "point",
        "unicycle",
        "fixed-wing",
        "quadrotor-lite",
    }


def test_vehicle_limits_and_tracking():
    d = load_script("simlab/worlds/swarm/dynamics.py", "swarm_dynamics")
    lim = d.limits_for("unicycle", max_speed=2.0, max_accel=4.0)
    assert lim.max_turn > 0 and lim.max_speed == 2.0
    heading = 0.0
    v = np.array([1.0, 0.0])
    for _ in range(80):  # a unicycle asked to go sideways turns there, it never jumps sideways
        v, heading = d.track(np.array([0.0, 2.0]), v, heading, lim, 0.1)
    assert abs(math.atan2(v[1], v[0]) - math.pi / 2) < 0.2
    fw = d.limits_for("fixed-wing", max_speed=2.0, max_accel=4.0)
    assert fw.min_speed > 0
    v2, _ = d.track(np.zeros(2), np.array([1.0, 0.0]), 0.0, fw, 0.1)
    assert np.linalg.norm(v2) >= fw.min_speed - 1e-9  # a fixed wing never stops
