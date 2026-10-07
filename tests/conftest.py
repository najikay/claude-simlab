import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_script(rel: str, name: str):
    """Import a world script (they insert their own folder on sys.path, like the simulator does)."""
    path = ROOT / rel
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod  # dataclasses resolve postponed annotations through sys.modules
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def lab(tmp_path):
    from simlab.lab import Lab

    return Lab(home=tmp_path / "home", python=sys.executable)
