"""A world that reports which environment variables it received (used by the secrets test)."""

import json
import os
import pathlib
import sys

secrets = sum(k in os.environ for k in ("OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY"))
pathlib.Path(sys.argv[1]).joinpath("metrics.json").write_text(
    json.dumps({"headline": {"secrets": secrets, "has_path": int("PATH" in os.environ)}}), encoding="utf-8"
)
