"""
どこで: `src/grafix/devtools/refresh_readme_grn.py`。
何を: `sketch/readme/grn` 配下のスケッチ群をまとめて SVG/PNG export し、README examples まで更新する。
なぜ: `P.grn_a5_frame`（a5_frame）の改訂時に、全 examples を 1 回で更新できるようにするため。

使い方:
- `PYTHONPATH=src python src/grafix/devtools/refresh_readme_grn.py`

Notes
-----
- ParamStore が存在する場合、保存済みの GUI 値（コードに明示していないパラメータ）を反映して export する。
- README 用の縮小画像生成と README の Examples ブロック更新は
  `prepare_readme_examples_grn.py` を呼び出して行う（サイズ等はそちらの定数で調整する）。

前提:
- PNG 生成には `resvg` が必要。
- README 用縮小には `sips` が必要（`prepare_readme_examples_grn.py` が使用）。
"""

from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass
from pathlib import Path

###############################################################################
# Parameters
###############################################################################

# 対象スケッチディレクトリ（リポジトリルートからの相対パス）。
SKETCH_DIR = Path("sketch/readme/grn")

# 対象スケッチファイル: `1.py`, `2.py`, ... のみ（`template.py` は除外）。
_SKETCH_FILE_RE = re.compile(r"^(\d+)\.py$")

# export の時刻（draw(t) に渡す）。
EXPORT_T = 0.0

# export の既定スタイル（スケッチの run(...) と揃える）。
LINE_THICKNESS = 0.001
LINE_COLOR = (0.0, 0.0, 0.0)
BACKGROUND_COLOR = (1.0, 1.0, 1.0)

# export 後に README examples（docs/readme/grn + README.md）まで更新する。
UPDATE_README_EXAMPLES = True

# 画像生成/README 更新を行わず、対象一覧だけ表示する。
DRY_RUN = False


@dataclass(frozen=True)
class _Sketch:
    num: int
    path: Path


def _find_repo_root() -> Path:
    """リポジトリルート（`pyproject.toml` のあるディレクトリ）を返す。"""
    here = Path(__file__).resolve()
    for p in [here.parent, *here.parents]:
        if (p / "pyproject.toml").exists():
            return p
    raise RuntimeError("repo root が見つかりません（pyproject.toml が見つからない）")


def _collect_sketches(root: Path) -> list[_Sketch]:
    """`sketch/readme/grn` の数字スケッチ（`^\\d+\\.py$`）を列挙する。"""
    d = root / SKETCH_DIR
    if not d.exists():
        raise FileNotFoundError(f"スケッチディレクトリが見つかりません: {d}")

    out: list[_Sketch] = []
    for p in sorted(d.glob("*.py")):
        m = _SKETCH_FILE_RE.match(p.name)
        if m is None:
            continue
        out.append(_Sketch(num=int(m.group(1)), path=p))

    out.sort(key=lambda x: x.num)
    return out


def _load_module(*, path: Path) -> object:
    """ファイルパスからスケッチモジュールをロードする（`1.py` のような数字名に対応）。"""
    module_name = f"_grafix_sketch_readme_grn_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"モジュールをロードできません: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _resolve_draw_and_canvas(mod: object) -> tuple[object, tuple[int, int]]:
    """スケッチモジュールから `draw` と `canvas_size` を取り出す。"""
    draw = getattr(mod, "draw", None)
    if draw is None or not callable(draw):
        raise ValueError("draw(t) が見つかりません")

    w = getattr(mod, "CANVAS_WIDTH", None)
    h = getattr(mod, "CANVAS_HEIGHT", None)
    if w is None or h is None:
        raise ValueError("CANVAS_WIDTH / CANVAS_HEIGHT が見つかりません")

    canvas_size = (int(w), int(h))
    return draw, canvas_size


def _export_one(
    *,
    draw: object,
    canvas_size: tuple[int, int],
    config: object,
) -> tuple[Path, Path]:
    """1 スケッチ分の SVG/PNG を export して保存先パスを返す。"""
    from grafix import RenderOptions, export, render
    from grafix.export.output_paths import output_path_for_draw
    from grafix.core.runtime_config import RuntimeConfig
    from grafix.export.image import default_png_output_path

    if not isinstance(config, RuntimeConfig):
        raise TypeError("config は RuntimeConfig である必要があります")
    svg_path = output_path_for_draw(
        kind="svg",
        ext="svg",
        draw=draw,  # type: ignore[arg-type]
        canvas_size=canvas_size,
        config=config,
    )
    frame = render(
        draw,  # type: ignore[arg-type]
        float(EXPORT_T),
        options=RenderOptions(
            canvas_size=canvas_size,
            line_color=LINE_COLOR,
            line_thickness=float(LINE_THICKNESS),
            background_color=BACKGROUND_COLOR,
        ),
        parameter_source="saved",
        config=config,
    )
    svg_result = export(frame, svg_path, overwrite=True)

    png_path = default_png_output_path(
        draw,  # type: ignore[arg-type]
        scale=frame.metadata.effective_config.png_scale,
        canvas_size=canvas_size,
        config=frame.metadata.effective_config,
    )
    png_result = export(frame, png_path, overwrite=True)
    return svg_result.path, png_result.path


def main() -> int:
    """`sketch/readme/grn` を一括で export し、README examples を更新する。"""
    root = _find_repo_root()

    # `P.grn_a5_frame` のような preset を確実に使えるよう、プロジェクトの config を優先する。
    candidate_config = root / ".grafix/config.yaml"
    config_path = candidate_config if candidate_config.exists() else None
    from grafix.core.runtime_config import bind_runtime_config
    from grafix.runtime_config_loader import load_runtime_config

    config = load_runtime_config(config_path)

    sketches = _collect_sketches(root)
    if not sketches:
        print(f"No sketches found: {root / SKETCH_DIR}")
        return 0

    print(f"Found: {len(sketches)} sketches")
    for sk in sketches:
        print(f"- {sk.path}")

    if DRY_RUN:
        return 0

    for sk in sketches:
        with bind_runtime_config(config):
            mod = _load_module(path=sk.path)
            draw, canvas_size = _resolve_draw_and_canvas(mod)
            svg_path, png_path = _export_one(
                draw=draw,
                canvas_size=canvas_size,
                config=config,
            )
        print(f"Exported: {sk.path.name} -> {svg_path} / {png_path}")

    if UPDATE_README_EXAMPLES:
        from grafix.devtools.prepare_readme_examples_grn import main as update_readme

        update_readme()

    return 0


__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
