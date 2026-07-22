from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from grafix.__main__ import main as grafix_main
from grafix.runtime_config_loader import load_runtime_config
from grafix.devtools.onboarding import init_project, list_examples


def test_init_creates_minimal_project_without_clobbering_existing_files(
    tmp_path: Path,
) -> None:
    project = tmp_path / "My Creative Project"

    first = init_project(project)

    assert {path.relative_to(project).as_posix() for path in first.created} == {
        ".grafix/config.yaml",
        "pyproject.toml",
        "sketch/__init__.py",
        "sketch/main.py",
        "sketch/presets/__init__.py",
    }
    assert first.existing == ()
    assert 'name = "my-creative-project"' in (project / "pyproject.toml").read_text()
    assert 'sketch_dir: "../sketch"' in (project / ".grafix/config.yaml").read_text()

    sketch_path = project / "sketch/main.py"
    sketch_path.write_text("# keep me\n", encoding="utf-8")
    second = init_project(project)

    assert second.created == ()
    assert len(second.existing) == 5
    assert sketch_path.read_text(encoding="utf-8") == "# keep me\n"


def test_init_config_paths_resolve_and_build_explicit_authoring_catalog(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    init_project(project)
    preset_module = project / "sketch/presets/path_probe.py"
    preset_module.write_text(
        "\n".join(
            [
                "from grafix import G, preset",
                "",
                "@preset(meta={})",
                "def onboarding_path_probe():",
                "    return G.circle(radius=1.0)",
                "",
            ]
        ),
        encoding="utf-8",
    )

    config_path = project / ".grafix/config.yaml"
    config = load_runtime_config(config_path)
    assert config.output_dir == project / "data/output"
    assert config.sketch_dir == project / "sketch"
    assert config.preset_module_dirs == (project / "sketch/presets",)

    repo_root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(repo_root / "src")
    command = "\n".join(
        [
            "from grafix import P",
            "from grafix.core.authoring_loader import load_config_authoring_definitions",
            "from grafix.core.operation_catalog import bind_operation_catalog",
            "from grafix.core.preset_catalog import bind_preset_catalog",
            "from grafix.runtime_config_loader import load_runtime_config",
            f"config = load_runtime_config({str(config_path)!r})",
            "definitions = load_config_authoring_definitions(config)",
            "with bind_operation_catalog(definitions.operations), bind_preset_catalog(definitions.presets):",
            "    print(type(P.onboarding_path_probe()).__name__)",
        ]
    )
    completed = subprocess.run(
        [sys.executable, "-c", command],
        cwd=project,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "Geometry"


def test_init_cli_dispatches_and_reports_created_files(tmp_path: Path, capsys) -> None:
    project = tmp_path / "demo"

    assert grafix_main(["init", str(project), "--name", "demo-art"]) == 0

    assert (project / "sketch/main.py").is_file()
    assert "[created] sketch/main.py" in capsys.readouterr().out


def test_examples_lists_and_copies_without_clobbering(
    tmp_path: Path,
    capsys,
) -> None:
    names = {example.name for example in list_examples()}
    assert {"basic_shapes", "custom_operation"} <= names

    assert grafix_main(["examples"]) == 0
    assert "basic_shapes" in capsys.readouterr().out

    destination = tmp_path / "copied.py"
    assert (
        grafix_main(
            ["examples", "copy", "basic_shapes", "--output", str(destination)]
        )
        == 0
    )
    original = destination.read_text(encoding="utf-8")
    assert "G.circle" in original

    assert (
        grafix_main(
            ["examples", "copy", "basic_shapes", "--output", str(destination)]
        )
        == 1
    )
    assert destination.read_text(encoding="utf-8") == original
    assert "上書きしません" in capsys.readouterr().err
