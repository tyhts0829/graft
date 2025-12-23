# どこで: `src/grafix/core/parameters/resolver.py`。
# 何を: base/GUI/CC から最終値を決定し、frame_params に記録する。
# なぜ: Geometry 生成時点で決定値を一意にし、GUI と署名を整合させるため。

from __future__ import annotations

from typing import Any, Iterable

from .context import current_cc_snapshot, current_frame_params, current_param_snapshot
from .frame_params import FrameParamsBuffer
from .key import ParameterKey
from .meta import ParamMeta
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
    """base/GUI/CC から effective 値を選び、(値, source) を返す。

    Notes
    -----
    優先順位は概ね CC > GUI > base（ただし kind=bool は常に GUI 値）。
    ここでは「どの値を採用するか」だけを決め、量子化は `_quantize()` が担う。
    """

    # cc_snapshot は parameter_context で固定された「今フレームの CC 値」。
    # 無い場合（None）や state.cc_key が未設定の場合は CC 経路をスキップする。
    cc_snapshot = current_cc_snapshot()
    if cc_snapshot is not None and state.cc_key is not None:
        # --- scalar CC（cc_key が int の場合）---
        if isinstance(state.cc_key, int) and state.cc_key in cc_snapshot:
            v = float(cc_snapshot[state.cc_key])
            if meta.kind in {"float", "int"}:
                # 0..1 を min..max に線形写像
                lo = float(meta.ui_min) if meta.ui_min is not None else 0.0
                hi = float(meta.ui_max) if meta.ui_max is not None else 1.0
                effective = lo + (hi - lo) * v
                return effective, "cc"
            if (
                meta.kind == "choice"
                and meta.choices is not None
                and list(meta.choices)
            ):
                # 0..1 を choices の index に写像
                choices = list(meta.choices)
                idx = min(len(choices) - 1, int(v * len(choices)))
                return str(choices[int(idx)]), "cc"

        # --- vec3 CC（cc_key が (a,b,c) の場合）---
        # 各成分ごとに「CC があれば CC」「なければ override に応じて GUI/base」を選ぶ。
        # ※ vec3 は成分ごとに CC を割り当てたい要望が多いので特別扱いしている。
        if meta.kind == "vec3" and isinstance(state.cc_key, tuple):
            lo = float(meta.ui_min) if meta.ui_min is not None else 0.0
            hi = float(meta.ui_max) if meta.ui_max is not None else 1.0

            try:
                bx, by, bz = base_value
                ux, uy, uz = state.ui_value
            except Exception:
                # 想定外の値が来た場合は CC 適用を諦め、通常の経路へフォールバックする。
                pass
            else:
                out: list[Any] = []
                used_cc = False
                for cc, b, u in zip(
                    state.cc_key, (bx, by, bz), (ux, uy, uz), strict=True
                ):
                    if cc is not None and cc in cc_snapshot:
                        used_cc = True
                        v = float(cc_snapshot[cc])
                        out.append(lo + (hi - lo) * v)
                    elif state.override:
                        out.append(u)
                    else:
                        out.append(b)

                # vec3 は「1 成分でも CC が使われたら source=cc」とする。
                # そうでなければ override の有無で gui/base に分岐する。
                if used_cc:
                    return tuple(out), "cc"
                if state.override:
                    return tuple(out), "gui"
                return tuple(out), "base"

    # --- CC を使わない通常経路 ---
    if meta.kind == "bool":
        # bool は override トグルを持たない。ui_value を常に採用する。
        # ui_value は初期状態では base_value と一致するため、実質的に base を踏襲する。
        return bool(state.ui_value), "gui"
    if state.override:
        # override=True のときだけ GUI 値を採用する（bool 以外）。
        return state.ui_value, "gui"
    # override=False のときはコードが与えた base を採用する。
    return base_value, "base"


def resolve_params(
    *,
    op: str,
    params: dict[str, Any],
    meta: dict[str, ParamMeta],
    site_id: str,
    chain_id: str | None = None,
    step_index: int | None = None,
    explicit_args: set[str] | None = None,
) -> dict[str, Any]:
    """引数辞書を解決し、Geometry.create 用の値を返す。

    Notes
    -----
    explicit_args は「ユーザーが明示的に渡した kwargs のキー集合」。
    指定時は FrameParamRecord.explicit に記録され、初期 override ポリシーに使われる。
    """

    param_snapshot = current_param_snapshot()
    frame_params: FrameParamsBuffer | None = current_frame_params()
    resolved: dict[str, Any] = {}

    for arg, base_value in params.items():
        # explicit_args は API 層で「ユーザーが明示的に渡した kwargs」のキー集合として渡される。
        # ここでの判定結果は record に記録され、初期 override ポリシー（store 側）に使われる。
        # ※ effective の解決結果そのものは explicit/implicit では変えない（state.override に従う）。
        is_explicit = True if explicit_args is None else arg in explicit_args

        # ParameterKey は GUI 行を一意に識別するキー（op + 呼び出し箇所 + 引数名）。
        key = ParameterKey(op=op, site_id=site_id, arg=arg)

        # param_snapshot は parameter_context 開始時点の store_snapshot(store) で固定されている。
        # そのため 1 draw 呼び出しの途中で GUI が動いても、このフレームの解決は決定的になる。
        snapshot_entry = param_snapshot.get(key)
        if snapshot_entry is not None:
            # 既に GUI 側で状態が存在する場合は、それを正として meta/state を採用する。
            snapshot_meta, state, _ordinal, _label = snapshot_entry
            arg_meta = snapshot_meta
        else:
            # 初出のキーは「登録側 meta がある場合のみ」GUI 対象として扱う。
            # meta が無い引数は GUI/CC の対象外とし、このフレームでも観測しない。
            arg_meta_opt = meta.get(arg)
            if arg_meta_opt is None:
                resolved[arg] = base_value
                continue
            arg_meta = arg_meta_opt
            # この場では仮の state（ui_value=base）を作るだけ。
            # override の初期値は store 側（フレーム境界のマージ）で explicit/implicit を見て決める。
            state = ParamState(ui_value=base_value)

        # base/GUI/CC を統合して effective を決める（source は "base"/"gui"/"cc"）。
        effective, source = _choose_value(base_value, state, arg_meta)
        # 量子化は「署名に入る値」と「実際に使う値」を一致させるため、ここで一元的に行う。
        effective = _quantize(effective, arg_meta)
        resolved[arg] = effective

        if frame_params is not None:
            # frame_params は「このフレームで観測した引数」を蓄積し、
            # parameter_context の finally で ParamStore にマージされる。
            frame_params.record(
                key=key,
                base=base_value,
                meta=arg_meta,
                effective=effective,
                source=source,
                explicit=is_explicit,
                chain_id=chain_id,
                step_index=step_index,
            )

    return resolved
