# どこで: `src/grafix/core/parameters/persistence.py`。
# 何を: ParamStore の JSON 永続化（path 算出 / load / save）を提供する。
# なぜ: parameter_gui で調整したパラメータを、スクリプト単位で再起動後に復元できるようにするため。

from __future__ import annotations

import inspect
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .codec import dumps_param_store, loads_param_store
from .prune_ops import prune_stale_loaded_groups, prune_unknown_args_in_known_ops
from .store import ParamStore

from grafix.core.runtime_config import output_root_dir

_logger = logging.getLogger(__name__)


def _sanitize_filename_fragment(text: str) -> str:
    """ファイル名に埋め込めるように text を正規化して返す。"""

    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", str(text))
    normalized = normalized.strip("._-")
    return normalized or "unknown"


def _draw_script_stem(draw: Callable[[float], Any]) -> str:
    """draw 関数の定義元ファイルから stem を推定して返す。"""

    code = getattr(draw, "__code__", None)
    filename = getattr(code, "co_filename", None) if code is not None else None
    if not filename:
        try:
            filename = inspect.getsourcefile(draw) or inspect.getfile(draw)
        except Exception:
            filename = None
    if not filename:
        return "unknown"
    return Path(str(filename)).stem or "unknown"


def default_param_store_path(draw: Callable[[float], Any]) -> Path:
    """draw の定義元に基づく ParamStore の既定保存パスを返す。

    Notes
    -----
    パスは `{output_root}/param_store/{script_stem}.json`。
    """

    script_stem = _sanitize_filename_fragment(_draw_script_stem(draw))
    filename = f"{script_stem}.json"
    return output_root_dir() / "param_store" / filename


def load_param_store(path: Path) -> ParamStore:
    """JSON ファイルから ParamStore をロードして返す。無ければ空の ParamStore を返す。"""

    try:
        payload = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ParamStore()
    except OSError:
        return ParamStore()

    try:
        return loads_param_store(payload)
    except Exception:
        # 破損した JSON は利便性のため無視して起動する。
        return ParamStore()


def save_param_store(store: ParamStore, path: Path) -> None:
    """ParamStore を JSON として path に保存する（親ディレクトリは作成する）。"""

    # 保存前に「旧 site_id の残骸」を掃除して、GUI ヘッダ増殖とファイル肥大化を防ぐ。
    prune_stale_loaded_groups(store)
    removed_unknown = prune_unknown_args_in_known_ops(store)
    if removed_unknown:
        pairs = sorted({(str(k.op), str(k.arg)) for k in removed_unknown})
        preview = ", ".join(f"{op}.{arg}" for op, arg in pairs[:10])
        suffix = "" if len(pairs) <= 10 else ", ..."
        _logger.warning(
            "未登録引数を永続化から削除しました: count=%d pairs=%d [%s%s]",
            len(removed_unknown),
            len(pairs),
            preview,
            suffix,
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_param_store(store) + "\n", encoding="utf-8")
