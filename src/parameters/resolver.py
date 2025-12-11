# どこで: `src/parameters/resolver.py`。
# 何を: base/GUI/CC から最終値を決定し、frame_params に記録する。
# なぜ: Geometry 生成時点で決定値を一意にし、GUI と署名を整合させるため。

from __future__ import annotations

from typing import Any, Dict, Iterable

from .context import current_cc_snapshot, current_frame_params, current_param_snapshot
from .frame_params import FrameParamsBuffer
from .key import ParameterKey
from .meta import ParamMeta, infer_meta_from_value
from .state import ParamState

DEFAULT_QUANT_STEP = 1e-3


def _quantize(value: Any, meta: ParamMeta) -> Any:
    """量子化を一元的に行う唯一の関数（Geometry 側では再量子化しない）。"""
    if meta.kind == "float":
        try:
            v = float(value)
        except Exception:
            return value
        q = round(v / DEFAULT_QUANT_STEP) * DEFAULT_QUANT_STEP
        return q
    if meta.kind == "int":
        try:
            return int(value)
        except Exception:
            return value
    if meta.kind.startswith("vec") and isinstance(value, Iterable):
        quantized = []
        for v in value:
            try:
                fv = float(v)
            except Exception:
                quantized.append(v)
                continue
            q = round(fv / DEFAULT_QUANT_STEP) * DEFAULT_QUANT_STEP
            quantized.append(q)
        return tuple(quantized)
    return value


def _choose_value(
    base_value: Any, state: ParamState, meta: ParamMeta
) -> tuple[Any, str]:
    cc_snapshot = current_cc_snapshot()
    if state.cc_key is not None and cc_snapshot is not None and state.cc_key in cc_snapshot:
        v = cc_snapshot[state.cc_key]
        # 0..1 を min..max に線形写像
        lo = meta.ui_min if meta.ui_min is not None else 0.0
        hi = meta.ui_max if meta.ui_max is not None else 1.0
        effective = lo + (hi - lo) * float(v)
        return effective, "cc"
    if state.override:
        return state.ui_value, "gui"
    return base_value, "base"


def resolve_params(
    *,
    op: str,
    params: Dict[str, Any],
    meta: Dict[str, ParamMeta],
    site_id: str,
) -> Dict[str, Any]:
    """引数辞書を解決し、Geometry.create 用の値を返す。"""

    param_snapshot = current_param_snapshot()
    frame_params: FrameParamsBuffer | None = current_frame_params()
    resolved: Dict[str, Any] = {}

    for arg, base_value in params.items():
        key = ParameterKey(op=op, site_id=site_id, arg=arg)
        snapshot_entry = param_snapshot.get(key)  # type: ignore[arg-type]
        if snapshot_entry is not None:
            snapshot_meta, state, _ordinal, _label = snapshot_entry
            arg_meta = snapshot_meta
        else:
            arg_meta = meta.get(arg) or infer_meta_from_value(base_value)
            state = ParamState(
                override=False,
                ui_value=base_value,
                cc_key=None,
            )
        effective, source = _choose_value(base_value, state, arg_meta)
        effective = _quantize(effective, arg_meta)
        resolved[arg] = effective

        if frame_params is not None:
            frame_params.record(
                key=key,
                base=base_value,
                meta=arg_meta,
                effective=effective,
                source=source,
            )

    return resolved
