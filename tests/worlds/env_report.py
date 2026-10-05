"""A world that reports which variables of its environment it received (used by the allow-list test)."""

import json
import os
import pathlib
import sys

# set by the test; not on the allow-list
leaked = int("SIMLAB_TEST_MARKER_ONE" in os.environ) + int("SIMLAB_TEST_MARKER_TWO" in os.environ)
pathlib.Path(sys.argv[1]).joinpath("metrics.json").write_text(
    json.dumps({"headline": {"leaked": leaked, "has_path": int("PATH" in os.environ)}}), encoding="utf-8"
)
