# どこで: `src/app/parameter_gui/labeling.py`。
# 何を: GUI 表示用のヘッダ名/行ラベルを生成する純粋関数を提供する。
# なぜ: imgui 描画から分離し、衝突解消や整形をユニットテスト可能にするため。

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Mapping

from src.parameters.key import ParameterKey

GroupKey = tuple[str, int]


def format_param_row_label(op: str, ordinal: int, arg: str) -> str:
    """パラメータ行の表示ラベル `"{op}#{ordinal} {arg}"` を返す。"""

    return f"{op}#{int(ordinal)} {arg}"


def dedup_display_names_in_order(
    items: list[tuple[GroupKey, str]],
) -> dict[GroupKey, str]:
    """同名がある場合だけ `name#N` を付与して表示名を返す。

    - 衝突解消は表示専用で、永続化される label そのものは変更しない。
    - 連番は `items` の順序に従う。
    """

    counts = Counter(name for _key, name in items)
    seen: dict[str, int] = defaultdict(int)

    out: dict[GroupKey, str] = {}
    for key, name in items:
        if counts[name] <= 1:
            out[key] = name
            continue
        seen[name] += 1
        out[key] = f"{name}#{seen[name]}"
    return out


def primitive_header_display_names_from_snapshot(
    snapshot: Mapping[ParameterKey, tuple[object, object, int, str | None]],
    *,
    is_primitive_op: Callable[[str], bool],
) -> dict[GroupKey, str]:
    """snapshot から Primitive 用のヘッダ表示名（衝突解消済み）を作る。"""

    base_name_by_group: dict[GroupKey, str] = {}
    for key, (_meta, _state, ordinal, label) in snapshot.items():
        if not is_primitive_op(str(key.op)):
            continue
        group_key = (str(key.op), int(ordinal))
        if group_key in base_name_by_group:
            continue
        base_name_by_group[group_key] = str(label) if label else str(key.op)

    ordered = [(k, base_name_by_group[k]) for k in sorted(base_name_by_group)]
    return dedup_display_names_in_order(ordered)

