"""SceneItem の公開型契約が runtime と同じ container だけを許すことを検証する。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


_VALID_PROBE = '''from grafix import render, run
from grafix.core.geometry import Geometry
from grafix.core.layer import Layer
from grafix.core.scene import SceneItem, normalize_scene


geometry = Geometry.create("circle", params={"r": 1.0})
layer = Layer(geometry=geometry, site_id="layer:1")


def draw(_t: float) -> SceneItem:
    return [geometry, (layer, [geometry])]


normalize_scene(geometry)
normalize_scene(layer)
normalize_scene([geometry])
normalize_scene((geometry, layer))
run(draw)
render(draw)
'''


_INVALID_PROBE = '''from collections.abc import Iterator, Sequence

from grafix import render, run
from grafix.core.geometry import Geometry
from grafix.core.scene import normalize_scene


geometry = Geometry.create("circle", params={"r": 1.0})
custom_sequence: Sequence[Geometry] = [geometry]
generator: Iterator[Geometry] = iter([geometry])

normalize_scene("scene")
normalize_scene(b"scene")
normalize_scene(custom_sequence)
normalize_scene({geometry})
normalize_scene(generator)


def invalid_draw(_t: float) -> str:
    return "scene"


run(invalid_draw)
render(invalid_draw)
'''


def test_scene_item_static_contract_matches_runtime_contract(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    valid_probe = tmp_path / "scene_item_valid.py"
    invalid_probe = tmp_path / "scene_item_invalid.py"
    valid_probe.write_text(_VALID_PROBE, encoding="utf-8")
    invalid_probe.write_text(_INVALID_PROBE, encoding="utf-8")

    source_paths = (repo_root / "src", repo_root / "typings", tmp_path)
    env = {
        **os.environ,
        "MYPYPATH": os.pathsep.join(str(path) for path in source_paths),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    checked = subprocess.run(
        (
            sys.executable,
            "-m",
            "mypy",
            "--no-incremental",
            "--cache-dir",
            str(tmp_path / "mypy-cache"),
            "--show-error-codes",
            str(valid_probe),
            str(invalid_probe),
        ),
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    output = checked.stdout + checked.stderr
    assert checked.returncode == 1, output
    valid_errors = [
        line
        for line in checked.stdout.splitlines()
        if line.startswith(f"{valid_probe}:") and ": error:" in line
    ]
    assert valid_errors == [], output
    assert output.count(f"{invalid_probe}:") >= 7, output
    assert 'Argument 1 to "run" has incompatible type "Callable[[float], str]"' in output
    assert 'Argument 1 to "render" has incompatible type "Callable[[float], str]"' in output
