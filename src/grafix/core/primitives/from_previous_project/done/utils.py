"""
どこで: `util` の補助関数。
何を: 設定読み込み（YAML, フェイルソフト）とプロジェクトルート解決などの軽量ユーティリティ。
なぜ: 上位層が外部依存を最小にして構成情報へアクセスできるようにするため。
"""

from pathlib import Path
from typing import Any

import yaml


def _safe_load_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _find_project_root(start: Path) -> Path:
    """プロジェクトルートを推定して返す。

    - `src/` 配下から呼ばれることを想定し、上位に `.git` や `pyproject.toml`、`configs/` がある
      もっとも近いディレクトリを返す。
    - 見つからない場合は `start.parent.parent` をフォールバックとして返す。
    """
    cur = start.resolve()
    for parent in [cur] + list(cur.parents):
        if (
            (parent / ".git").exists()
            or (parent / "pyproject.toml").exists()
            or (parent / "configs").exists()
        ):
            return parent
    # 典型: <repo>/src/util/utils.py -> <repo>
    return cur.parent.parent


def load_config() -> dict[str, Any]:
    """構成を読み込んで辞書で返す（フェイルソフト）。

    優先順:
    1) `configs/default.yaml`（ベース）
    2) ルート `config.yaml`（ベースに上書き）

    - いずれも存在しない/不正な場合は空辞書を返す。
    - ネストした辞書のディープマージは行わず、トップレベルのみ上書き。
    """
    project_root = _find_project_root(Path(__file__).parent)
    base: dict[str, Any] = {}

    default_path = project_root / "configs" / "default.yaml"
    if default_path.exists():
        base.update(_safe_load_yaml(default_path))

    root_config_path = project_root / "config.yaml"
    if root_config_path.exists():
        base.update(_safe_load_yaml(root_config_path))

    return base
