# どこで: `src/grafix/core/parameters/codec.py`。
# 何を: ParamStore の JSON encode/decode を提供する。
# なぜ: 永続化仕様を ParamStore 本体から分離し、スキーマ変更の影響範囲を局所化するため。

from __future__ import annotations

import json
from typing import Any

from .key import ParameterKey
from .meta import ParamMeta
from .state import ParamState
from .store import ParamStore
from .view import canonicalize_ui_value


def encode_param_store(store: ParamStore) -> dict[str, Any]:
    """ParamStore を JSON 化可能な dict に変換して返す。"""

    # codec は ParamStore の内部表現へアクセスする（永続化仕様を 1 箇所へ閉じるため）。
    labels = store._labels_ref().as_dict()
    ordinals = store._ordinals_ref().as_dict()
    effects = store._effects_ref()

    return {
        "states": [
            {
                "op": k.op,
                "site_id": k.site_id,
                "arg": k.arg,
                # 明示 kwargs は「起動時はコードが勝つ」が期待値なので、
                # override=True を永続化しない（次回起動で override=False から開始する）。
                "override": (
                    False
                    if store._explicit_by_key.get(k) is True
                    else bool(v.override)
                ),
                "ui_value": v.ui_value,
                "cc_key": v.cc_key,
            }
            # meta が無い state は GUI 対象外なので永続化しない（ゴミ state の残留防止）。
            for k, v in store._states.items()
            if k in store._meta
        ],
        "meta": [
            {
                "op": k.op,
                "site_id": k.site_id,
                "arg": k.arg,
                "kind": m.kind,
                "ui_min": m.ui_min,
                "ui_max": m.ui_max,
                "choices": list(m.choices) if m.choices is not None else None,
            }
            for k, m in store._meta.items()
        ],
        "labels": [
            {"op": op, "site_id": site_id, "label": label}
            for (op, site_id), label in labels.items()
        ],
        "ordinals": ordinals,
        "effect_steps": [
            {
                "op": op,
                "site_id": site_id,
                "chain_id": chain_id,
                "step_index": step_index,
            }
            for (op, site_id), (chain_id, step_index) in effects.step_info_by_site().items()
        ],
        "chain_ordinals": effects.chain_ordinals(),
        "explicit": [
            {
                "op": k.op,
                "site_id": k.site_id,
                "arg": k.arg,
                "explicit": bool(v),
            }
            for k, v in store._explicit_by_key.items()
        ],
        "ui": {
            "collapsed_headers": sorted(store._collapsed_headers_ref()),
        },
    }


def dumps_param_store(store: ParamStore) -> str:
    """ParamStore を JSON 文字列へ変換して返す。"""

    return json.dumps(encode_param_store(store))


def decode_param_store(obj: object) -> ParamStore:
    """JSON 由来の dict から ParamStore を復元して返す。"""

    if not isinstance(obj, dict):
        raise TypeError("ParamStore payload must be a dict")

    store = ParamStore()

    for item in obj.get("states", []):
        if not isinstance(item, dict):
            continue
        try:
            key = ParameterKey(op=str(item["op"]), site_id=str(item["site_id"]), arg=str(item["arg"]))
        except Exception:
            continue

        cc_key: int | tuple[int | None, int | None, int | None] | None
        raw_cc = item.get("cc_key")
        if isinstance(raw_cc, list) and len(raw_cc) == 3:
            a, b, c = raw_cc
            cc_tuple = (
                None if a is None else int(a),
                None if b is None else int(b),
                None if c is None else int(c),
            )
            cc_key = None if cc_tuple == (None, None, None) else cc_tuple
        elif raw_cc is None:
            cc_key = None
        else:
            cc_key = int(raw_cc)

        state = ParamState(ui_value=item.get("ui_value"), cc_key=cc_key)
        if "override" in item:
            state.override = bool(item["override"])
        store._states[key] = state

    for item in obj.get("meta", []):
        if not isinstance(item, dict):
            continue
        try:
            key = ParameterKey(op=str(item["op"]), site_id=str(item["site_id"]), arg=str(item["arg"]))
            meta = ParamMeta(
                kind=str(item["kind"]),
                ui_min=item.get("ui_min"),
                ui_max=item.get("ui_max"),
                choices=item.get("choices"),
            )
        except Exception:
            continue
        store._meta[key] = meta

    # meta が無い state は GUI 対象外なので drop する（永続化/復元の双方で汚染を止める）。
    for key, state in list(store._states.items()):
        stored_meta = store._meta.get(key)
        if stored_meta is None:
            del store._states[key]
            continue
        state.ui_value = canonicalize_ui_value(state.ui_value, stored_meta)

    labels_items: list[tuple[tuple[str, str], str]] = []
    for item in obj.get("labels", []):
        if not isinstance(item, dict):
            continue
        try:
            group = (str(item["op"]), str(item["site_id"]))
            label = str(item["label"])
        except Exception:
            continue
        labels_items.append((group, label))
    store._labels_ref().replace_from_items(labels_items)

    store._ordinals_ref().replace_from_dict(obj.get("ordinals", {}))
    store._ordinals_ref().compact_all()

    store._effects_ref().replace_from_json(
        effect_steps=obj.get("effect_steps", []),
        chain_ordinals=obj.get("chain_ordinals", {}),
    )

    for item in obj.get("explicit", []):
        if not isinstance(item, dict):
            continue
        try:
            key = ParameterKey(op=str(item["op"]), site_id=str(item["site_id"]), arg=str(item["arg"]))
        except Exception:
            continue
        store._explicit_by_key[key] = bool(item.get("explicit", False))

    ui_obj = obj.get("ui")
    if isinstance(ui_obj, dict):
        collapsed = ui_obj.get("collapsed_headers", [])
        if isinstance(collapsed, list):
            for item in collapsed:
                try:
                    store._collapsed_headers_ref().add(str(item))
                except Exception:
                    continue

    # explicit=True のキーは再起動時に override=False から開始する。
    for key, is_explicit in store._explicit_by_key.items():
        if is_explicit is True and key in store._states:
            store._states[key].override = False

    store._runtime_ref().loaded_groups = {
        (str(k.op), str(k.site_id)) for k in set(store._states) | set(store._meta)
    }

    # store_snapshot が “pure” 前提なので、ロード直後に ordinal の不足を補完する。
    ordinals = store._ordinals_ref()
    for key in store._states.keys():
        ordinals.get_or_assign(key.op, key.site_id)
    for key in store._meta.keys():
        ordinals.get_or_assign(key.op, key.site_id)
    return store


def loads_param_store(payload: str) -> ParamStore:
    """JSON 文字列から ParamStore を復元して返す。"""

    return decode_param_store(json.loads(payload))


__all__ = [
    "encode_param_store",
    "decode_param_store",
    "dumps_param_store",
    "loads_param_store",
]
