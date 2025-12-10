# どこで: `src/parameters/resolver.py`。
# 何を: base/GUI/CC から最終値を決定し、frame_params に記録する。
# なぜ: Geometry 生成時点で決定値を一意にし、GUI と署名を整合させるため。

from __future__ import annotations

from typing import Any, Dict

from .context import current_cc_snapshot, current_frame_params, current_param_snapshot
from .frame_params import FrameParamsBuffer
from .key import ParameterKey
from .meta import ParamMeta, infer_meta_from_value
from .state import ParamState


def _quantize(value: Any, meta: ParamMeta) -> Any:
    if meta.kind in ("float", "int") and meta.step:
        step = float(meta.step)
        try:
            v = float(value)
        except Exception:
            return value
        q = round(v / step) * step
        if meta.kind == "int":
            return int(q)
        return q
    return value


def _choose_value(base_value: Any, state: ParamState, meta: ParamMeta) -> tuple[Any, str]:
    cc_snapshot = current_cc_snapshot()
    if state.cc is not None and cc_snapshot is not None and state.cc in cc_snapshot:
        v = cc_snapshot[state.cc]
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
) -> tuple[Dict[str, Any], Dict[str, float]]:
    """引数辞書を解決し、Geometry.create 用の値と step マップを返す。"""

    param_snapshot = current_param_snapshot()
    frame_params: FrameParamsBuffer | None = current_frame_params()
    resolved: Dict[str, Any] = {}
    param_steps: Dict[str, float] = {}

    for arg, base_value in params.items():
        key = ParameterKey(op=op, site_id=site_id, arg=arg)
        arg_meta = meta.get(arg) or infer_meta_from_value(base_value)
        state: ParamState | None = param_snapshot.get(key)  # type: ignore[arg-type]
        if state is None:
            state = ParamState(
                override=False,
                ui_value=base_value,
                ui_min=arg_meta.ui_min,
                ui_max=arg_meta.ui_max,
                cc=None,
            )
        effective, source = _choose_value(base_value, state, arg_meta)
        effective = _quantize(effective, arg_meta)
        resolved[arg] = effective
        if arg_meta.step is not None:
            try:
                param_steps[arg] = float(arg_meta.step)
            except Exception:
                pass

        if frame_params is not None:
            frame_params.record(
                key=key,
                base=base_value,
                meta=arg_meta,
                effective=effective,
                source=source,
            )

    return resolved, param_steps
