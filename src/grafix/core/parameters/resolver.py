"""
Purpose:
    code、GUI、MIDIから一frameのcanonical effective parameter値とsourceを決定する。
Use when:
    parameter優先順位、override、量子化、またはGeometry recipeへ渡す値を変更する場合。
Constraints:
    - 優先順位をMIDI、UI、CODEの順に保ち、frame固定snapshotだけを参照する。
    - 数値量子化はこの境界で一度だけ行い、Geometry側で再量子化しない。
    - DAG identityと実評価へ同じresolved valueを渡し、観測recordにもそのsourceを残す。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from .context import current_cc_snapshot, current_frame_params, current_param_snapshot
from .frame_params import FrameParamsBuffer
from .key import ParameterKey
from .meta import ParamMeta, merge_code_meta_with_stored_gui_meta
from .source import ValueSource
from .state import ParamState, ParamStateSnapshot
from .validation import validate_cc_key, validate_parameter_value
from .view import canonicalize_ui_value_for_meta_change

DEFAULT_QUANT_STEP = 1e-3


def _quantize(value: Any, meta: ParamMeta) -> Any:
    """量子化を一元的に行う唯一の関数（Geometry 側では再量子化しない）。"""
    if meta.kind == "float":
        q = round(value / DEFAULT_QUANT_STEP) * DEFAULT_QUANT_STEP
        return q
    if meta.kind == "int":
        return int(value)
    if meta.kind == "vec3":
        return tuple(
            round(component / DEFAULT_QUANT_STEP) * DEFAULT_QUANT_STEP
            for component in value
        )
    return value


def _choose_value(
    base_value: Any,
    state: ParamState | ParamStateSnapshot,
    meta: ParamMeta,
    *,
    op: str,
) -> tuple[Any, ValueSource]:
    """CODE/UI/MIDI から effective 値を選び、(値, source) を返す。

    Notes
    -----
    優先順位は MIDI > UI > CODE。全 kind で UI/CODE は ``override`` に従う。
    ここでは「どの値を採用するか」だけを決め、量子化は `_quantize()` が担う。
    """

    # cc_snapshot は parameter_context で固定された「今フレームの CC 値」。
    # 無い場合（None）や state.cc_key が未設定の場合は CC 経路をスキップする。
    cc_snapshot = current_cc_snapshot()
    if state.cc_key is not None:
        validate_cc_key(state.cc_key, kind=meta.kind, op=op)
    if cc_snapshot is not None and state.cc_key is not None:
        # --- scalar CC（cc_key が int の場合）---
        if isinstance(state.cc_key, int) and state.cc_key in cc_snapshot:
            v = float(cc_snapshot[state.cc_key])
            if meta.kind in {"float", "int"}:
                # 0..1 を min..max に線形写像
                lo = float(meta.ui_min) if meta.ui_min is not None else 0.0
                hi = float(meta.ui_max) if meta.ui_max is not None else 1.0
                effective = lo + (hi - lo) * v
                return effective, cc_snapshot.source
            if (
                meta.kind == "choice"
                and meta.choices is not None
            ):
                # 0..1 を choices の index に写像
                choices = list(meta.choices)
                idx = min(len(choices) - 1, int(v * len(choices)))
                return choices[int(idx)], cc_snapshot.source

        # --- vec3 CC（cc_key が (a,b,c) の場合）---
        # 各成分ごとに「CC があれば CC」「なければ override に応じて GUI/base」を選ぶ。
        # ※ vec3 は成分ごとに CC を割り当てたい要望が多いので特別扱いしている。
        # ※ RGB は Style/Layer Style の専用 resolver を通り、そこでは cc_snapshot を
        #    解決していない。誤って非機能 UI を見せないよう rules.py 側でも MIDI を
        #    無効にしており、この分岐を安易に RGB へ広げないこと。
        if meta.kind == "vec3" and isinstance(state.cc_key, tuple):
            lo = float(meta.ui_min) if meta.ui_min is not None else 0.0
            hi = float(meta.ui_max) if meta.ui_max is not None else 1.0

            bx, by, bz = base_value
            ux, uy, uz = state.ui_value
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
                return tuple(out), cc_snapshot.source
            if state.override:
                return tuple(out), "ui"
            return tuple(out), "code"

    # --- CC を使わない通常経路 ---
    if state.override:
        # override=True のときだけ UI 値を採用する。
        return state.ui_value, "ui"
    # override=False のときはコードが与えた base を採用する。
    return base_value, "code"


def resolve_params(
    *,
    op: str,
    params: dict[str, Any],
    meta: Mapping[str, ParamMeta],
    site_id: str,
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
        state: ParamState | ParamStateSnapshot
        snapshot_entry = param_snapshot.get(key)
        if snapshot_entry is not None:
            # state と GUI-owned range は snapshot を正とする。一方、
            # kind/choices/説明などの code-owned metadata は現在の登録内容を
            # 正とし、catalog や callable の更新へ同じ frame から追随する。
            snapshot_meta, state, _ordinal, _label = snapshot_entry
            code_meta = meta.get(arg)
            arg_meta = (
                snapshot_meta
                if code_meta is None
                else merge_code_meta_with_stored_gui_meta(
                    code_meta,
                    snapshot_meta,
                )
            )
            if (
                code_meta is not None
                and code_meta.kind != snapshot_meta.kind
            ):
                state = replace(
                    state,
                    ui_value=canonicalize_ui_value_for_meta_change(
                        state.ui_value,
                        base_value,
                        snapshot_meta,
                        arg_meta,
                    ),
                )
        else:
            # 初出のキーは「登録側 meta がある場合のみ」GUI 対象として扱う。
            # meta が無い引数は GUI/CC の対象外とし、このフレームでも観測しない。
            arg_meta_opt = meta.get(arg)
            if arg_meta_opt is None:
                resolved[arg] = base_value
                continue
            arg_meta = arg_meta_opt
            base_value = validate_parameter_value(
                base_value,
                kind=arg_meta.kind,
                choices=arg_meta.choices,
            )
            # この場では仮の state（ui_value=base）を作るだけ。
            # override の初期値は store 側（フレーム境界のマージ）で explicit/implicit を見て決める。
            state = ParamState(ui_value=base_value)

        base_value = validate_parameter_value(
            base_value,
            kind=arg_meta.kind,
            choices=arg_meta.choices,
        )

        # CODE/UI/MIDI を統合して effective 値と接続由来を決める。
        effective, source = _choose_value(
            base_value,
            state,
            arg_meta,
            op=op,
        )
        # 量子化は「署名に入る値」と「実際に使う値」を一致させるため、ここで一元的に行う。
        effective = _quantize(effective, arg_meta)
        effective = validate_parameter_value(
            effective,
            kind=arg_meta.kind,
            choices=arg_meta.choices,
        )
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
            )

    return resolved
