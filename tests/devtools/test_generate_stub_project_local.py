from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import grafix.api

from grafix.__main__ import main as grafix_main


def _write_project(project: Path) -> None:
    (project / ".grafix").mkdir(parents=True)
    (project / "sketch").mkdir(parents=True)
    (project / ".grafix/config.yaml").write_text(
        """version: 1
paths:
  output_dir: "../data/output"
  sketch_dir: "../sketch"
  preset_module_dirs: []
""",
        encoding="utf-8",
    )
    (project / "sketch/main.py").write_text(
        '''from __future__ import annotations
'''
        "from grafix.core.runtime_config import current_runtime_config\n"
        f"assert str(current_runtime_config().output_dir) == {str(project / 'data/output')!r}\n"
        '''
import numpy as np

from grafix import G, effect, preset, primitive
from grafix.core.geometry import Geometry


@primitive(meta={"size": {"kind": "float", "ui_min": 0.0, "ui_max": 10.0}})
def onboarding_local_primitive(*, size: float = 1.0):
    return np.zeros((0, 3), dtype=np.float32), np.zeros((1,), dtype=np.int32)


@effect(meta={"amount": {"kind": "float", "ui_min": 0.0, "ui_max": 1.0}})
def onboarding_local_effect(g, *, amount: float = 0.5):
    return g


@preset(meta={"size": {"kind": "float", "ui_min": 0.0, "ui_max": 10.0}})
def onboarding_local_preset(
    *, size: float = 1.0
) -> Geometry:
    return G.circle(radius=size)
''',
        encoding="utf-8",
    )


def test_stub_cli_defaults_to_project_local_output_and_includes_user_ops(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    _write_project(project)
    installed_stub = Path(grafix.api.__file__).with_name("__init__.pyi")
    installed_before = installed_stub.read_bytes()

    assert grafix_main(["stub", "--project", str(project)]) == 0

    output = project / "typings/grafix/api/__init__.pyi"
    root_proxy = project / "typings/grafix/__init__.pyi"
    generated = output.read_text(encoding="utf-8")
    assert "def onboarding_local_primitive(" in generated
    assert generated.count("def onboarding_local_effect(") == 2
    assert "def onboarding_local_preset(" in generated
    assert root_proxy.is_file()
    assert installed_stub.read_bytes() == installed_before

    probe = project / "preset_typing_probe.py"
    probe.write_text(
        "from grafix import P, RuntimeConfig, render, run, save\n"
        "from grafix.api import ParameterLoadState\n"
        "from grafix.api.render import CaptureProvenance, FrameStyle\n"
        "P.onboarding_local_preset(size=2.0)\n"
        "P.onboarding_local_presett(size=2.0)\n",
        encoding="utf-8",
    )
    checked = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--no-incremental",
            "--cache-dir",
            str(tmp_path / "mypy-cache"),
            str(probe),
        ],
        cwd=project,
        env={**os.environ, "MYPYPATH": str(project / "typings")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert checked.returncode == 1
    assert '"_P" has no attribute "onboarding_local_presett"' in checked.stdout
    assert '"_P" has no attribute "onboarding_local_preset"' not in checked.stdout

    canvas_probe = project / "canvas_size_typing_probe.py"
    canvas_probe.write_text(
        "from typing import assert_type\n"
        "from grafix import (\n"
        "    A2, A2_LANDSCAPE, A3, A3_LANDSCAPE, A4, A4_LANDSCAPE,\n"
        "    A5, A5_LANDSCAPE, A6, A6_LANDSCAPE, RenderOptions, SQUARE,\n"
        ")\n"
        "assert_type(A2, tuple[int, int])\n"
        "assert_type(A2_LANDSCAPE, tuple[int, int])\n"
        "assert_type(A3, tuple[int, int])\n"
        "assert_type(A3_LANDSCAPE, tuple[int, int])\n"
        "assert_type(A4, tuple[int, int])\n"
        "assert_type(A4_LANDSCAPE, tuple[int, int])\n"
        "assert_type(A5, tuple[int, int])\n"
        "assert_type(A5_LANDSCAPE, tuple[int, int])\n"
        "assert_type(A6, tuple[int, int])\n"
        "assert_type(A6_LANDSCAPE, tuple[int, int])\n"
        "assert_type(SQUARE, tuple[int, int])\n"
        "RenderOptions(canvas_size=SQUARE)\n",
        encoding="utf-8",
    )
    canvas_checked = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--no-incremental",
            "--cache-dir",
            str(tmp_path / "canvas-mypy-cache"),
            str(canvas_probe),
        ],
        cwd=project,
        env={**os.environ, "MYPYPATH": str(project / "typings")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert canvas_checked.returncode == 0, canvas_checked.stdout + canvas_checked.stderr

    # 同一 process での再生成でも module を二重登録せず正常に更新できる。
    assert grafix_main(["stub", "--project", str(project)]) == 0

    explicit = Path("generated/custom_api.pyi")
    assert (
        grafix_main(
            [
                "stub",
                "--project",
                str(project),
                "--no-default-import",
                "--output",
                str(explicit),
            ]
        )
        == 0
    )
    assert (project / explicit).is_file()
