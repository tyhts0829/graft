# どこで: `src/grafix/export/output_paths.py`。
# 何を: draw 定義元（例: sketch/）に基づき、出力ファイルの保存先パスを決める。
# なぜ: `output/{kind}/` 配下で、ユーザースクリプトのディレクトリ構造をミラーして整理するため。

from __future__ import annotations

import inspect
import os
import re
import threading
from collections.abc import Callable
from pathlib import Path

from grafix.core.runtime_config import RuntimeConfig
from grafix.runtime_config_loader import runtime_config
from grafix.core.value_validation import (
    exact_integer,
    exact_string,
    finite_real,
)


class VersionedPathAllocator:
    """保存先を上書きしない連番 path として予約する。

    1 つの instance を 1 live session で共有することを想定する。実ファイルがまだ作られて
    いない非同期 export も予約集合へ残すため、キー連打や複数 thread からの呼び出しでも
    同じ path を二度返さない。既定 path が使用中なら ``name_001.ext`` から順に探す。

    Notes
    -----
    この class は空の placeholder を作らない。したがって export 失敗時に偽の成果物を
    残さず、既存の atomic writer / recorder の確定処理も妨げない。allocation 自体は
    別 process との排他境界ではないため、正式公開は必ず no-clobber transaction
    （``publish_capture_generation`` 等）で行う。同一 session 内では同じ instance を
    共有し、未公開の非同期予約同士の衝突を防ぐ。
    """

    def __init__(self, *, minimum_digits: int = 3) -> None:
        digits = exact_integer(
            minimum_digits,
            name="minimum_digits",
            minimum=1,
        )
        self._minimum_digits = digits
        self._reserved: set[str] = set()
        self._lock = threading.Lock()

    @staticmethod
    def _reservation_key(path: Path) -> str:
        """相対表記の違いを吸収した session 内予約キーを返す。"""

        return os.path.normcase(os.path.abspath(os.fspath(path)))

    @staticmethod
    def _is_occupied(path: Path) -> bool:
        """通常ファイルに加え broken symlink も使用中として扱う。"""

        return os.path.lexists(path)

    def _candidate(self, base_path: Path, index: int) -> Path:
        if index == 0:
            return base_path
        suffix = f"_{index:0{self._minimum_digits}d}"
        return base_path.with_name(f"{base_path.stem}{suffix}{base_path.suffix}")

    def allocate(self, base_path: str | Path) -> Path:
        """未使用の ``base_path`` またはその連番版を予約して返す。"""

        if isinstance(base_path, Path):
            base = base_path
        else:
            base = Path(exact_string(base_path, name="base_path"))
        if not base.name:
            raise ValueError("base_path はファイル名を含む必要がある")

        with self._lock:
            index = 0
            while True:
                candidate = self._candidate(base, index)
                key = self._reservation_key(candidate)
                if key not in self._reserved and not self._is_occupied(candidate):
                    self._reserved.add(key)
                    return candidate
                index += 1


def _sanitize_run_id(run_id: str) -> str:
    """run_id をファイル名の一部として使える形に正規化して返す。"""

    return re.sub(r"[^A-Za-z0-9._-]+", "_", run_id)


def _run_id_suffix(run_id: str | None) -> str:
    """run_id の接尾辞（例: `_v1`）を返す。未指定なら空文字を返す。"""

    if run_id is None:
        return ""
    s = exact_string(run_id, name="run_id").strip()
    if not s:
        return ""
    sanitized = _sanitize_run_id(s)
    if not sanitized:
        return ""
    return f"_{sanitized}"


def _fmt_canvas_dim_for_filename(value: float | int) -> str:
    """canvas の寸法をファイル名に埋め込むための短い表現にして返す。"""

    v = finite_real(
        value,
        name="canvas_size item",
        minimum=0.0,
        minimum_inclusive=False,
    )
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))

    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _canvas_size_suffix(canvas_size: tuple[float | int, float | int] | None) -> str:
    """canvas_size の接尾辞（例: `_800x800`）を返す。未指定なら空文字を返す。"""

    if canvas_size is None:
        return ""
    if type(canvas_size) is not tuple or len(canvas_size) != 2:
        raise TypeError("canvas_size は2要素の tuple である必要がある")
    w, h = canvas_size
    return f"_{_fmt_canvas_dim_for_filename(w)}x{_fmt_canvas_dim_for_filename(h)}"


def _layer_name_suffix(layer_name: str | None, *, max_len: int) -> str:
    """レイヤ名の接尾辞（例: `_outline`）を返す。未指定/空なら空文字を返す。"""

    if layer_name is None:
        return ""
    s = exact_string(layer_name, name="layer_name").strip()
    if not s:
        return ""

    # ファイル名として安全な範囲に正規化する。
    # `run_id` 用のサニタイズと同じ規則で統一する。
    sanitized = _sanitize_run_id(s).strip("_")
    if not sanitized:
        return ""

    if len(sanitized) > max_len:
        sanitized = sanitized[:max_len].rstrip("_")
    if not sanitized:
        return ""

    return f"_{sanitized}"


def gcode_layer_output_path(
    base_path: Path,
    *,
    layer_index: int,
    n_layers: int,
    layer_name: str | None = None,
    max_layer_name_len: int = 32,
) -> Path:
    """レイヤ別 G-code の保存先パスを返す。

    Notes
    -----
    - `base_path` と同じディレクトリへ保存する。
    - ファイル名は `<base_stem>_layer001[_<name>].gcode` 形式。
      `layer_name` が未指定/空、またはサニタイズ後に空になった場合は `<name>` を省略する。
    - layer index は 1 始まりで渡す想定。
    """

    if not isinstance(base_path, Path):
        raise TypeError("base_path は Path である必要がある")
    idx = exact_integer(layer_index, name="layer_index", minimum=1)
    total = exact_integer(n_layers, name="n_layers", minimum=1)
    if idx > total:
        raise ValueError("layer_index は n_layers 以下である必要がある")
    max_name_len = exact_integer(
        max_layer_name_len,
        name="max_layer_name_len",
        minimum=1,
    )
    # 例: 12 レイヤなら layer001..layer012 / 1000 レイヤなら layer0001..layer1000
    width = max(3, len(str(total)))

    idx_txt = f"{idx:0{width}d}"
    suffix = (
        f"_layer{idx_txt}"
        f"{_layer_name_suffix(layer_name, max_len=max_name_len)}"
    )

    # suffixes は壊さず、末尾の拡張子だけを使う（通常 `.gcode`）。
    return base_path.with_name(f"{base_path.stem}{suffix}{base_path.suffix}")


def gcode_layer_family_is_occupied(base_path: Path) -> bool:
    """同じ version の split G-code family entry が一つでも存在するか返す。"""

    if not isinstance(base_path, Path):
        raise TypeError("base_path は Path である必要があります")
    family_prefix = f"{base_path.stem}_layer".casefold()

    def belongs(path: Path) -> bool:
        path_stem = path.stem.casefold()
        if (
            path.suffix.casefold() != base_path.suffix.casefold()
            or not path_stem.startswith(family_prefix)
        ):
            return False
        remainder = path_stem[len(family_prefix) :]
        return bool(remainder) and remainder[0].isdigit()

    parent = base_path.parent
    return parent.exists() and any(belongs(path) for path in parent.iterdir())


def _is_pseudo_filename(text: str) -> bool:
    """`<stdin>` などの疑似ファイル名なら True を返す。"""

    s = str(text).strip()
    return bool(s) and s.startswith("<") and s.endswith(">")


def _draw_source_path(draw: Callable[[float], object]) -> Path | None:
    """draw の定義元ファイルパスを推定して返す。推定できなければ None を返す。"""

    explicit = getattr(draw, "__grafix_source_path__", None)
    if explicit is not None and not _is_pseudo_filename(str(explicit)):
        return Path(str(explicit))

    code = getattr(draw, "__code__", None)
    filename = getattr(code, "co_filename", None) if code is not None else None
    if filename and not _is_pseudo_filename(str(filename)):
        return Path(str(filename))

    try:
        found = inspect.getsourcefile(draw) or inspect.getfile(draw)
    except TypeError:
        found = None

    if found and not _is_pseudo_filename(str(found)):
        return Path(str(found))

    return None


def _find_ancestor_dir_ending_with(start_dir: Path, suffix: Path) -> Path | None:
    """start_dir の祖先から、suffix（相対パス）の末尾一致でディレクトリを探す。"""

    suffix_norm = Path(*[p for p in Path(suffix).parts if p not in {"", "."}])
    suffix_parts = suffix_norm.parts
    if not suffix_parts:
        return None

    for candidate in (start_dir, *start_dir.parents):
        parts = candidate.parts
        if len(parts) >= len(suffix_parts) and parts[-len(suffix_parts) :] == suffix_parts:
            return candidate
    return None


def _resolve_sketch_root_dir(sketch_dir: Path, *, source_path: Path) -> Path | None:
    """sketch_dir の絶対ルートを推定して返す。推定できなければ None。"""

    sketch_dir_p = Path(sketch_dir)
    source_resolved = source_path.resolve(strict=False)

    if sketch_dir_p.is_absolute():
        root = sketch_dir_p.resolve(strict=False)
        try:
            source_resolved.relative_to(root)
        except ValueError:
            return None
        return root

    # 相対 sketch_dir は、まず cwd 基準で解決する。
    root = sketch_dir_p.resolve(strict=False)
    try:
        source_resolved.relative_to(root)
        return root
    except ValueError:
        pass

    # cwd がプロジェクトルートでなければ source の ancestor から解決する。
    # 例: sketch_dir="sketch" で、source が ".../sketch/generated/foo.py" にある場合など。
    return _find_ancestor_dir_ending_with(source_resolved.parent, sketch_dir_p)


def _project_root_dir_from_sketch_root(sketch_root: Path, sketch_dir: Path) -> Path | None:
    """sketch_root からプロジェクトルート（sketch_dir の親）を推定して返す。"""

    sketch_dir_p = Path(sketch_dir)
    if sketch_dir_p.is_absolute():
        return sketch_root.parent

    n_parts = len([p for p in sketch_dir_p.parts if p not in {"", "."}])
    if n_parts <= 0:
        return None
    try:
        return sketch_root.parents[n_parts - 1]
    except IndexError:
        return None


def output_path_for_draw(
    *,
    kind: str,
    ext: str,
    draw: Callable[[float], object],
    run_id: str | None = None,
    canvas_size: tuple[float | int, float | int] | None = None,
    config: RuntimeConfig | None = None,
) -> Path:
    """draw の定義元（sketch_dir）に基づき、出力ファイルの保存先パスを返す。

    Notes
    -----
    - `paths.sketch_dir` が設定され、かつ draw の定義元ファイルがその配下にある場合:
      `output_root/{kind}/<sketch 相対 dir>/<stem>[_WxH][_run_id].{ext}`
    - それ以外の場合（フォールバック）:
      `output_root/{kind}/misc/<stem>[_WxH][_run_id].{ext}`
    - `canvas_size` が指定されている場合は `_800x800` のような接尾辞をファイル名へ付与する。
    """

    kind_name = exact_string(kind, name="kind")
    if not kind_name:
        raise ValueError("kind は空でない必要がある")
    ext_norm = exact_string(ext, name="ext").lstrip(".").strip()
    if not ext_norm:
        raise ValueError("ext は空でない必要がある")
    if not callable(draw):
        raise TypeError("draw は callable である必要がある")

    if config is not None and not isinstance(config, RuntimeConfig):
        raise TypeError("config は RuntimeConfig または None である必要がある")
    cfg = runtime_config() if config is None else config
    suffix = _run_id_suffix(run_id)

    source_path = _draw_source_path(draw)
    stem = source_path.stem if source_path is not None else "unknown"

    sketch_root: Path | None = None
    project_root: Path | None = None
    rel_parent: Path | None = None
    sketch_dir = cfg.sketch_dir
    if sketch_dir is not None and source_path is not None:
        sketch_root = _resolve_sketch_root_dir(sketch_dir, source_path=source_path)
        if sketch_root is not None:
            rel = source_path.resolve(strict=False).relative_to(sketch_root)
            rel_parent = rel.parent
            stem = rel.stem or stem
            project_root = _project_root_dir_from_sketch_root(sketch_root, sketch_dir)

    # output_dir / sketch_dir が相対パスの場合、cwd に依存してズレることがある。
    # cwd がプロジェクトルートでない場合だけ、project_root を推定して補正する。
    out_root = Path(cfg.output_dir)
    if not out_root.is_absolute() and project_root is not None:
        if Path.cwd().resolve(strict=False) != project_root.resolve(strict=False):
            out_root = project_root / out_root
    base_dir = out_root / kind_name

    filename = f"{stem}{_canvas_size_suffix(canvas_size)}{suffix}.{ext_norm}"
    if rel_parent is None:
        return base_dir / "misc" / filename
    if rel_parent == Path("."):
        return base_dir / filename
    return base_dir / rel_parent / filename


def default_param_store_path(
    draw: Callable[[float], object],
    *,
    run_id: str | None = None,
    config: RuntimeConfig | None = None,
) -> Path:
    """draw の定義元に対応する ParamStore の既定保存パスを返す。"""

    return output_path_for_draw(
        kind="param_store",
        ext="json",
        draw=draw,
        run_id=run_id,
        config=config,
    )


__all__ = [
    "VersionedPathAllocator",
    "default_param_store_path",
    "gcode_layer_family_is_occupied",
    "gcode_layer_output_path",
    "output_path_for_draw",
]
