# どこで: `src/grafix/core/runtime_config.py`。
# 何を: config.yaml による実行時設定（探索・ロード・キャッシュ）を提供する。
# なぜ: PyPI 環境でも、外部リソースや出力先をユーザーが指定できるようにするため。

from __future__ import annotations

import os
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """grafix の実行時設定。"""

    config_path: Path | None
    output_dir: Path
    font_dirs: tuple[Path, ...]
    window_pos_draw: tuple[int, int]
    window_pos_parameter_gui: tuple[int, int]
    parameter_gui_window_size: tuple[int, int]
    png_scale: float


_EXPLICIT_CONFIG_PATH: Path | None = None
_CONFIG_CACHE: RuntimeConfig | None = None


def set_config_path(path: str | Path | None) -> None:
    """以降の設定探索で使う明示 config パスを設定する。

    Notes
    -----
    `path` を None にすると明示指定を解除し、既定の探索に戻る。
    """

    global _EXPLICIT_CONFIG_PATH, _CONFIG_CACHE
    if path is None:
        _EXPLICIT_CONFIG_PATH = None
        _CONFIG_CACHE = None
        return
    p = Path(str(path)).expanduser()
    _EXPLICIT_CONFIG_PATH = p
    _CONFIG_CACHE = None


def _default_config_candidates() -> tuple[Path, ...]:
    cwd = Path.cwd()
    home = Path.home()
    return (
        cwd / ".grafix" / "config.yaml",
        home / ".config" / "grafix" / "config.yaml",
    )


def _expand_path_text(text: str) -> str:
    return os.path.expandvars(os.path.expanduser(str(text)))


def _as_optional_path(value: Any) -> Path | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    return Path(_expand_path_text(s))


def _as_path_list(value: Any) -> list[Path]:
    if value is None:
        return []
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return []
        parts = [p for p in s.split(os.pathsep) if p]
        return [Path(_expand_path_text(p)) for p in parts]

    try:
        seq = list(value)
    except Exception:
        return []

    out: list[Path] = []
    for item in seq:
        p = _as_optional_path(item)
        if p is not None:
            out.append(p)
    return out


def _as_mapping(value: Any, *, key: str) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    raise RuntimeError(f"{key} は mapping である必要があります: got={value!r}")


def _as_int_pair(value: Any, *, key: str) -> tuple[int, int] | None:
    if value is None:
        return None
    try:
        seq = list(value)
    except Exception as exc:
        raise RuntimeError(f"{key} は [x, y] の配列である必要があります: got={value!r}") from exc
    if len(seq) != 2:
        raise RuntimeError(f"{key} は [x, y] の配列である必要があります: got={value!r}")
    try:
        x = int(seq[0])
        y = int(seq[1])
    except Exception as exc:
        raise RuntimeError(f"{key} は [x, y] の整数配列である必要があります: got={value!r}") from exc
    return (x, y)


def _as_float(value: Any, *, key: str) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception as exc:
        raise RuntimeError(f"{key} は数値である必要があります: got={value!r}") from exc


def _load_yaml_text(text: str, *, source: str) -> dict[str, Any]:
    try:
        import yaml  # type: ignore[import-untyped]
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"PyYAML を import できません: {exc}") from exc

    try:
        data = yaml.safe_load(text)
    except Exception as exc:
        raise RuntimeError(f"config.yaml の読み込みに失敗しました: source={source}") from exc

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise RuntimeError(f"config.yaml は mapping である必要があります: source={source}")

    return dict(data)


def _load_yaml_config(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    return _load_yaml_text(text, source=str(path))


def _load_packaged_default_config() -> dict[str, Any]:
    """同梱デフォルト config をロードして dict を返す。"""

    try:
        blob = (
            resources.files("grafix")
            .joinpath("resource", "default_config.yaml")
            .read_text(encoding="utf-8")
        )
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "同梱 default_config.yaml の読み込みに失敗しました"
            "（パッケージ配布物の package-data を確認してください）"
        ) from exc

    return _load_yaml_text(blob, source="grafix/resource/default_config.yaml")


def runtime_config() -> RuntimeConfig:
    """実行時設定をロードして返す（キャッシュ）。"""

    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE

    explicit_path = _EXPLICIT_CONFIG_PATH
    if explicit_path is not None and not explicit_path.is_file():
        raise FileNotFoundError(f"config.yaml が見つかりません: {explicit_path}")

    discovered_path: Path | None = None
    for p in _default_config_candidates():
        if p.is_file():
            discovered_path = p
            break

    payload = _load_packaged_default_config()
    if discovered_path is not None:
        payload.update(_load_yaml_config(discovered_path))
    if explicit_path is not None:
        payload.update(_load_yaml_config(explicit_path))

    version = payload.get("version")
    if version is None:
        raise RuntimeError(
            "config.yaml の version が未設定です（同梱 default_config.yaml を確認してください）"
        )
    try:
        version_i = int(version)
    except Exception as exc:
        raise RuntimeError(f"config.yaml の version は整数である必要があります: got={version!r}") from exc
    if version_i != 1:
        raise RuntimeError(f"未対応の config.yaml version です: got={version_i}")

    paths = _as_mapping(payload.get("paths"), key="paths")
    output_dir = _as_optional_path(paths.get("output_dir"))
    if output_dir is None:
        raise RuntimeError(
            "paths.output_dir が未設定です（同梱 default_config.yaml を確認してください）"
        )
    font_dirs = _as_path_list(paths.get("font_dirs"))

    ui = _as_mapping(payload.get("ui"), key="ui")
    window_positions = _as_mapping(ui.get("window_positions"), key="ui.window_positions")

    window_pos_draw = _as_int_pair(
        window_positions.get("draw"),
        key="ui.window_positions.draw",
    )
    if window_pos_draw is None:
        raise RuntimeError(
            "ui.window_positions.draw が未設定です（同梱 default_config.yaml を確認してください）"
        )

    window_pos_parameter_gui = _as_int_pair(
        window_positions.get("parameter_gui"),
        key="ui.window_positions.parameter_gui",
    )
    if window_pos_parameter_gui is None:
        raise RuntimeError(
            "ui.window_positions.parameter_gui が未設定です（同梱 default_config.yaml を確認してください）"
        )

    parameter_gui = _as_mapping(ui.get("parameter_gui"), key="ui.parameter_gui")
    parameter_gui_window_size = _as_int_pair(
        parameter_gui.get("window_size"),
        key="ui.parameter_gui.window_size",
    )
    if parameter_gui_window_size is None:
        raise RuntimeError(
            "ui.parameter_gui.window_size が未設定です（同梱 default_config.yaml を確認してください）"
        )

    export = _as_mapping(payload.get("export"), key="export")
    png = _as_mapping(export.get("png"), key="export.png")
    png_scale = _as_float(png.get("scale"), key="export.png.scale")
    if png_scale is None:
        raise RuntimeError(
            "export.png.scale が未設定です（同梱 default_config.yaml を確認してください）"
        )
    if png_scale <= 0:
        raise ValueError(f"export.png.scale は正の値である必要があります: got={png_scale}")

    cfg = RuntimeConfig(
        config_path=explicit_path or discovered_path,
        output_dir=output_dir,
        font_dirs=tuple(font_dirs),
        window_pos_draw=window_pos_draw,
        window_pos_parameter_gui=window_pos_parameter_gui,
        parameter_gui_window_size=parameter_gui_window_size,
        png_scale=float(png_scale),
    )
    _CONFIG_CACHE = cfg
    return cfg


def output_root_dir() -> Path:
    """出力ファイルを保存する既定ルートディレクトリを返す。

    上書き順（後勝ち）:
    1) 同梱 default_config.yaml
    2) `./.grafix/config.yaml` / `~/.config/grafix/config.yaml`
    3) `run(..., config_path=...)` の `config_path`
    """

    cfg = runtime_config()
    return Path(cfg.output_dir)


__all__ = ["RuntimeConfig", "output_root_dir", "runtime_config", "set_config_path"]
