"""YAML と探索 policy から RuntimeConfig を構築する application loader。"""

from __future__ import annotations

import os
import traceback
from importlib import resources
from pathlib import Path
from typing import Any

from grafix.core.runtime_config import (
    RuntimeConfig,
    RuntimeConfigFallback,
    RuntimeConfigReport,
    RuntimeConfigValue,
    runtime_config_from_mapping,
    validate_runtime_config_override,
)

_PACKAGED_CONFIG_SOURCE = "grafix/resource/default_config.yaml"
_PATH_KEYS = frozenset(
    {
        "paths.output_dir",
        "paths.sketch_dir",
        "paths.preset_module_dirs",
        "paths.font_dirs",
    }
)
_PATH_LIST_KEYS = frozenset({"paths.preset_module_dirs", "paths.font_dirs"})


def _explicit_config_path(path: str | Path | None) -> Path | None:
    """loader の明示 config path を検証・絶対化する。"""

    if path is None:
        return None
    if type(path) is str:
        candidate = Path(path)
    elif isinstance(path, Path):
        candidate = path
    else:
        raise TypeError("config path は str、Path、None のいずれかである必要があります")
    return candidate.expanduser().resolve(strict=False)


def _default_config_candidates() -> tuple[Path, ...]:
    """既定の `config.yaml` 探索候補を返す。

    探索順（先勝ち）:
    - `./.grafix/config.yaml`
    - `~/.config/grafix/config.yaml`
    """

    cwd = Path.cwd()
    home = Path.home()
    return (
        cwd / ".grafix" / "config.yaml",
        home / ".config" / "grafix" / "config.yaml",
    )


def _expand_path_text(text: str) -> str:
    """パス文字列内の `~` と環境変数を展開して返す。"""

    if type(text) is not str:
        raise TypeError("path text は str である必要があります")
    return os.path.expandvars(os.path.expanduser(text))


def _load_yaml_text(text: str, *, source: str) -> dict[str, Any]:
    """YAML テキストを読み、トップレベル mapping を dict として返す。

    Parameters
    ----------
    text:
        YAML 本文。
    source:
        エラーメッセージ用の識別子（パス等）。

    Returns
    -------
    dict[str, Any]
        YAML のトップレベル mapping。空（`null`）なら `{}`。
    """

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
    """UTF-8 の YAML ファイルを読み、dict を返す。"""

    text = path.read_text(encoding="utf-8")
    return _load_yaml_text(text, source=str(path))


def _load_packaged_default_config() -> dict[str, Any]:
    """同梱デフォルト config をロードして dict を返す。

    パッケージ配布（wheel/sdist）でも動作するように、`importlib.resources` を使って
    `grafix/resource/default_config.yaml` を読み込む。
    """

    try:
        blob = (
            resources.files("grafix")
            .joinpath("resource")
            .joinpath("default_config.yaml")
            .read_text(encoding="utf-8")
        )
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "同梱 default_config.yaml の読み込みに失敗しました"
            "（パッケージ配布物の package-data を確認してください）"
        ) from exc

    return _load_yaml_text(blob, source="grafix/resource/default_config.yaml")


def _resolve_path_text(value: Any, *, base_dir: Path, key: str) -> str | None:
    """config path 文字列を ``base_dir`` 基準の絶対 path にする。"""

    if value is None:
        return None
    if type(value) is not str:
        raise RuntimeError(f"{key} は path 文字列である必要があります: got={value!r}")
    text = value.strip()
    if not text:
        return ""
    path = Path(_expand_path_text(text))
    if not path.is_absolute():
        path = base_dir / path
    return str(path.resolve(strict=False))


def _resolve_path_list(value: Any, *, base_dir: Path, key: str) -> list[str]:
    """config path 配列を ``base_dir`` 基準の絶対 path 配列にする。"""

    if not isinstance(value, list):
        raise RuntimeError(f"{key} は path 文字列の配列である必要があります: got={value!r}")

    resolved: list[str] = []
    for index, item in enumerate(value):
        path = _resolve_path_text(item, base_dir=base_dir, key=f"{key}[{index}]")
        if not path:
            raise RuntimeError(f"{key}[{index}] は空でない path 文字列である必要があります")
        resolved.append(path)
    return resolved


def _resolve_layer_paths(
    payload: dict[str, Any],
    *,
    base_dir: Path,
    parent: tuple[str, ...] = (),
) -> dict[str, Any]:
    """1 config file に含まれる path leaf だけを絶対化する。"""

    resolved: dict[str, Any] = {}
    for key, value in payload.items():
        path = (*parent, str(key))
        dotted = ".".join(path)
        if dotted in _PATH_LIST_KEYS:
            resolved[key] = _resolve_path_list(value, base_dir=base_dir, key=dotted)
        elif dotted in _PATH_KEYS:
            resolved[key] = _resolve_path_text(value, base_dir=base_dir, key=dotted)
        elif isinstance(value, dict):
            resolved[key] = _resolve_layer_paths(value, base_dir=base_dir, parent=path)
        else:
            resolved[key] = value
    return resolved


def _flatten_config_leaves(
    payload: dict[str, Any],
    *,
    parent: tuple[str, ...] = (),
) -> dict[str, Any]:
    """nested config mapping を dotted leaf mapping にする。"""

    leaves: dict[str, Any] = {}
    for key, value in payload.items():
        path = (*parent, str(key))
        if isinstance(value, dict):
            leaves.update(_flatten_config_leaves(value, parent=path))
        else:
            leaves[".".join(path)] = value
    return leaves


def _freeze_report_value(value: Any) -> object:
    """report が mutable YAML 値を保持しないようにする。"""

    if isinstance(value, dict):
        return tuple((str(key), _freeze_report_value(item)) for key, item in value.items())
    if isinstance(value, list):
        return tuple(_freeze_report_value(item) for item in value)
    return value


def _resolved_report_path(
    *,
    key: str,
    value: Any,
    base_dir: Path,
) -> Path | tuple[Path, ...] | None:
    """raw effective path を show 用に絶対化する。"""

    if key in _PATH_LIST_KEYS:
        return tuple(Path(path) for path in _resolve_path_list(value, base_dir=base_dir, key=key))
    resolved = _resolve_path_text(value, base_dir=base_dir, key=key)
    return None if not resolved else Path(resolved)


def _merge_mappings(
    base: dict[str, Any],
    override: dict[str, Any],
) -> dict[str, Any]:
    """``override`` で指定された leaf だけを再帰的に上書きする。"""

    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _merge_mappings(current, value)
        else:
            merged[key] = value
    return merged


def load_runtime_config_report(
    config_path: str | Path | None = None,
) -> RuntimeConfigReport:
    """packaged、探索、明示 layer を後勝ちで読み、出典付き設定を返す。"""

    explicit_path = _explicit_config_path(config_path)
    if explicit_path is not None and not explicit_path.is_file():
        raise FileNotFoundError(f"config.yaml が見つかりません: {explicit_path}")

    discovered_path = next(
        (path for path in _default_config_candidates() if path.is_file()),
        None,
    )
    packaged_payload = _load_packaged_default_config()
    layers: list[tuple[dict[str, Any], str, Path, bool]] = [
        (packaged_payload, _PACKAGED_CONFIG_SOURCE, Path.cwd().resolve(), False)
    ]
    if discovered_path is not None:
        discovered_payload = _load_yaml_config(discovered_path)
        discovered_source = str(discovered_path.resolve(strict=False))
        validate_runtime_config_override(
            discovered_payload,
            schema=packaged_payload,
            source=discovered_source,
        )
        layers.append(
            (
                discovered_payload,
                discovered_source,
                discovered_path.resolve(strict=False).parent,
                True,
            )
        )
    if explicit_path is not None and (
        discovered_path is None or explicit_path != discovered_path.resolve(strict=False)
    ):
        explicit_payload = _load_yaml_config(explicit_path)
        explicit_source = str(explicit_path.resolve(strict=False))
        validate_runtime_config_override(
            explicit_payload,
            schema=packaged_payload,
            source=explicit_source,
        )
        layers.append(
            (
                explicit_payload,
                explicit_source,
                explicit_path.resolve(strict=False).parent,
                True,
            )
        )

    raw_effective: dict[str, Any] = {}
    payload: dict[str, Any] = {}
    source_by_key: dict[str, str] = {}
    base_dir_by_key: dict[str, Path] = {}
    for layer, source, base_dir, resolve_paths in layers:
        raw_effective = _merge_mappings(raw_effective, layer)
        runtime_layer = _resolve_layer_paths(layer, base_dir=base_dir) if resolve_paths else layer
        payload = _merge_mappings(payload, runtime_layer)
        for key in _flatten_config_leaves(layer):
            source_by_key[key] = source
            base_dir_by_key[key] = base_dir

    cfg = runtime_config_from_mapping(
        payload,
        config_path=explicit_path or discovered_path,
    )
    raw_leaves = _flatten_config_leaves(raw_effective)
    report_values: list[RuntimeConfigValue] = []
    for key in sorted(raw_leaves):
        is_path = key in _PATH_KEYS
        resolved_path = (
            _resolved_report_path(
                key=key,
                value=raw_leaves[key],
                base_dir=base_dir_by_key[key],
            )
            if is_path
            else None
        )
        report_values.append(
            RuntimeConfigValue(
                key=key,
                source=source_by_key[key],
                effective_value=_freeze_report_value(raw_leaves[key]),
                is_path=is_path,
                resolved_path=resolved_path,
            )
        )
    return RuntimeConfigReport(
        config=cfg,
        active_source=(
            str(cfg.config_path) if cfg.config_path is not None else _PACKAGED_CONFIG_SOURCE
        ),
        values=tuple(report_values),
    )


def load_runtime_config(config_path: str | Path | None = None) -> RuntimeConfig:
    """明示 path または CWD/HOME 探索から設定をロードする。"""

    return load_runtime_config_report(config_path).config


def runtime_config() -> RuntimeConfig:
    """既定探索で設定をロードする convenience。"""

    return load_runtime_config()


def runtime_config_report() -> RuntimeConfigReport:
    """既定探索で出典付き設定をロードする convenience。"""

    return load_runtime_config_report()


def _packaged_runtime_config_report() -> RuntimeConfigReport:
    """user layer を読まず packaged default だけをロードする。"""

    payload = _load_packaged_default_config()
    cfg = runtime_config_from_mapping(payload)
    values: list[RuntimeConfigValue] = []
    base_dir = Path.cwd().resolve()
    for key, value in sorted(_flatten_config_leaves(payload).items()):
        is_path = key in _PATH_KEYS
        values.append(
            RuntimeConfigValue(
                key=key,
                source=_PACKAGED_CONFIG_SOURCE,
                effective_value=_freeze_report_value(value),
                is_path=is_path,
                resolved_path=(
                    _resolved_report_path(key=key, value=value, base_dir=base_dir)
                    if is_path
                    else None
                ),
            )
        )
    return RuntimeConfigReport(
        config=cfg,
        active_source=_PACKAGED_CONFIG_SOURCE,
        values=tuple(values),
    )


def runtime_config_with_fallback(
    config_path: str | Path | None = None,
) -> tuple[RuntimeConfig, RuntimeConfigFallback | None]:
    """strict load 失敗時だけ packaged default と診断情報を返す。"""

    try:
        return load_runtime_config(config_path), None
    except (OSError, RuntimeError, ValueError) as exc:
        source = _explicit_config_path(config_path)
        if source is None:
            source = next(
                (path for path in _default_config_candidates() if path.is_file()),
                None,
            )
        report = _packaged_runtime_config_report()
        return report.config, RuntimeConfigFallback(
            summary=f"{type(exc).__name__}: {exc}",
            details="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            source=None if source is None else source.resolve(strict=False),
        )


def output_root_dir(config: RuntimeConfig | None = None) -> Path:
    """明示設定、または既定探索設定の出力 root を返す。"""

    if config is not None and type(config) is not RuntimeConfig:
        raise TypeError("config は exact RuntimeConfig または None です")
    cfg = runtime_config() if config is None else config
    return cfg.output_dir


__all__ = [
    "load_runtime_config",
    "load_runtime_config_report",
    "output_root_dir",
    "runtime_config",
    "runtime_config_report",
    "runtime_config_with_fallback",
]
