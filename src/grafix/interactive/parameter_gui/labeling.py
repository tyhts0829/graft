# どこで: `src/grafix/interactive/parameter_gui/labeling.py`。
# 何を: GUI 表示用のヘッダ名/行ラベルを生成する純粋関数を提供する。
# なぜ: imgui 描画から分離し、衝突解消や整形をユニットテスト可能にするため。

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Hashable, Mapping
from typing import TypeVar

from grafix.core.parameters.key import ParameterKey

GroupKey = tuple[str, int]
K = TypeVar("K", bound=Hashable)


def format_param_row_label(op: str, ordinal: int, arg: str) -> str:
    """パラメータ行の表示ラベル `"{op}#{ordinal} {arg}"` を返す。"""

    return f"{op}#{int(ordinal)} {arg}"


def format_layer_style_row_label(layer_name: str, ordinal: int, arg: str) -> str:
    """Layer style 行の表示ラベル `"{layer_name}#{ordinal} {arg}"` を返す。"""

    return f"{layer_name}#{int(ordinal)} {arg}"


def dedup_display_names_in_order(items: list[tuple[K, str]]) -> dict[K, str]:
    """同名がある場合だけ `name#N` を付与して表示名を返す。

    - 衝突解消は表示専用で、永続化される label そのものは変更しない。
    - 連番は `items` の順序に従う。
    """

    counts = Counter(name for _key, name in items)
    seen: dict[str, int] = defaultdict(int)

    out: dict[K, str] = {}
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
    display_order_by_group: Mapping[tuple[str, str], int] | None = None,
) -> dict[GroupKey, str]:
    """snapshot から Primitive 用のヘッダ表示名（衝突解消済み）を作る。"""

    base_name_by_group: dict[GroupKey, str] = {}
    site_id_by_group: dict[GroupKey, str] = {}
    for key, (_meta, _state, ordinal, label) in snapshot.items():
        if not is_primitive_op(str(key.op)):
            continue
        group_key = (str(key.op), int(ordinal))
        if group_key in base_name_by_group:
            continue
        base_name_by_group[group_key] = str(label) if label else str(key.op)
        site_id_by_group[group_key] = str(key.site_id)

    def _sort_key(group_key: GroupKey) -> tuple[int, str, int]:
        op, ordinal = group_key
        site_id = site_id_by_group.get(group_key, "")
        order = 10**9
        if display_order_by_group is not None:
            order = int(display_order_by_group.get((str(op), str(site_id)), 10**9))
        return (int(order), str(op), int(ordinal))

    ordered = [(k, base_name_by_group[k]) for k in sorted(base_name_by_group, key=_sort_key)]
    return dedup_display_names_in_order(ordered)


EffectStepKey = tuple[str, str]  # (op, site_id)


def effect_step_ordinals_by_site(
    step_info_by_site: Mapping[EffectStepKey, tuple[str, int]],
) -> dict[EffectStepKey, int]:
    """同一チェーン内の “同一 op の出現回数” でステップ連番を計算する。"""

    steps_by_chain: dict[str, list[tuple[int, str, str]]] = {}
    for (op, site_id), (chain_id, step_index) in step_info_by_site.items():
        steps_by_chain.setdefault(str(chain_id), []).append(
            (int(step_index), op, site_id)
        )

    out: dict[EffectStepKey, int] = {}
    for chain_id, steps in steps_by_chain.items():
        counts: dict[str, int] = defaultdict(int)
        for _step_index, op, site_id in sorted(steps):
            counts[op] += 1
            out[(op, site_id)] = int(counts[op])
    return out


def effect_chain_header_display_names_from_snapshot(
    snapshot: Mapping[ParameterKey, tuple[object, object, int, str | None]],
    *,
    step_info_by_site: Mapping[EffectStepKey, tuple[str, int]],
    display_order_by_group: Mapping[tuple[str, str], int],
    is_effect_op: Callable[[str], bool],
) -> dict[str, str]:
    """snapshot から Effect チェーン用のヘッダ表示名（衝突解消済み）を作る。"""

    # chain_id ごとの「明示ラベル（あれば）」を集める。
    # - E(name=...) が付いている場合: label をヘッダ名として採用
    # - そうでない場合: 無名チェーンとして扱う
    label_by_chain: dict[str, str | None] = {}
    for key, (_meta, _state, _ordinal, label) in snapshot.items():
        op = str(key.op)
        if not is_effect_op(op):
            continue
        step = step_info_by_site.get((op, str(key.site_id)))
        if step is None:
            continue
        chain_id, _step_index = step
        if chain_id in label_by_chain:
            continue
        label_by_chain[chain_id] = None if label is None else str(label)

    # 表示順は “コード順（観測順）” に寄せる。
    chain_min_display_order: dict[str, int] = {}
    for (op, site_id), (chain_id, _step_index) in step_info_by_site.items():
        order = int(display_order_by_group.get((str(op), str(site_id)), 10**9))
        prev = chain_min_display_order.get(str(chain_id))
        if prev is None or order < prev:
            chain_min_display_order[str(chain_id)] = int(order)

    chain_ids_sorted = sorted(
        label_by_chain.keys(),
        key=lambda cid: (int(chain_min_display_order.get(str(cid), 10**9)), str(cid)),
    )

    # effect#N は “無名チェーンだけ” を対象に 1..K へ正規化する。
    # これにより、名前付きチェーンが存在しても無名は必ず effect#1 から始まる。
    unnamed_count = 0
    base_name_by_chain: dict[str, str] = {}
    for chain_id in chain_ids_sorted:
        label = label_by_chain.get(chain_id)
        if label:
            base_name_by_chain[chain_id] = str(label)
            continue
        unnamed_count += 1
        base_name_by_chain[chain_id] = f"effect#{unnamed_count}"

    ordered = [(cid, base_name_by_chain[cid]) for cid in chain_ids_sorted]
    return dedup_display_names_in_order(ordered)
