from __future__ import annotations

from pathlib import Path

from grafix.runtime_config_loader import load_runtime_config
from grafix.devtools.generate_stub import generate_stubs_str


def _write_config(*, path: Path, preset_module_dir: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "version: 1",
                "paths:",
                '  output_dir: "data/output"',
                "  preset_module_dirs:",
                f'    - "{preset_module_dir.as_posix()}"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_generate_stub_lists_user_presets_on_p(tmp_path: Path) -> None:
    preset_dir = tmp_path / "presets"
    preset_dir.mkdir(parents=True, exist_ok=True)

    (preset_dir / "user.py").write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "from grafix.api import preset",
                "from grafix.core.geometry import Geometry",
                "",
                'meta = {"out": {"kind": "str"}}',
                "",
                "@preset(meta=meta)",
                "def stubgen_path(*, out: str = 'out') -> Geometry:",
                '    """path 文字列を使う preset。',
                "",
                "    Parameters",
                "    ----------",
                "    out : str",
                "        出力 path。",
                '    """',
                "    return Geometry.create(op='concat')",
                "",
            ]
        ),
        encoding="utf-8",
    )

    cfg_path = tmp_path / "config.yaml"
    _write_config(path=cfg_path, preset_module_dir=preset_dir)

    stub = generate_stubs_str(config=load_runtime_config(cfg_path))

    assert (
        "def stubgen_path(self, *, activate: bool = ..., out: str = ...) "
        "-> SceneItem:" in stub
    )
    method = stub.split("    def stubgen_path(", 1)[1].split("\n    def ", 1)[0]
    assert "activate:" in method
    assert "out:" in method
    assert "key:" not in method
    assert "instance_key:" not in method
    assert "shared:" not in method
    assert "def __getattr__(self, name: str)" not in stub
