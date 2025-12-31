"""
どこで: `tools/gen_g_stubs.py`。
何を: `grafix.api` の IDE 補完用スタブ `src/grafix/api/__init__.pyi` を自動生成する。
なぜ: `G`/`E` が動的名前空間のため、静的解析が公開 API を把握できる形を用意するため。

補足:
- effect の public param の型アノテーションは「stub 側で解決可能な名前」だけを書く（自動 import 収集はしない）。
- `ParamMeta.kind == "vec3"` の場合、`tuple[float, float, float]` アノテーションでも stub は `Vec3` 表現を優先する。
"""

from __future__ import annotations

import importlib
import inspect
import re
import sys
from pathlib import Path
from typing import Any

_VALID_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_VEC3_TUPLE_RE = re.compile(
    r"(?:tuple|Tuple)\[\s*float\s*,\s*float\s*,\s*float\s*\]"
)


def _is_valid_identifier(name: str) -> bool:
    return _VALID_IDENT_RE.match(name) is not None


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _ensure_src_on_syspath(repo_root: Path) -> None:
    src_dir = repo_root / "src"
    src_str = str(src_dir)
    if src_str not in sys.path:
        sys.path.insert(0, src_str)


def _shorten(text: str, *, limit: int = 120) -> str:
    t = " ".join(text.split())
    if "。" in t:
        t = t.split("。", 1)[0]
    if len(t) > limit:
        t = t[: limit - 1] + "…"
    return t


def _parse_numpy_doc(doc: str) -> tuple[str | None, dict[str, str]]:
    """NumPy スタイル docstring から summary と引数説明を抽出する。"""
    if not doc:
        return None, {}

    lines = doc.splitlines()

    summary: str | None = None
    for ln in lines:
        s = ln.strip()
        if s:
            summary = s
            break

    # "Parameters" セクションを探索
    i = 0
    while i < len(lines):
        if lines[i].strip().lower() == "parameters":
            if i + 1 < len(lines) and set(lines[i + 1].strip()) == {"-"}:
                i += 2
                break
        i += 1
    else:
        return summary, {}

    param_docs: dict[str, str] = {}
    current: str | None = None

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # 次セクションに到達したら終了（見出し + 罫線）
        if stripped and not line.startswith((" ", "\t")) and i + 1 < len(lines):
            nxt = lines[i + 1].strip()
            if nxt and set(nxt) == {"-"}:
                break

        # "name : type" 行を拾う（先頭カラムのみ）
        if stripped and not line.startswith((" ", "\t")):
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$", stripped)
            if m:
                current = m.group(1)
                param_docs[current] = ""
                i += 1
                continue

        # 説明行（インデント付き想定）
        if current is not None and stripped:
            if param_docs[current]:
                param_docs[current] += " "
            param_docs[current] += stripped

        i += 1

    out: dict[str, str] = {}
    for k, v in param_docs.items():
        v2 = _shorten(v) if v else ""
        if v2:
            out[k] = v2
    return summary, out


def _meta_hint(meta: Any) -> str | None:
    kind = getattr(meta, "kind", None)
    ui_min = getattr(meta, "ui_min", None)
    ui_max = getattr(meta, "ui_max", None)
    choices = getattr(meta, "choices", None)

    parts: list[str] = []
    if kind:
        parts.append(str(kind))

    if ui_min is not None or ui_max is not None:
        if ui_min is not None and ui_max is not None:
            parts.append(f"range [{ui_min}, {ui_max}]")
        elif ui_min is not None:
            parts.append(f"min {ui_min}")
        elif ui_max is not None:
            parts.append(f"max {ui_max}")

    try:
        seq = list(choices) if choices is not None else []
    except Exception:
        seq = []
    if seq:
        preview = ", ".join(map(repr, seq[:6]))
        parts.append(f"choices {{ {preview}{' …' if len(seq) > 6 else ''} }}")

    return ", ".join(parts) if parts else None


def _type_for_kind(kind: str) -> str:
    if kind == "float":
        return "float"
    if kind == "int":
        return "int"
    if kind == "bool":
        return "bool"
    if kind == "str":
        return "str"
    if kind == "font":
        return "str"
    if kind == "choice":
        return "str"
    if kind == "vec3":
        return "Vec3"
    return "Any"


def _type_str_from_annotation(annotation: Any) -> str | None:
    if annotation is inspect._empty:
        return None
    if isinstance(annotation, str):
        s = annotation.strip()
        return s or None
    try:
        return inspect.formatannotation(annotation)
    except Exception:
        s = str(annotation).strip()
        return s or None


def _type_str_from_impl_param(impl: Any, param_name: str) -> str | None:
    try:
        sig = inspect.signature(impl)
    except Exception:
        return None

    p = sig.parameters.get(param_name)
    if p is None:
        return None
    return _type_str_from_annotation(p.annotation)


def _type_str_for_effect_param(
    *, impl: Any | None, param_name: str, meta: Any
) -> str:
    kind = str(getattr(meta, "kind", ""))
    fallback = _type_for_kind(kind)
    if impl is None:
        return fallback

    type_str = _type_str_from_impl_param(impl, param_name)
    if type_str is None:
        return fallback

    if kind == "vec3":
        type_str = _VEC3_TUPLE_RE.sub("Vec3", type_str)
    return type_str


def _resolve_impl_callable(kind: str, name: str) -> Any | None:
    """built-in 実装関数（docstring ソース）を最善で見つける。"""
    if kind == "primitive":
        module_name = f"grafix.core.primitives.{name}"
    elif kind == "effect":
        module_name = f"grafix.core.effects.{name}"
    else:
        raise ValueError(f"unknown kind: {kind!r}")

    try:
        mod = importlib.import_module(module_name)
    except Exception:
        return None

    fn = getattr(mod, name, None)
    return fn if callable(fn) else None


def _render_docstring(
    *,
    summary: str | None,
    param_order: list[str],
    parsed_param_docs: dict[str, str],
    meta_by_name: dict[str, Any],
) -> list[str]:
    lines: list[str] = []
    if summary:
        lines.append(summary)

    arg_lines: list[str] = []
    for p in param_order:
        desc = parsed_param_docs.get(p)
        if desc is None:
            hint = _meta_hint(meta_by_name.get(p))
            desc = hint
        if desc:
            arg_lines.append(f"    {p}: {desc}")

    if arg_lines:
        if lines:
            lines.append("")
        lines.append("引数:")
        lines.extend(arg_lines)

    return lines


def _render_method(
    *,
    indent: str,
    name: str,
    return_type: str,
    params: list[str],
    doc_lines: list[str],
) -> str:
    lines: list[str] = []

    if params:
        sig_params = "*, " + ", ".join(params)
    else:
        sig_params = ""

    comma = ", " if sig_params else ""
    lines.append(f"{indent}def {name}(self{comma}{sig_params}) -> {return_type}:\n")

    if doc_lines:
        body_indent = indent + " " * 4
        lines.append(f'{body_indent}"""\n')
        for dl in doc_lines:
            if dl:
                lines.append(f"{body_indent}{dl}\n")
            else:
                lines.append("\n")
        lines.append(f'{body_indent}"""\n')

    lines.append(f"{indent}    ...\n")
    return "".join(lines)


def _render_g_protocol(primitive_names: list[str]) -> str:
    lines: list[str] = []
    lines.append("class _G(Protocol):\n")

    lines.append("    def __call__(self, name: str | None = None) -> _G:\n")
    lines.append('        """ラベル付き primitive 名前空間を返す。"""\n')
    lines.append("        ...\n")

    from grafix.core.primitive_registry import primitive_registry  # type: ignore[import]

    for prim in primitive_names:
        meta = primitive_registry.get_meta(prim)
        meta_by_name: dict[str, Any] = dict(meta)
        param_order = list(meta_by_name.keys())

        params: list[str] = []
        if meta_by_name:
            for p in param_order:
                pm = meta_by_name[p]
                kind = str(getattr(pm, "kind", ""))
                params.append(f"{p}: {_type_for_kind(kind)} = ...")
        else:
            params = ["**params: Any"]

        impl = _resolve_impl_callable("primitive", prim)
        if impl is not None:
            parsed_summary, parsed_docs = _parse_numpy_doc(inspect.getdoc(impl) or "")
        else:
            parsed_summary, parsed_docs = (None, {})
        doc_lines = _render_docstring(
            summary=parsed_summary,
            param_order=[p for p in param_order if _is_valid_identifier(p)],
            parsed_param_docs=parsed_docs,
            meta_by_name=meta_by_name,
        )

        lines.append(
            _render_method(
                indent="    ",
                name=prim,
                return_type="Geometry",
                params=params,
                doc_lines=doc_lines,
            )
        )

    lines.append("\n")
    return "".join(lines)


def _render_effect_builder_protocol(effect_names: list[str]) -> str:
    lines: list[str] = []
    lines.append("class _EffectBuilder(Protocol):\n")
    lines.append("    def __call__(self, geometry: Geometry, *more_geometries: Geometry) -> Geometry:\n")
    lines.append('        """保持している effect 列を Geometry に適用する。"""\n')
    lines.append("        ...\n")

    from grafix.core.effect_registry import effect_registry  # type: ignore[import]

    for eff in effect_names:
        impl = _resolve_impl_callable("effect", eff)

        meta = effect_registry.get_meta(eff)
        meta_by_name: dict[str, Any] = dict(meta)
        param_order = list(meta_by_name.keys())

        params: list[str] = []
        if meta_by_name:
            for p in param_order:
                pm = meta_by_name[p]
                type_str = _type_str_for_effect_param(impl=impl, param_name=p, meta=pm)
                params.append(f"{p}: {type_str} = ...")
        else:
            params = ["**params: Any"]

        if impl is not None:
            parsed_summary, parsed_docs = _parse_numpy_doc(inspect.getdoc(impl) or "")
        else:
            parsed_summary, parsed_docs = (None, {})
        doc_lines = _render_docstring(
            summary=parsed_summary,
            param_order=[p for p in param_order if _is_valid_identifier(p)],
            parsed_param_docs=parsed_docs,
            meta_by_name=meta_by_name,
        )

        lines.append(
            _render_method(
                indent="    ",
                name=eff,
                return_type="_EffectBuilder",
                params=params,
                doc_lines=doc_lines,
            )
        )

    lines.append("\n")
    return "".join(lines)


def _render_e_protocol(effect_names: list[str]) -> str:
    lines: list[str] = []
    lines.append("class _E(Protocol):\n")

    lines.append("    def __call__(self, name: str | None = None) -> _E:\n")
    lines.append('        """ラベル付き effect 名前空間を返す。"""\n')
    lines.append("        ...\n")

    from grafix.core.effect_registry import effect_registry  # type: ignore[import]

    for eff in effect_names:
        impl = _resolve_impl_callable("effect", eff)

        meta = effect_registry.get_meta(eff)
        meta_by_name: dict[str, Any] = dict(meta)
        param_order = list(meta_by_name.keys())

        params: list[str] = []
        if meta_by_name:
            for p in param_order:
                pm = meta_by_name[p]
                type_str = _type_str_for_effect_param(impl=impl, param_name=p, meta=pm)
                params.append(f"{p}: {type_str} = ...")
        else:
            params = ["**params: Any"]

        if impl is not None:
            parsed_summary, parsed_docs = _parse_numpy_doc(inspect.getdoc(impl) or "")
        else:
            parsed_summary, parsed_docs = (None, {})
        doc_lines = _render_docstring(
            summary=parsed_summary,
            param_order=[p for p in param_order if _is_valid_identifier(p)],
            parsed_param_docs=parsed_docs,
            meta_by_name=meta_by_name,
        )

        lines.append(
            _render_method(
                indent="    ",
                name=eff,
                return_type="_EffectBuilder",
                params=params,
                doc_lines=doc_lines,
            )
        )

    lines.append("\n")
    return "".join(lines)


def _render_l_protocol() -> str:
    lines: list[str] = []
    lines.append("class _L(Protocol):\n")
    lines.append(
        "    def __call__(\n"
        "        self,\n"
        "        geometry_or_list: Geometry | Sequence[Geometry],\n"
        "        *,\n"
        "        color: Vec3 | None = ...,\n"
        "        thickness: float | None = ...,\n"
        "        name: str | None = ...,\n"
        "    ) -> list[Layer]:\n"
    )
    lines.append('        """単体/複数の Geometry から Layer を生成する。"""\n')
    lines.append("        ...\n\n")
    return "".join(lines)


def generate_stubs_str() -> str:
    """`src/grafix/api/__init__.pyi` の生成結果を文字列として返す。"""
    repo_root = _repo_root()
    _ensure_src_on_syspath(repo_root)

    # public API 起点で import し、registry を初期化する。
    importlib.import_module("grafix.api.primitives")
    importlib.import_module("grafix.api.effects")
    importlib.import_module("grafix.api.layers")

    from grafix.core.primitive_registry import primitive_registry  # type: ignore[import]
    from grafix.core.effect_registry import effect_registry  # type: ignore[import]

    primitive_names = sorted(
        name
        for name, _ in primitive_registry.items()
        if _is_valid_identifier(name)
        and not name.startswith("_")
        and _resolve_impl_callable("primitive", name) is not None
    )
    effect_names = sorted(
        name
        for name, _ in effect_registry.items()
        if _is_valid_identifier(name)
        and not name.startswith("_")
        and _resolve_impl_callable("effect", name) is not None
    )

    header = (
        "# This file is auto-generated by tools/gen_g_stubs.py. DO NOT EDIT.\n"
        "# Regenerate with: python -m tools.gen_g_stubs\n\n"
        "# ruff: noqa: F401, E402\n\n"
    )

    lines: list[str] = [header]
    lines.append("from __future__ import annotations\n\n")
    lines.append("from collections.abc import Callable, Sequence\n")
    lines.append("from pathlib import Path\n")
    lines.append("from typing import Any, Protocol, TypeAlias\n\n")

    lines.append("from grafix.core.geometry import Geometry\n")
    lines.append("from grafix.core.layer import Layer\n")
    lines.append("from grafix.core.scene import SceneItem\n\n")

    lines.append("Vec3: TypeAlias = tuple[float, float, float]\n\n")

    lines.append(_render_g_protocol(primitive_names))
    lines.append(_render_effect_builder_protocol(effect_names))
    lines.append(_render_e_protocol(effect_names))
    lines.append(_render_l_protocol())

    lines.append("G: _G\n")
    lines.append("E: _E\n")
    lines.append("L: _L\n\n")

    # 実行時 API と整合する再エクスポート
    lines.append("from grafix.api.export import Export as Export\n")
    lines.append("from grafix.api.preset import preset as preset\n")
    lines.append("from grafix.core.effect_registry import effect as effect\n")
    lines.append("from grafix.core.primitive_registry import primitive as primitive\n\n")

    # `grafix.api.__init__.py` は遅延 import だが、型はここで固定する。
    lines.append(
        "def run(\n"
        "    draw: Callable[[float], SceneItem],\n"
        "    *,\n"
        "    config_path: str | Path | None = ...,\n"
        "    run_id: str | None = ...,\n"
        "    background_color: Vec3 = ...,\n"
        "    line_thickness: float = ...,\n"
        "    line_color: Vec3 = ...,\n"
        "    render_scale: float = ...,\n"
        "    canvas_size: tuple[int, int] = ...,\n"
        "    parameter_gui: bool = ...,\n"
        "    parameter_persistence: bool = ...,\n"
        "    midi_port_name: str | None = ...,\n"
        "    midi_mode: str = ...,\n"
        "    n_worker: int = ...,\n"
        "    fps: float = ...,\n"
        ") -> None:\n"
        '    """pyglet ウィンドウを生成し `draw(t)` のシーンをリアルタイム描画する。"""\n'
        "    ...\n\n"
    )

    lines.append(
        "__all__ = ['E', 'Export', 'G', 'L', 'effect', 'preset', 'primitive', 'run']\n"
    )
    return "".join(lines)


def main() -> None:
    content = generate_stubs_str()
    repo_root = _repo_root()
    out_path = repo_root / "src" / "grafix" / "api" / "__init__.pyi"
    out_path.write_text(content, encoding="utf-8")
    print(f"Wrote {out_path}")  # noqa: T201


if __name__ == "__main__":
    main()
