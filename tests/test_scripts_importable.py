"""Every script under `scripts/` must be importable as a module.

`analyze_arm_runs.py` had a bare `from interleaved_generation_rounds import ...`
-- a sibling module, resolved only because Python puts a *script's* directory on
the path when it is run directly.  So the file worked from the command line and
failed the moment anything imported it, which is precisely why it went untested:
pytest could not collect it.

That is the worst shape a bug can take here.  It is invisible in the way the
file is normally used, and the thing it prevents -- a test -- is the thing that
would have found it.

This test is cheap and closes the class rather than the instance.
"""

from __future__ import annotations

import importlib
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCRIPTS = sorted((ROOT / "scripts").glob("*.py"))


def test_there_are_scripts_to_check():
    """A glob that matches nothing would make this file pass silently."""
    assert SCRIPTS, "scripts/ 下没有找到任何 .py"


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.stem)
def test_script_is_importable_as_a_module(path: Path):
    """Importing must not depend on being launched as a script."""
    try:
        importlib.import_module(f"scripts.{path.stem}")
    except ModuleNotFoundError as exc:
        # A missing third-party dependency is an environment fact, not a defect
        # in the file's own import structure.
        if exc.name and exc.name.split(".")[0] not in {"scripts"} and not (
            ROOT / f"{exc.name}.py"
        ).exists() and not (ROOT / "scripts" / f"{exc.name}.py").exists():
            pytest.skip(f"缺少第三方依赖 {exc.name}")
        raise
