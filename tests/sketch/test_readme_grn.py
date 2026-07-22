from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_grn_6_draw_evaluates_with_repository_preset_path(tmp_path: Path) -> None:
    preset_dir = _PROJECT_ROOT / "sketch" / "presets"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "version: 1",
                "paths:",
                "  preset_module_dirs:",
                f'    - "{preset_dir.as_posix()}"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(_PROJECT_ROOT / "src")
    command = "\n".join(
        [
            "import json, runpy",
            "from grafix.core.authoring_loader import load_config_authoring_definitions",
            "from grafix.core.operation_catalog import bind_operation_catalog",
            "from grafix.core.preset_catalog import bind_preset_catalog",
            "from grafix.runtime_config_loader import load_runtime_config",
            "from grafix.core.scene import normalize_scene",
            f"config = load_runtime_config({str(config_path)!r})",
            "definitions = load_config_authoring_definitions(config)",
            "with bind_operation_catalog(definitions.operations), bind_preset_catalog(definitions.presets):",
            f"    namespace = runpy.run_path({str(_PROJECT_ROOT / 'sketch/readme/grn/6.py')!r})",
            "    layers = normalize_scene(namespace['draw'](0.0))",
            "print(json.dumps([{'name': layer.name, 'color': layer.color} for layer in layers]))",
        ]
    )
    completed = subprocess.run(
        [sys.executable, "-c", command],
        cwd=_PROJECT_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    layers = json.loads(completed.stdout)
    assert [layer["name"] for layer in layers] == ["layout", "template", None]
    assert layers[0]["color"] == [191.0 / 255.0] * 3
    assert layers[1]["color"] == [0.0, 0.0, 0.0]
