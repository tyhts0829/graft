# どこで: `src/grafix/core/runtime_config.py`。
# 何を: config.yaml による実行時設定（探索・ロード・キャッシュ）を提供する。
# なぜ: PyPI 環境でも、外部リソースや出力先をユーザーが指定できるようにするため。

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """grafix の実行時設定。"""

    config_path: Path | None
    output_dir: Path | None
    font_dirs: tuple[Path, ...]


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


def _load_yaml_config(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore[import-untyped]
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"PyYAML を import できません: {exc}") from exc

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}

    try:
        data = yaml.safe_load(text)
    except Exception as exc:
        raise RuntimeError(f"config.yaml の読み込みに失敗しました: path={path}") from exc

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise RuntimeError(f"config.yaml は mapping である必要があります: path={path}")

    return dict(data)


def _env_optional_path(name: str) -> Path | None:
    v = os.environ.get(name)
    return _as_optional_path(v)


def _env_path_list(name: str) -> list[Path]:
    v = os.environ.get(name)
    return _as_path_list(v)


def runtime_config() -> RuntimeConfig:
    """実行時設定をロードして返す（キャッシュ）。"""

    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE

    config_path: Path | None = None
    if _EXPLICIT_CONFIG_PATH is not None:
        config_path = _EXPLICIT_CONFIG_PATH
    else:
        for p in _default_config_candidates():
            if p.is_file():
                config_path = p
                break

    payload: dict[str, Any] = {}
    if config_path is not None and config_path.is_file():
        payload = _load_yaml_config(config_path)

    output_dir = _as_optional_path(payload.get("output_dir"))
    font_dirs = _as_path_list(payload.get("font_dirs"))

    # 環境変数は「config が無い/未指定のキーの補完」にのみ使う。
    if output_dir is None:
        output_dir = _env_optional_path("GRAFIX_OUTPUT_DIR")
    if not font_dirs:
        font_dirs = _env_path_list("GRAFIX_FONT_DIRS")

    cfg = RuntimeConfig(
        config_path=config_path,
        output_dir=output_dir,
        font_dirs=tuple(font_dirs),
    )
    _CONFIG_CACHE = cfg
    return cfg


def output_root_dir() -> Path:
    """出力ファイルを保存する既定ルートディレクトリを返す。

    優先順位:
    1) config.yaml / 環境変数の `output_dir`
    2) 既定: "data/output"
    """

    cfg = runtime_config()
    if cfg.output_dir is not None:
        return Path(cfg.output_dir)
    return Path("data") / "output"


__all__ = ["RuntimeConfig", "output_root_dir", "runtime_config", "set_config_path"]
