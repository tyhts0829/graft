"""公開 operation decorator が元 callable の型を保持することを検証する。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


_VALID_PROBE = '''from __future__ import annotations

import numpy as np

from grafix import effect, primitive
from grafix.core.realized_geometry import GeomTuple


def empty_geometry() -> GeomTuple:
    return (
        np.empty((0, 3), dtype=np.float32),
        np.zeros((1,), dtype=np.int32),
    )


@primitive
def bare_primitive(count: int, *, label: str = "") -> GeomTuple:
    _ = count, label
    return empty_geometry()


@primitive(meta={"scale": {"kind": "float"}})
def configured_primitive(
    origin: tuple[float, float],
    *,
    scale: float = 1.0,
) -> GeomTuple:
    _ = origin, scale
    return empty_geometry()


@effect
def bare_effect(
    geometry: GeomTuple,
    strength: float,
    *,
    mode: str = "normal",
) -> GeomTuple:
    _ = strength, mode
    return geometry


@effect(meta={"strength": {"kind": "float"}})
def configured_effect(
    geometry: GeomTuple,
    *,
    strength: float = 1.0,
) -> GeomTuple:
    _ = strength
    return geometry


reveal_type(bare_primitive)
reveal_type(configured_primitive)
reveal_type(bare_effect)
reveal_type(configured_effect)

bare_primitive(1, label="ok")
configured_primitive((0.0, 1.0), scale=2.0)
bare_effect(empty_geometry(), 0.5, mode="normal")
configured_effect(empty_geometry(), strength=0.5)
'''


_INVALID_PROBE = '''from operation_authoring_valid import (
    bare_effect,
    bare_primitive,
    configured_effect,
    configured_primitive,
    empty_geometry,
)

bare_primitive("one", unexpected=True)
configured_primitive()
bare_effect(empty_geometry(), "strong")
configured_effect(empty_geometry(), strength="strong")
'''


def test_operation_decorators_preserve_bare_and_configured_signatures(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    valid_probe = tmp_path / "operation_authoring_valid.py"
    invalid_probe = tmp_path / "operation_authoring_invalid.py"
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

    assert checked.returncode == 1, checked.stdout + checked.stderr
    assert f"{valid_probe}:" not in "\n".join(
        line for line in checked.stdout.splitlines() if ": error:" in line
    )
    assert 'Revealed type is "def (count: builtins.int, *, label: builtins.str =)' in checked.stdout
    assert "origin: tuple[builtins.float, builtins.float]" in checked.stdout
    assert "strength: builtins.float" in checked.stdout
    assert 'Unexpected keyword argument "unexpected" for "bare_primitive"' in checked.stdout
    assert 'incompatible type "str"; expected "int"' in checked.stdout
    assert 'Missing positional argument "origin"' in checked.stdout
    assert 'Argument 2 to "bare_effect" has incompatible type "str"; expected "float"' in checked.stdout
    assert (
        'Argument "strength" to "configured_effect" has incompatible type "str"; '
        'expected "float"'
    ) in checked.stdout
