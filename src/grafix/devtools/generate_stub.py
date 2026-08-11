"""
どこで: `src/grafix/devtools/generate_stub.py`。
何を: `grafix.api` の IDE 補完用スタブを project-local に自動生成する。
なぜ: `G`/`E` が動的名前空間のため、静的解析が公開 API を把握できる形を用意するため。

主な流れ（読む順）:
- `generate_stubs_str()` が immutable catalog snapshot から primitive/effect/preset を集計する。
- 集計した名前から `_render_*_protocol()` で `Protocol` ベースの API（`G/E/L/P`）を文字列として生成する。
- `main()` が project の `typings/grafix/api/__init__.pyi` へ書き出す。

副作用:
- `generate_stubs_str()` は provenance 解決に必要な module import だけを行う。
- `main()` は `__init__.pyi` をファイル出力する。

補足:
- effect の public param の型アノテーションは「stub 側で解決可能な名前」だけを書く（自動 import 収集はしない）。
- `ParamMeta.kind == "vec3"` の場合、`tuple[float, float, float]` アノテーションでも stub は `Vec3` 表現を優先する。
"""

from __future__ import annotations

import importlib
import importlib.util
import argparse
import hashlib
import inspect
import re
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, assert_never, cast

from grafix.file_io import atomic_write_text
from grafix.core.operation_catalog import (
    OperationCatalog,
    current_operation_catalog,
)
from grafix.core.operation_declaration import OpKind
from grafix.core.parameters.meta import ParamMeta
from grafix.core.parameters.validation import ParamKind
from grafix.core.preset_catalog import PresetCatalog

if TYPE_CHECKING:
    from grafix.core.runtime_config import RuntimeConfig

DEFAULT_PROJECT_STUB_PATH = Path("typings/grafix/api/__init__.pyi")

# `G.<prim>(...)` / `E.<eff>(...)` / `P.<preset>(...)` といった
# スタブ側メソッド名に使える識別子をフィルタするための正規表現。
_VALID_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# 実装側の型注釈が `tuple[float, float, float]`（or `Tuple[...]`）でも、
# スタブでは統一して `Vec3` として表現したいので置換する。
_VEC3_TUPLE_RE = re.compile(r"(?:tuple|Tuple)\[\s*float\s*,\s*float\s*,\s*float\s*\]")

# 型文字列を厳密にパースせず、「識別子っぽいトークン」だけ拾うための正規表現。
# `list[Path]` なら `list` と `Path` が取れる想定。
_TYPE_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# スタブ（`__init__.pyi`）の import / alias だけで解決できる識別子集合。
# ここに無い名前が型注釈に含まれる場合は、保守的に `Any` へフォールバックする。
_RESOLVABLE_TYPE_IDENTS = {
    # builtins
    "bool",
    "dict",
    "float",
    "frozenset",
    "int",
    "list",
    "object",
    "set",
    "str",
    "tuple",
    "None",
    # stub imports / aliases
    "Any",
    "Callable",
    "Geometry",
    "Layer",
    "OperationInfo",
    "Path",
    "Literal",
    "Mapping",
    "SceneItem",
    "Sequence",
    "Vec3",
}

_PARAMETER_IDENTITY_STUB_PARAMS = (
    "key: str | int | None = ...",
    "instance_key: str | int | None = ...",
    "shared: bool = ...",
)

_PARAMETER_IDENTITY_STUB_DOCS = {
    "key": "コード移動後も同じパラメータグループとして扱うための semantic identity。",
    "instance_key": "loop/comprehension の反復ごとにパラメータグループを分ける identity。",
    "shared": (
        "True なら反復呼び出しで同じ semantic parameter group を意図的に共有する。"
        "instance_key とは同時指定できない。"
    ),
}


def _is_valid_identifier(name: str) -> bool:
    """`name` が Python の識別子として安全（attribute/method 名に使える）かを返す。"""
    return _VALID_IDENT_RE.match(name) is not None


def _shorten(text: str, *, limit: int = 120) -> str:
    """1 行 summary 用にテキストを短縮する。

    - 連続空白を 1 つに畳む
    - 句点 `。` があれば先頭文だけにする
    - `limit` を超える場合は末尾を `…` で切る
    """
    t = " ".join(text.split())
    if "。" in t:
        t = t.split("。", 1)[0]
    if len(t) > limit:
        t = t[: limit - 1] + "…"
    return t


def _parse_numpy_doc(doc: str) -> tuple[str | None, dict[str, str]]:
    """NumPy スタイル docstring から summary と引数説明を抽出する。

    ここでの「NumPy スタイル」は、最低限以下を想定したかなり軽量なパーサ。

    - 先頭の非空行を summary として採用
    - `Parameters` セクション（見出し + 罫線）を探す
    - `name : type` 形式の行をパラメータ開始として扱い、後続のインデント行を説明として連結
    - 次のセクション見出しに到達したら打ち切る

    Returns
    -------
    summary:
        先頭行（見つからない場合は `None`）。
    param_docs:
        `{"param_name": "説明"}` の辞書。説明は `_shorten()` で短縮される。
    """
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
                name = m.group(1)
                current = name
                param_docs[name] = ""
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


def _meta_hint(meta: ParamMeta | None) -> str | None:
    """`ParamMeta` から、docstring 用の短いヒント文字列を作る。

    stub 側の docstring は「型」よりも「UI/値の制約」が助けになることが多いので、
    semantic metadata と既存の range/choices を寄せ集めて 1 行にする。
    """
    if meta is None:
        return None

    parts: list[str] = []
    if meta.description:
        parts.append(meta.description)
    if meta.display_name:
        parts.append(f"display {meta.display_name!r}")
    parts.append(meta.kind)

    if meta.ui_min is not None or meta.ui_max is not None:
        if meta.ui_min is not None and meta.ui_max is not None:
            parts.append(f"range [{meta.ui_min}, {meta.ui_max}]")
        elif meta.ui_min is not None:
            parts.append(f"min {meta.ui_min}")
        elif meta.ui_max is not None:
            parts.append(f"max {meta.ui_max}")

    if meta.recommended_range is not None:
        lower, upper = meta.recommended_range
        parts.append(f"recommended [{lower}, {upper}]")

    if meta.unit:
        parts.append(f"unit {meta.unit}")
    if meta.step is not None:
        parts.append(f"step {meta.step}")
    if meta.scale:
        parts.append(f"scale {meta.scale}")
    if meta.format:
        parts.append(f"format {meta.format!r}")
    if meta.category:
        parts.append(f"category {meta.category!r}")
    if meta.advanced:
        parts.append("advanced")

    if meta.choices is not None:
        choices = tuple(meta.choices)
        preview = ", ".join(map(repr, choices[:6]))
        parts.append(f"choices {{ {preview}{' …' if len(choices) > 6 else ''} }}")

    return ", ".join(parts) if parts else None


def _type_for_kind(kind: ParamKind) -> str:
    """`ParamMeta.kind` から、スタブに書く型名（文字列）を決める。"""
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
    if kind == "rgb":
        return "tuple[int, int, int]"
    assert_never(kind)


def _type_for_meta(meta: ParamMeta) -> str:
    """choice metadata は ``Literal``、それ以外は通常の kind 型へ変換する。"""

    if meta.kind == "choice":
        assert meta.choices is not None
        return f"Literal[{', '.join(repr(choice) for choice in meta.choices)}]"
    return _type_for_kind(meta.kind)


def _type_str_from_annotation(annotation: Any) -> str | None:
    """アノテーション（型オブジェクト/文字列）を `inspect` 経由で文字列化する。"""
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


def _normalize_type_str(type_str: str) -> str:
    """型文字列をスタブ側の import に寄せて正規化する。

    例: `typing.Callable` -> `Callable` / `pathlib.Path` -> `Path`。
    """
    s = str(type_str).strip()
    s = s.replace("typing.", "")
    s = s.replace("pathlib.Path", "Path")
    s = s.replace("collections.abc.Sequence", "Sequence")
    s = s.replace("collections.abc.Callable", "Callable")
    s = _VEC3_TUPLE_RE.sub("Vec3", s)
    return s


def _is_resolvable_type_str(type_str: str) -> bool:
    """`type_str` がスタブ内の import/alias だけで解決できそうかを判定する。

    厳密な型パーサではなく、識別子っぽいトークンを拾って集合 membership で判定する。
    したがって保守的（false negative はあり得る）だが、unknown name を出力しないために使う。
    """
    for ident in _TYPE_IDENT_RE.findall(type_str):
        if ident not in _RESOLVABLE_TYPE_IDENTS:
            return False
    return True


def _type_str_from_impl_param(impl: Any, param_name: str) -> str | None:
    """実装関数 `impl` のシグネチャから `param_name` の型注釈を取り出す。"""
    try:
        sig = inspect.signature(impl)
    except (TypeError, ValueError):
        return None

    p = sig.parameters.get(param_name)
    if p is None:
        return None
    return _type_str_from_annotation(p.annotation)


def _type_str_for_code_owned_param(*, impl: Any, param_name: str) -> str:
    """GUI metadataを持たないcode-owned引数の公開annotationを返す。"""

    type_str = _type_str_from_impl_param(impl, param_name)
    if type_str is None:
        return "Any"
    normalized = _normalize_type_str(type_str)
    return normalized if _is_resolvable_type_str(normalized) else "Any"


def _operation_param_order(spec: Any) -> list[str]:
    """wrapper引数の後に元callable順の引数を並べる。"""

    accepted = [name for name in spec.accepted_args if _is_valid_identifier(name)]
    wrapper_owned = [
        name
        for name in spec.schema.param_order
        if name not in spec.accepted_args and _is_valid_identifier(name)
    ]
    return list(dict.fromkeys((*wrapper_owned, *accepted)))


def _type_str_for_effect_param(
    *,
    impl: Any,
    param_name: str,
    meta: ParamMeta,
) -> str:
    """effect の param に対する型文字列を決める。

    - 実装関数の型注釈（文字列化）を優先
    - 取れなければ `ParamMeta.kind` からのフォールバック型
    - `vec3` は `tuple[float, float, float]` でも `Vec3` に寄せる
    """
    fallback = _type_for_meta(meta)
    if meta.kind == "choice":
        return fallback

    type_str = _type_str_from_impl_param(impl, param_name)
    if type_str is None:
        return fallback

    if meta.kind == "vec3":
        type_str = _VEC3_TUPLE_RE.sub("Vec3", type_str)
    return type_str


def _type_str_for_preset_param(
    *,
    impl: Any,
    param_name: str,
    meta: ParamMeta,
) -> str:
    """preset 関数の param に対する型文字列を決める。

    preset は user code 由来のため、スタブ側で解決不能な型名を出力しないように
    `_normalize_type_str()` + `_is_resolvable_type_str()` でフィルタし、ダメなら `Any` に倒す。
    """
    fallback = _type_for_meta(meta)
    if meta.kind == "choice":
        return fallback

    type_str = _type_str_from_impl_param(impl, param_name)
    if type_str is None:
        return fallback

    type_str_norm = _normalize_type_str(type_str)
    if not _is_resolvable_type_str(type_str_norm):
        return fallback
    return type_str_norm


def _resolve_impl_callable(
    kind: str,
    name: str,
    *,
    catalog: OperationCatalog | None = None,
) -> Any:
    """catalog entry の ``module:qualname`` provenance から実装 callable を解決する。"""

    if kind not in {"primitive", "effect"}:
        raise ValueError(f"unknown kind: {kind!r}")
    selected_catalog = current_operation_catalog() if catalog is None else catalog
    if type(selected_catalog) is not OperationCatalog:
        raise TypeError("catalog は exact OperationCatalog である必要があります")
    operation_spec = selected_catalog.resolve(cast(OpKind, kind), name)

    provenance = str(operation_spec.provenance)
    module_name, separator, qualname = provenance.partition(":")
    if not separator or not module_name or not qualname or "<locals>" in qualname:
        raise ValueError(f"{kind} {name!r} の provenance が不正です: {provenance!r}")

    try:
        value: Any = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        missing_module = str(exc.name)
        if module_name == missing_module or module_name.startswith(f"{missing_module}."):
            raise ValueError(
                f"{kind} {name!r} の provenance module が存在しません: {provenance!r}"
            ) from exc
        raise

    for part in qualname.split("."):
        try:
            value = getattr(value, part)
        except AttributeError as exc:
            raise ValueError(
                f"{kind} {name!r} の provenance attribute が存在しません: {provenance!r}"
            ) from exc
    if not callable(value):
        raise ValueError(
            f"{kind} {name!r} の provenance は callable ではありません: {provenance!r}"
        )
    return value


def _source_is_within(source: str | Path | None, roots: tuple[Path, ...]) -> bool:
    """source path がいずれかの project root 配下なら True を返す。"""

    if source is None:
        return False
    source_path = Path(source).resolve(strict=False)
    for root in roots:
        try:
            source_path.relative_to(root)
        except ValueError:
            continue
        return True
    return False


def _callable_source(value: Any) -> str | None:
    """callable の source path を取得できない場合は None を返す。"""

    try:
        return inspect.getsourcefile(inspect.unwrap(value))
    except TypeError:
        return None


def _include_operation(
    kind: str,
    name: str,
    roots: tuple[Path, ...],
    *,
    catalog: OperationCatalog,
) -> bool:
    """built-in または指定 project 配下の operation なら True を返す。"""

    if kind == "primitive":
        builtin_prefix = "grafix.core.primitives."
    elif kind == "effect":
        builtin_prefix = "grafix.core.effects."
    else:
        raise ValueError(f"unknown kind: {kind!r}")
    operation_spec = catalog.resolve(cast(OpKind, kind), name)
    module_name = str(operation_spec.provenance).partition(":")[0]
    return module_name.startswith(builtin_prefix) or _source_is_within(
        operation_spec.source,
        roots,
    )


def _render_docstring(
    *,
    summary: str | None,
    param_order: list[str],
    parsed_param_docs: dict[str, str],
    meta_by_name: dict[str, ParamMeta],
) -> list[str]:
    """stub のメソッド docstring（複数行）を組み立てる。

    非空の ``ParamMeta.description`` は registry metadata を source of truth とする。
    description が無い引数は ``inspect.getdoc()`` の抽出結果を優先し、
    それも無ければ registry meta からのヒントを補う。
    返す行は「インデント無し」。`_render_method()` 側でインデントを付ける。
    """
    lines: list[str] = []
    if summary:
        lines.append(summary)

    arg_lines: list[str] = []
    for p in param_order:
        meta = meta_by_name.get(p)
        if meta is not None and meta.description and meta.description.strip():
            desc = _meta_hint(meta)
        else:
            desc = parsed_param_docs.get(p)
            if desc is None:
                desc = _meta_hint(meta)
        if desc:
            arg_lines.append(f"    {p}: {desc}")

    if arg_lines:
        if lines:
            lines.append("")
        lines.append("引数:")
        lines.extend(arg_lines)

    return lines


def _render_operation_docstring(
    *,
    summary: str | None,
    param_order: list[str],
    parsed_param_docs: dict[str, str],
    meta_by_name: dict[str, ParamMeta],
) -> list[str]:
    """operation 引数と共通 identity 引数の説明を含む docstring を組み立てる。"""

    all_param_order = list(dict.fromkeys((*param_order, *_PARAMETER_IDENTITY_STUB_DOCS)))
    all_param_docs = {
        **parsed_param_docs,
        **_PARAMETER_IDENTITY_STUB_DOCS,
    }
    return _render_docstring(
        summary=summary,
        param_order=all_param_order,
        parsed_param_docs=all_param_docs,
        meta_by_name=meta_by_name,
    )


def _render_method(
    *,
    indent: str,
    name: str,
    return_type: str,
    params: list[str],
    doc_lines: list[str],
) -> str:
    """`Protocol` の 1 メソッド分のスタブ文字列を生成する。"""
    lines: list[str] = []

    if params:
        # このスタブ API は基本的に keyword-only パラメータ設計（`*, a=..., b=...`）。
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


def _render_g_protocol(
    primitive_names: list[str],
    *,
    catalog: OperationCatalog,
) -> str:
    """`G`（primitive 名前空間）の `Protocol` 定義を生成する。"""
    lines: list[str] = []
    lines.append("class _G(Protocol):\n")

    lines.append("    def __call__(self, name: str | None = None) -> _G:\n")
    lines.append('        """ラベル付き primitive 名前空間を返す。"""\n')
    lines.append("        ...\n")
    lines.append("    def catalog(self) -> tuple[OperationInfo, ...]:\n")
    lines.append('        """登録済み primitive の catalog を名前順で返す。"""\n')
    lines.append("        ...\n")
    lines.append("    def describe(self, name: str) -> OperationInfo:\n")
    lines.append('        """primitive の catalog entry を名前で取得する。"""\n')
    lines.append("        ...\n")
    lines.append(
        _render_method(
            indent="    ",
            name="select",
            return_type="Geometry",
            params=[
                "target: str = ...",
                "params_by_target: Mapping[str, Mapping[str, Any]] | None = ...",
                *_PARAMETER_IDENTITY_STUB_PARAMS,
            ],
            doc_lines=_render_operation_docstring(
                summary="登録済み primitive を Parameter GUI から選択して Geometry を生成する。",
                param_order=["target", "params_by_target"],
                parsed_param_docs={
                    "target": "選択する primitive 名。",
                    "params_by_target": "primitive 名ごとの base keyword 引数。",
                },
                meta_by_name={},
            ),
        )
    )

    for prim in primitive_names:
        if prim == "select":
            continue
        spec = catalog.resolve("primitive", prim)
        meta_by_name: dict[str, ParamMeta] = dict(spec.schema.meta)
        param_order = _operation_param_order(spec)
        impl = _resolve_impl_callable("primitive", prim, catalog=catalog)

        params: list[str] = []
        if param_order or meta_by_name:
            for p in param_order:
                if p in meta_by_name:
                    type_str = _type_for_meta(meta_by_name[p])
                else:
                    type_str = _type_str_for_code_owned_param(impl=impl, param_name=p)
                default = "" if p in spec.required_args else " = ..."
                params.append(f"{p}: {type_str}{default}")
            params.extend(_PARAMETER_IDENTITY_STUB_PARAMS)
            if spec.accepts_var_kwargs:
                params.append("**params: Any")
        else:
            params = [*_PARAMETER_IDENTITY_STUB_PARAMS, "**params: Any"]

        doc = inspect.getdoc(impl) or spec.doc
        parsed_summary, parsed_docs = _parse_numpy_doc(doc or "")
        doc_lines = _render_operation_docstring(
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


def _render_effect_builder_protocol(
    effect_names: list[str],
    *,
    catalog: OperationCatalog,
) -> str:
    """`E.xxx(...)` の戻り値である builder の `Protocol` 定義を生成する。"""
    lines: list[str] = []
    lines.append("class _EffectBuilder(Protocol):\n")
    lines.append(
        "    def __call__(self, geometry: Geometry, *more_geometries: Geometry) -> Geometry:\n"
    )
    lines.append('        """保持している effect 列を Geometry に適用する。"""\n')
    lines.append("        ...\n")
    lines.append(
        _render_method(
            indent="    ",
            name="select",
            return_type="_EffectBuilder",
            params=[
                "target: str = ...",
                "n_inputs: Literal[1] = ...",
                "params_by_target: Mapping[str, Mapping[str, Any]] | None = ...",
                *_PARAMETER_IDENTITY_STUB_PARAMS,
            ],
            doc_lines=_render_operation_docstring(
                summary="チェーン末尾へ unary effect selector を追加する。",
                param_order=["target", "n_inputs", "params_by_target"],
                parsed_param_docs={
                    "target": "選択する effect 名。",
                    "n_inputs": "チェーン中段では 1 のみ指定できる。",
                    "params_by_target": "effect 名ごとの base keyword 引数。",
                },
                meta_by_name={},
            ),
        )
    )

    for eff in effect_names:
        if eff == "select":
            continue
        impl = _resolve_impl_callable("effect", eff, catalog=catalog)

        spec = catalog.resolve("effect", eff)
        meta_by_name: dict[str, ParamMeta] = dict(spec.schema.meta)
        param_order = _operation_param_order(spec)

        params: list[str] = []
        if param_order or meta_by_name:
            for p in param_order:
                if p in meta_by_name:
                    type_str = _type_str_for_effect_param(
                        impl=impl,
                        param_name=p,
                        meta=meta_by_name[p],
                    )
                else:
                    type_str = _type_str_for_code_owned_param(impl=impl, param_name=p)
                default = "" if p in spec.required_args else " = ..."
                params.append(f"{p}: {type_str}{default}")
            params.extend(_PARAMETER_IDENTITY_STUB_PARAMS)
            if spec.accepts_var_kwargs:
                params.append("**params: Any")
        else:
            params = [*_PARAMETER_IDENTITY_STUB_PARAMS, "**params: Any"]

        doc = inspect.getdoc(impl) or spec.doc
        parsed_summary, parsed_docs = _parse_numpy_doc(doc or "")
        doc_lines = _render_operation_docstring(
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


def _render_e_protocol(
    effect_names: list[str],
    *,
    catalog: OperationCatalog,
) -> str:
    """`E`（effect 名前空間）の `Protocol` 定義を生成する。"""
    lines: list[str] = []
    lines.append("class _E(Protocol):\n")

    lines.append("    def __call__(self, name: str | None = None) -> _E:\n")
    lines.append('        """ラベル付き effect 名前空間を返す。"""\n')
    lines.append("        ...\n")
    lines.append("    def catalog(self) -> tuple[OperationInfo, ...]:\n")
    lines.append('        """登録済み effect の catalog を名前順で返す。"""\n')
    lines.append("        ...\n")
    lines.append("    def describe(self, name: str) -> OperationInfo:\n")
    lines.append('        """effect の catalog entry を名前で取得する。"""\n')
    lines.append("        ...\n")
    lines.append(
        _render_method(
            indent="    ",
            name="select",
            return_type="_EffectBuilder",
            params=[
                "target: str = ...",
                "n_inputs: int = ...",
                "params_by_target: Mapping[str, Mapping[str, Any]] | None = ...",
                *_PARAMETER_IDENTITY_STUB_PARAMS,
            ],
            doc_lines=_render_operation_docstring(
                summary="登録済み effect を Parameter GUI から選択して builder を生成する。",
                param_order=["target", "n_inputs", "params_by_target"],
                parsed_param_docs={
                    "target": "選択する effect 名。",
                    "n_inputs": "選択候補と適用時の入力 Geometry 数。",
                    "params_by_target": "effect 名ごとの base keyword 引数。",
                },
                meta_by_name={},
            ),
        )
    )

    for eff in effect_names:
        if eff == "select":
            continue
        impl = _resolve_impl_callable("effect", eff, catalog=catalog)

        spec = catalog.resolve("effect", eff)
        meta_by_name: dict[str, ParamMeta] = dict(spec.schema.meta)
        param_order = _operation_param_order(spec)

        params: list[str] = []
        if param_order or meta_by_name:
            for p in param_order:
                if p in meta_by_name:
                    type_str = _type_str_for_effect_param(
                        impl=impl,
                        param_name=p,
                        meta=meta_by_name[p],
                    )
                else:
                    type_str = _type_str_for_code_owned_param(impl=impl, param_name=p)
                default = "" if p in spec.required_args else " = ..."
                params.append(f"{p}: {type_str}{default}")
            params.extend(_PARAMETER_IDENTITY_STUB_PARAMS)
            if spec.accepts_var_kwargs:
                params.append("**params: Any")
        else:
            params = [*_PARAMETER_IDENTITY_STUB_PARAMS, "**params: Any"]

        doc = inspect.getdoc(impl) or spec.doc
        parsed_summary, parsed_docs = _parse_numpy_doc(doc or "")
        doc_lines = _render_operation_docstring(
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
    """`L`（Layer 生成）の `Protocol` 定義を生成する。"""
    lines: list[str] = []
    lines.append("class _L(Protocol):\n")
    lines.append(
        "    def __call__(\n        self,\n        name: str | None = None,\n    ) -> _L:\n"
    )
    lines.append('        """ラベル付き Layer 名前空間を返す。"""\n')
    lines.append("        ...\n\n")

    lines.append(
        "    def layer(\n"
        "        self,\n"
        "        geometry_or_list: Geometry | Sequence[Geometry],\n"
        "        *,\n"
        "        key: str | int | None = ...,\n"
        "        instance_key: str | int | None = ...,\n"
        "        shared: bool = ...,\n"
        "        color: Vec3 | None = ...,\n"
        "        thickness: float | None = ...,\n"
        "        gcode_optimize: bool = ...,\n"
        "    ) -> Layer:\n"
    )
    lines.append('        """単体/複数の Geometry を単一 Layer にする。"""\n')
    lines.append("        ...\n\n")
    return "".join(lines)


def _render_p_protocol(
    preset_names: list[str],
    *,
    catalog: PresetCatalog,
) -> str:
    """`P`（preset 名前空間）の `Protocol` 定義を生成する。"""
    lines: list[str] = []
    lines.append("class _P(Protocol):\n")
    lines.append(
        "    def __call__(\n"
        "        self,\n"
        "        *,\n"
        "        name: str | None = None,\n"
        "        key: str | int | None = None,\n"
        "        instance_key: str | int | None = None,\n"
        "        shared: bool = False,\n"
        "    ) -> _P:\n"
    )
    lines.append('        """ラベル付き preset 名前空間を返す。"""\n')
    lines.append("        ...\n\n")
    for preset_name in preset_names:
        if preset_name not in catalog:
            continue

        spec = catalog[preset_name]
        impl = spec.func
        meta_by_name: dict[str, ParamMeta] = dict(spec.schema.meta)
        param_order = [
            p for p in spec.schema.param_order if _is_valid_identifier(p)
        ]

        params: list[str] = []
        if meta_by_name:
            for p in param_order:
                pm = meta_by_name[p]
                type_str = _type_str_for_preset_param(impl=impl, param_name=p, meta=pm)
                params.append(f"{p}: {type_str} = ...")
        parsed_summary, parsed_docs = _parse_numpy_doc(inspect.getdoc(impl) or "")
        doc_lines = _render_docstring(
            summary=parsed_summary,
            param_order=param_order,
            parsed_param_docs=parsed_docs,
            meta_by_name=meta_by_name,
        )

        lines.append(
            _render_method(
                indent="    ",
                name=preset_name,
                return_type="SceneItem",
                params=params,
                doc_lines=doc_lines,
            )
        )

    return "".join(lines)


def generate_stubs_str(
    *,
    source_roots: Sequence[str | Path] = (),
    config: RuntimeConfig | None = None,
    operation_catalog: OperationCatalog | None = None,
    preset_catalog: PresetCatalog | None = None,
) -> str:
    """`grafix/api/__init__.pyi` の生成結果を文字列として返す。

    Parameters
    ----------
    source_roots : Sequence[str or Path], optional
        built-in に加えて stub へ含める project-local operation/preset の source root。

    Notes
    -----
    - config authoring module は隔離 candidate へ明示ロードする。
    - 注入 catalog がある場合は、その immutable snapshot だけを読む。
    - presets は config preset directory または source_roots 配下だけを採用する。
    """
    from grafix.authoring_loader import load_config_authoring_definitions
    from grafix.core.runtime_config import RuntimeConfig
    from grafix.runtime_config_loader import runtime_config

    if config is not None and not isinstance(config, RuntimeConfig):
        raise TypeError("config は RuntimeConfig または None である必要があります")
    cfg = runtime_config() if config is None else config
    loaded = (
        None
        if operation_catalog is not None and preset_catalog is not None
        else load_config_authoring_definitions(cfg)
    )
    selected_operations = operation_catalog
    if selected_operations is None:
        assert loaded is not None
        selected_operations = loaded.operations
    selected_presets = preset_catalog
    if selected_presets is None:
        assert loaded is not None
        selected_presets = loaded.presets
    if type(selected_operations) is not OperationCatalog:
        raise TypeError("operation_catalog は exact OperationCatalog である必要があります")
    if type(selected_presets) is not PresetCatalog:
        raise TypeError("preset_catalog は exact PresetCatalog である必要があります")

    roots = tuple(Path(root).expanduser().resolve(strict=False) for root in source_roots)

    # IDE 補完に載せたい public 名だけ抽出する。
    # - 先頭が `_` の名前は除外
    # - method 名に使えない識別子は除外
    # - built-in と source_roots 配下の project-local operation だけを採用
    primitive_names = sorted(
        entry.name
        for entry in selected_operations.public_entries(kind="primitive")
        if _is_valid_identifier(entry.name)
        and _include_operation(
            "primitive",
            entry.name,
            roots,
            catalog=selected_operations,
        )
    )
    effect_names = sorted(
        entry.name
        for entry in selected_operations.public_entries(kind="effect")
        if _is_valid_identifier(entry.name)
        and _include_operation(
            "effect",
            entry.name,
            roots,
            catalog=selected_operations,
        )
    )

    preset_roots = tuple(
        Path(directory).resolve(strict=False)
        for directory in cfg.preset_module_dirs
        if Path(directory).resolve(strict=False).is_dir()
    )
    preset_entries = tuple(
        (declaration.name, declaration.func) for declaration in selected_presets.declarations()
    )
    preset_names = sorted(
        name
        for name, fn in preset_entries
        if _is_valid_identifier(name)
        and not name.startswith("_")
        and (
            (
                _source_is_within(_callable_source(fn), preset_roots)
                and (not roots or _source_is_within(_callable_source(fn), roots))
            )
            or _source_is_within(_callable_source(fn), roots)
        )
    )

    # 生成物の先頭には「自動生成」ヘッダと lint 抑制を入れる。
    # `from __future__ import annotations` 以降は、型文字列化を簡単にするため固定の import を並べる。
    header = (
        "# This file is auto-generated by grafix.devtools.generate_stub. DO NOT EDIT.\n"
        "# Regenerate with: python -m grafix stub\n\n"
        "# ruff: noqa: F401, E402\n\n"
    )

    lines: list[str] = [header]
    lines.append("from __future__ import annotations\n\n")
    lines.append("from collections.abc import Callable, Mapping, Sequence\n")
    lines.append("from pathlib import Path\n")
    lines.append("from typing import Any, Literal, Protocol, TypeAlias\n\n")

    lines.append("from grafix.core.geometry import Geometry as Geometry\n")
    lines.append("from grafix.core.layer import Layer as Layer\n")
    lines.append("from grafix.api.operation_info import OperationInfo as OperationInfo\n")
    lines.append("from grafix.core.scene import SceneItem\n\n")

    lines.append("Vec3: TypeAlias = tuple[float, float, float]\n\n")

    lines.append(_render_g_protocol(primitive_names, catalog=selected_operations))
    lines.append(_render_effect_builder_protocol(effect_names, catalog=selected_operations))
    lines.append(_render_e_protocol(effect_names, catalog=selected_operations))
    lines.append(_render_l_protocol())
    lines.append(_render_p_protocol(preset_names, catalog=selected_presets))

    lines.append("G: _G\n")
    lines.append("E: _E\n")
    lines.append("L: _L\n\n")
    lines.append("P: _P\n\n")

    # 実行時の ``grafix.api`` と整合する authoring surface / public value type。
    lines.append("from grafix.api.cc import CcView as CcView\n")
    lines.append(
        "from grafix.api.render import ("
        "CaptureProvenance as CaptureProvenance, Color as Color, "
        "ColorInput as ColorInput, ConfigProvenance as ConfigProvenance, "
        "ExportFormat as ExportFormat, ExportResult as ExportResult, Frame as Frame, "
        "FrameProvenance as FrameProvenance, FrameStyle as FrameStyle, "
        "GitProvenance as GitProvenance, LoadProvenance as LoadProvenance, "
        "ParameterLoadMode as ParameterLoadMode, "
        "ParameterLoadState as ParameterLoadState, "
        "ParameterSnapshotProvenance as ParameterSnapshotProvenance, "
        "ParamStoreLoadDiagnostic as ParamStoreLoadDiagnostic, "
        "RGB01 as RGB01, RGB8 as RGB8, RealizedLayer as RealizedLayer, "
        "RenderOptions as RenderOptions, "
        "RenderSession as RenderSession, "
        "RenderSessionMetadata as RenderSessionMetadata, "
        "RuntimeConfig as RuntimeConfig, SessionProvenance as SessionProvenance, "
        "SourceProvenance as SourceProvenance)\n"
    )
    lines.append(
        "from grafix.export.variation_batch import ("
        "VariationBatchResult as VariationBatchResult, "
        "VariationRenderResult as VariationRenderResult, "
        "VariationRenderStatus as VariationRenderStatus)\n"
    )
    lines.append("from grafix.api.preset import preset as preset\n")
    lines.append(
        "from grafix.core.gcode_params import GCodeParams as GCodeParams\n"
    )
    lines.append("from grafix.core.operation_authoring import effect as effect\n")
    lines.append("from grafix.core.operation_authoring import primitive as primitive\n")
    lines.append(
        "from grafix.core.parameters.meta import ParamMeta as ParamMeta\n"
    )
    lines.append(
        "from grafix.core.realize import GeometryCacheKey as GeometryCacheKey\n"
    )
    lines.append(
        "from grafix.core.realized_geometry import "
        "RealizedGeometry as RealizedGeometry\n"
    )
    lines.append(
        "from grafix.core.resource_budget import ResourceBudget as ResourceBudget, "
        "ResourceLimitError as ResourceLimitError\n\n"
    )
    lines.append(
        "from grafix.core.runtime_limits import ("
        "RuntimeLimitProfiles as RuntimeLimitProfiles, "
        "RuntimeLimits as RuntimeLimits)\n\n"
    )
    lines.append(
        "__all__ = ['CaptureProvenance', 'CcView', 'Color', 'ColorInput', "
        "'ConfigProvenance', 'E', 'ExportFormat', 'ExportResult', 'Frame', "
        "'FrameProvenance', 'FrameStyle', 'G', 'GCodeParams', 'Geometry', "
        "'GeometryCacheKey', 'GitProvenance', 'L', 'Layer', "
        "'LoadProvenance', 'OperationInfo', 'P', 'ParamMeta', 'ParameterLoadMode', "
        "'ParameterLoadState', 'ParameterSnapshotProvenance', "
        "'ParamStoreLoadDiagnostic', 'RGB01', 'RGB8', 'RealizedGeometry', "
        "'RealizedLayer', 'RenderOptions', "
        "'RenderSession', 'RenderSessionMetadata', "
        "'ResourceBudget', 'ResourceLimitError', 'RuntimeLimitProfiles', "
        "'RuntimeLimits', 'RuntimeConfig', 'SceneItem', 'SessionProvenance', "
        "'SourceProvenance', 'VariationBatchResult', 'VariationRenderResult', "
        "'VariationRenderStatus', 'effect', 'preset', 'primitive']\n"
    )
    return "".join(lines)


@contextmanager
def _project_import_path(project_root: Path) -> Iterator[None]:
    """project root を import path の先頭へ一時追加する。"""

    root_text = str(project_root)
    sys.path.insert(0, root_text)
    importlib.invalidate_caches()
    try:
        yield
    finally:
        try:
            sys.path.remove(root_text)
        except ValueError:
            pass


def _import_project_target(project_root: Path, target: str) -> None:
    """dotted module または project-relative Python file を import する。"""

    candidate = Path(target).expanduser()
    if not candidate.is_absolute():
        candidate = project_root / candidate
    if not candidate.is_file():
        importlib.import_module(str(target))
        return

    resolved = candidate.resolve(strict=True)
    token = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12]
    module_name = f"grafix_project_module_{token}"
    if module_name in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(module_name, resolved)
    if spec is None or spec.loader is None:
        raise ImportError(f"project module を読み込めません: {resolved}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise


_ROOT_STUB = """from grafix.api import E as E, G as G, L as L, P as P
from grafix.api.cc import CcView as CcView, cc as cc
from grafix.api.export import save as save
from grafix.api.operation_info import OperationInfo as OperationInfo
from grafix.api.preset import preset as preset
from grafix.api.render import Frame as Frame, RenderSession as RenderSession, RenderSessionMetadata as RenderSessionMetadata, render as render
from grafix.api.runner import run as run
from grafix.api.variation_batch import render_variation_batch as render_variation_batch
from grafix.core.canvas_sizes import A2 as A2, A2_LANDSCAPE as A2_LANDSCAPE, A3 as A3, A3_LANDSCAPE as A3_LANDSCAPE, A4 as A4, A4_LANDSCAPE as A4_LANDSCAPE, A5 as A5, A5_LANDSCAPE as A5_LANDSCAPE, A6 as A6, A6_LANDSCAPE as A6_LANDSCAPE, SQUARE as SQUARE
from grafix.core.capture_provenance import CaptureProvenance as CaptureProvenance, SessionProvenance as SessionProvenance
from grafix.core.export_format import ExportFormat as ExportFormat
from grafix.core.export_result import ExportResult as ExportResult
from grafix.core.operation_authoring import effect as effect, primitive as primitive
from grafix.core.parameters.runtime import LoadProvenance as LoadProvenance, ParameterLoadState as ParameterLoadState, ParamStoreLoadDiagnostic as ParamStoreLoadDiagnostic
from grafix.core.parameters.source import ParameterLoadMode as ParameterLoadMode
from grafix.core.parameters.style_resolver import FrameStyle as FrameStyle
from grafix.core.pipeline import RealizedLayer as RealizedLayer
from grafix.core.render_options import Color as Color, ColorInput as ColorInput, RGB01 as RGB01, RGB8 as RGB8, RenderOptions as RenderOptions
from grafix.core.resource_budget import ResourceBudget as ResourceBudget, ResourceLimitError as ResourceLimitError
from grafix.core.runtime_config import RuntimeConfig as RuntimeConfig
from grafix.core.runtime_limits import RuntimeLimitProfiles as RuntimeLimitProfiles, RuntimeLimits as RuntimeLimits
from grafix.export.variation_batch import VariationBatchResult as VariationBatchResult, VariationRenderResult as VariationRenderResult, VariationRenderStatus as VariationRenderStatus

__all__ = [
    "A2",
    "A2_LANDSCAPE",
    "A3",
    "A3_LANDSCAPE",
    "A4",
    "A4_LANDSCAPE",
    "A5",
    "A5_LANDSCAPE",
    "A6",
    "A6_LANDSCAPE",
    "CaptureProvenance",
    "CcView",
    "Color",
    "ColorInput",
    "E",
    "ExportFormat",
    "ExportResult",
    "Frame",
    "FrameStyle",
    "G",
    "L",
    "LoadProvenance",
    "OperationInfo",
    "P",
    "ParameterLoadMode",
    "ParameterLoadState",
    "ParamStoreLoadDiagnostic",
    "RGB01",
    "RGB8",
    "RealizedLayer",
    "RenderOptions",
    "RenderSession",
    "RenderSessionMetadata",
    "ResourceBudget",
    "ResourceLimitError",
    "RuntimeConfig",
    "RuntimeLimitProfiles",
    "RuntimeLimits",
    "SQUARE",
    "SessionProvenance",
    "VariationBatchResult",
    "VariationRenderResult",
    "VariationRenderStatus",
    "cc",
    "effect",
    "preset",
    "primitive",
    "render",
    "render_variation_batch",
    "run",
    "save",
]
"""


def _project_output_path(project_root: Path, output: Path | None) -> Path:
    if output is None:
        return project_root / DEFAULT_PROJECT_STUB_PATH
    expanded = output.expanduser()
    if not expanded.is_absolute():
        expanded = project_root / expanded
    return expanded.resolve(strict=False)


def main(argv: list[str] | None = None) -> int:
    """project-local な ``grafix.api`` stub を生成する。

    Parameters
    ----------
    argv : list[str] or None, optional
        CLI 引数。None の場合は ``sys.argv`` を使う。

    Returns
    -------
    int
        生成成功時は 0、project module の import 失敗時は 2。

    Notes
    -----
    既定出力は ``<project>/typings/grafix/api/__init__.pyi`` であり、installed
    package は変更しない。``--output`` を指定した場合だけ任意 file へ出力する。
    """

    parser = argparse.ArgumentParser(prog="python -m grafix stub")
    parser.add_argument("--project", type=Path, default=Path.cwd(), help="project root")
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="出力先（相対pathはproject root基準）",
    )
    parser.add_argument(
        "--import",
        "--module",
        dest="imports",
        action="append",
        default=[],
        help="事前importする dotted module または project-relative .py（複数指定可）",
    )
    parser.add_argument(
        "--no-default-import",
        action="store_true",
        help="sketch.main の自動importを無効にする",
    )
    parser.add_argument("--config", type=Path, help="config.yaml（相対pathはproject root基準）")
    args = parser.parse_args(argv)

    project_root = args.project.expanduser().resolve(strict=False)
    output_path = _project_output_path(project_root, args.output)
    config_path = args.config
    if config_path is None:
        discovered = project_root / ".grafix" / "config.yaml"
        config_path = discovered if discovered.is_file() else None
    elif not config_path.is_absolute():
        config_path = project_root / config_path

    from grafix.core.runtime_config import bind_runtime_config
    from grafix.runtime_config_loader import load_runtime_config

    targets = list(args.imports)
    if not args.no_default_import and (project_root / "sketch" / "main.py").is_file():
        targets.insert(0, "sketch/main.py")

    try:
        config = load_runtime_config(config_path)
        with bind_runtime_config(config), _project_import_path(project_root):
            for target in targets:
                _import_project_target(project_root, str(target))
            content = generate_stubs_str(
                source_roots=(project_root,),
                config=config,
            )
    except Exception as exc:
        print(
            f"project-local stub の生成に失敗しました: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )  # noqa: T201
        return 2

    atomic_write_text(output_path, content)
    if args.output is None:
        # 標準 MYPYPATH/stubPath で ``from grafix import G`` も解決できる root proxy。
        atomic_write_text(output_path.parent.parent / "__init__.pyi", _ROOT_STUB)
    print(f"Wrote {output_path}")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
