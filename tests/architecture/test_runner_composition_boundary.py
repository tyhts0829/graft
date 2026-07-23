"""runner import が optional GUI capability を初期化しないことを検査する。

application の正常終了・構築失敗・逆順 cleanup は公開 ``run()`` を通す
``tests/api/test_runner_authoring_composition.py`` が検査する。この module では class 名や
``run()`` の AST shape ではなく、import 時に実際に得る capability を固定する。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


def test_importing_runner_does_not_initialize_optional_gui_runtime() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    probe = """
import json
import sys
import pyglet

pyglet.options["shadow_window"] = False
import grafix.api.runner

optional_gui_modules = (
    "grafix.interactive.parameter_gui.catalog",
    "grafix.interactive.parameter_gui.gui",
    "grafix.interactive.runtime.parameter_gui_system",
)
print(json.dumps([name for name in optional_gui_modules if name in sys.modules]))
"""
    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(repo_root / "src"),
    }

    completed = subprocess.run(
        (sys.executable, "-c", probe),
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == []
