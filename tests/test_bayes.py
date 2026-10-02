"""The Bayesian proposer: encodes mixed spaces, never repeats a point, and homes in on a quadratic's minimum."""

from simlab.bayes import BayesProposer, Encoder


def test_encoder_handles_numbers_and_categoricals():
    enc = Encoder([{"a": 1, "m": "x"}, {"a": 3, "m": "y"}])
    assert enc.encode({"a": 2, "m": "y"}).tolist() == [0.5, 0.0, 1.0]
    assert enc.encode({"a": 1, "m": "x"}).tolist() == [0.0, 1.0, 0.0]


def test_proposer_finds_the_minimum_of_a_bowl_without_repeating():
    space = {"x": [float(v) for v in range(-5, 6)], "kind": ["a", "b"]}
    pool = [{"x": x, "kind": k} for x in space["x"] for k in space["kind"]]
    prop = BayesProposer(pool, budget=14, minimize=True, seed=3)
    seen: list[tuple[dict, float]] = []
    for _ in range(14):
        p = prop.next()
        assert p is not None and p["x"] in space["x"] and p["kind"] in space["kind"]
        assert all(p != q for q, _ in seen), "a point was proposed twice"
        score = (p["x"] - 1.0) ** 2 + (0.0 if p["kind"] == "a" else 2.0)
        prop.tell(p, score)
        seen.append((p, score))
    assert prop.next() is None, "the budget is spent"
    best = min(seen, key=lambda s: s[1])[0]
    assert best == {"x": 1.0, "kind": "a"}
    # the search prefers the good region: more than half of the later trials are near the optimum
    late = [p for p, _ in seen[6:]]
    assert sum(abs(p["x"] - 1.0) <= 2 for p in late) > len(late) / 2
