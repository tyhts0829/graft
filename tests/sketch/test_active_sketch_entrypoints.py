"""通常保守対象の sketch が現行 API だけで 1 frame 描画できることを検証する。"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SKETCH_ROOT = _PROJECT_ROOT / "sketch"


def _has_main_guard_with_draw(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    has_draw = any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "draw"
        for node in tree.body
    )
    has_main_guard = any(
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
        for node in tree.body
    )
    return has_draw and has_main_guard


def _active_entrypoints() -> tuple[Path, ...]:
    paths = (
        path
        for path in _SKETCH_ROOT.rglob("*.py")
        if "agent_loop" not in path.relative_to(_SKETCH_ROOT).parts
    )
    return tuple(
        path
        for path in sorted(paths)
        if _has_main_guard_with_draw(path)
    )


_ENTRYPOINTS = _active_entrypoints()
_CHILD_PROGRAM = """
import inspect
import json
import sys
from pathlib import Path

import grafix
from grafix.api.render import RenderSession
from grafix.core.authoring_loader import default_session_authoring_definitions
from grafix.core.render_options import RenderOptions
from grafix.core.value_validation import (
    exact_bool,
    exact_integer,
    exact_string_choice,
    finite_real,
)

real_run = grafix.run


def smoke_run(draw, *args, **kwargs):
    inspect.signature(real_run).bind(draw, *args, **kwargs)
    exact_bool(
        kwargs.get("parameter_gui", True),
        name="parameter_gui",
    )
    exact_bool(
        kwargs.get("parameter_persistence", True),
        name="parameter_persistence",
    )
    exact_string_choice(
        kwargs.get("midi_mode", "7bit"),
        name="midi_mode",
        choices=("7bit", "14bit"),
    )
    exact_integer(kwargs.get("n_worker", 1), name="n_worker", minimum=0)
    finite_real(
        kwargs.get("render_scale", 1.0),
        name="render_scale",
        minimum=0.0,
        minimum_inclusive=False,
    )
    timeout = kwargs.get("evaluation_timeout", 5.0)
    if timeout is not None:
        finite_real(
            timeout,
            name="evaluation_timeout",
            minimum=0.0,
            minimum_inclusive=False,
    )
    finite_real(kwargs.get("fps", 60.0), name="fps")

    options = RenderOptions(
        background_color=kwargs.get(
            "background_color",
            (1.0, 1.0, 1.0),
        ),
        line_thickness=kwargs.get("line_thickness", 0.001),
        line_color=kwargs.get("line_color", (0.0, 0.0, 0.0)),
        canvas_size=kwargs.get("canvas_size", (800, 800)),
    )
    # 直接実行した preset module は自身の declaration を登録済みなので、
    # config directory を再 import せず、その generation を明示的に固定する。
    preset_root = (Path.cwd() / "sketch" / "presets").resolve()
    definitions = (
        default_session_authoring_definitions()
        if script_path.is_relative_to(preset_root)
        else None
    )
    with RenderSession(
        draw,
        options=options,
        parameter_source="code",
        seed=kwargs.get("seed"),
        definitions=definitions,
    ) as session:
        frame = session.render(0.0)
    print(json.dumps({"layers": len(frame.layers)}))


grafix.run = smoke_run
script_path = Path(sys.argv[1]).resolve()
sys.argv[:] = [str(script_path)]
globals()["__file__"] = str(script_path)
globals()["__package__"] = None
globals()["__cached__"] = None
exec(compile(script_path.read_bytes(), str(script_path), "exec"), globals())
"""


@pytest.mark.parametrize(
    "script_path",
    _ENTRYPOINTS,
    ids=lambda path: str(path.relative_to(_SKETCH_ROOT)),
)
def test_active_sketch_entrypoint_renders_headlessly(
    script_path: Path,
) -> None:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(_PROJECT_ROOT / "src")
    completed = subprocess.run(
        [sys.executable, "-c", _CHILD_PROGRAM, str(script_path)],
        cwd=_PROJECT_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=60.0,
    )

    assert completed.returncode == 0, completed.stderr


def test_active_sketch_inventory_is_not_empty() -> None:
    assert len(_ENTRYPOINTS) == 52
