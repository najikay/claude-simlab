"""A world that reports how its arguments arrived: run_dir, then one word (used by the quoting test)."""

import json
import pathlib
import sys

pathlib.Path(sys.argv[1]).joinpath("metrics.json").write_text(
    json.dumps({"headline": {"n": len(sys.argv[2]), "argc": len(sys.argv)}}), encoding="utf-8"
)
