"""ParamStore の現行 schema に限定した JSON codec。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .collapsed_header import encode_collapsed_header_key
from .codec_parser import (
    PARAM_STORE_SCHEMA_VERSION,
    ParamStoreDecodeIssue,
    ParamStoreSchemaError,
    ParsedParamStore,
    UnsupportedParamStoreSchemaError,
    param_store_schema_version,
    parse_param_store_payload,
)
from .meta_spec import meta_to_spec
from .store import ParamStore
from .variations import Variation


@dataclass(frozen=True, slots=True)
class ParamStoreDecodeResult:
    """復元した store と、除外・修復した entry の診断。"""

    store: ParamStore
    issues: tuple[ParamStoreDecodeIssue, ...] = ()


def encode_param_store(
    store: ParamStore,
    *,
    preserve_explicit_overrides: bool = False,
) -> dict[str, Any]:
    """ParamStore を現行 schema の JSON 化可能な dict へ変換する。"""

    labels = store._labels_ref().as_dict()
    effects = store._effects_ref()
    adjustments = store.capture_adjustment_snapshot()
    persisted_items = adjustments.items()
    return {
        "schema_version": PARAM_STORE_SCHEMA_VERSION,
        "states": [
            {
                "op": key.op,
                "site_id": key.site_id,
                "arg": key.arg,
                "override": (
                    adjustment.state.override
                    if preserve_explicit_overrides
                    else (
                        False
                        if store._get_explicit_ref(key)
                        else adjustment.state.override
                    )
                ),
                "ui_value": _json_array(adjustment.state.ui_value),
                "cc_key": _json_array(adjustment.state.cc_key),
            }
            for key, adjustment in persisted_items
        ],
        "meta": [
            {
                "op": key.op,
                "site_id": key.site_id,
                "arg": key.arg,
                **meta_to_spec(adjustment.meta),
            }
            for key, adjustment in persisted_items
        ],
        "labels": [
            {"op": op, "site_id": site_id, "label": label}
            for (op, site_id), label in labels.items()
        ],
        "ordinals": store._ordinals_ref().as_dict(),
        "effect_steps": [
            {
                "op": step.op,
                "site_id": step.site_id,
                "chain_id": chain_id,
                "step_index": step.code_index,
                "n_inputs": step.n_inputs,
            }
            for chain_id, steps in effects.topologies().items()
            for step in steps
        ],
        "chain_ordinals": effects.chain_ordinals(),
        "explicit": [
            {
                "op": key.op,
                "site_id": key.site_id,
                "arg": key.arg,
                "explicit": bool(store._get_explicit_ref(key)),
            }
            for key, _adjustment in persisted_items
        ],
        "ui": {
            "collapsed_headers": [
                encode_collapsed_header_key(key)
                for key in sorted(
                    store.collapsed_headers(),
                    key=lambda item: item.sort_key(),
                )
            ],
            "effect_order_overrides": [
                {
                    "chain_id": chain_id,
                    "steps": [{"op": op, "site_id": site_id} for op, site_id in step_keys],
                }
                for chain_id, step_keys in sorted(
                    effects.order_overrides().items(),
                    key=lambda item: item[0],
                )
            ],
            "locked_parameters": [
                {"op": key.op, "site_id": key.site_id, "arg": key.arg}
                for key in sorted(
                    store._locked_keys_ref(),
                    key=lambda item: (item.op, item.site_id, item.arg),
                )
            ],
            "favorite_parameters": [
                {"op": key.op, "site_id": key.site_id, "arg": key.arg}
                for key in sorted(
                    store._favorite_keys_ref(),
                    key=lambda item: (item.op, item.site_id, item.arg),
                )
            ],
        },
        "variations": [
            _encode_variation(variation) for variation in store._variations_ref().values()
        ],
    }


def _json_array(value: Any) -> Any:
    """canonical tuple を JSON-native な array へ射影する。"""

    return list(value) if isinstance(value, tuple) else value


def _encode_variation(variation: Variation) -> dict[str, Any]:
    """Variation を schema v4 の JSON-native record へ射影する。"""

    snapshot = variation.parameter_snapshot
    topology_by_chain = dict(snapshot.effect_topology_items())
    return {
        "name": variation.name,
        "created_at": variation.created_at,
        "note": variation.note,
        "seed": variation.seed,
        "t": variation.t,
        "thumbnail_path": variation.thumbnail_path,
        "parameter_snapshot": {
            "states": [
                {
                    "op": key.op,
                    "site_id": key.site_id,
                    "arg": key.arg,
                    "override": adjustment.state.override,
                    "ui_value": _json_array(adjustment.state.ui_value),
                    "cc_key": _json_array(adjustment.state.cc_key),
                }
                for key, adjustment in snapshot.items()
            ],
            "meta": [
                {
                    "op": key.op,
                    "site_id": key.site_id,
                    "arg": key.arg,
                    **meta_to_spec(adjustment.meta),
                }
                for key, adjustment in snapshot.items()
            ],
            "collapsed_headers": [
                {
                    **encode_collapsed_header_key(key),
                    "collapsed": collapsed,
                }
                for key, collapsed in snapshot.collapsed_items()
            ],
            "effect_order_state": [
                {
                    "chain_id": chain_id,
                    "topology": [
                        {
                            "op": op,
                            "site_id": site_id,
                            "n_inputs": n_inputs,
                        }
                        for op, site_id, n_inputs in topology_by_chain.get(
                            chain_id,
                            (),
                        )
                    ],
                    "steps": (
                        None
                        if step_keys is None
                        else [
                            {"op": op, "site_id": site_id}
                            for op, site_id in step_keys
                        ]
                    ),
                }
                for chain_id, step_keys in snapshot.effect_order_items()
            ],
        },
    }


def dumps_param_store(
    store: ParamStore,
    *,
    preserve_explicit_overrides: bool = False,
) -> str:
    """ParamStore を JSON 文字列へ変換する。"""

    return json.dumps(
        encode_param_store(
            store,
            preserve_explicit_overrides=preserve_explicit_overrides,
        )
    )


def _store_from_parsed(
    parsed: ParsedParamStore,
    *,
    preserve_explicit_overrides: bool,
) -> ParamStore:
    """typed intermediate を追加検証せず ParamStore へ一度だけ適用する。"""

    store = ParamStore()
    store._load_persisted_parameters(
        states={key: entry.value for key, entry in parsed.states.items()},
        meta=parsed.meta,
        explicit_by_key=parsed.explicit_by_key,
        preserve_explicit_overrides=preserve_explicit_overrides,
    )
    store._labels_ref().replace(dict(parsed.labels))
    store._ordinals_ref().replace({op: dict(by_site) for op, by_site in parsed.ordinals.items()})
    store._effects_ref().replace_persisted_state(
        topologies=dict(parsed.topologies),
        chain_ordinals=dict(parsed.chain_ordinals),
        order_overrides=dict(parsed.effect_order_overrides),
    )
    store._collapsed_headers_ref().update(parsed.collapsed_headers)
    store._locked_keys_ref().update(parsed.locked_parameters)
    store._replace_favorite_keys(parsed.favorite_parameters)
    store._variations_ref().update({variation.name: variation for variation in parsed.variations})

    store._touch()
    return store


def decode_param_store_result(
    obj: object,
    *,
    preserve_explicit_overrides: bool = False,
) -> ParamStoreDecodeResult:
    """現行 schema の payload を一度 parse して復元する。"""

    parsed = parse_param_store_payload(obj)
    return ParamStoreDecodeResult(
        store=_store_from_parsed(
            parsed,
            preserve_explicit_overrides=preserve_explicit_overrides,
        ),
        issues=parsed.issues,
    )


def loads_param_store_result(
    payload: str,
    *,
    preserve_explicit_overrides: bool = False,
) -> ParamStoreDecodeResult:
    """JSON 文字列を部分破損診断付きで復元する。"""

    return decode_param_store_result(
        json.loads(payload),
        preserve_explicit_overrides=preserve_explicit_overrides,
    )


__all__ = [
    "PARAM_STORE_SCHEMA_VERSION",
    "ParamStoreDecodeIssue",
    "ParamStoreDecodeResult",
    "ParamStoreSchemaError",
    "UnsupportedParamStoreSchemaError",
    "decode_param_store_result",
    "dumps_param_store",
    "encode_param_store",
    "loads_param_store_result",
    "param_store_schema_version",
]
