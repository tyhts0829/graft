"""
Purpose:
    Layer固有の線幅・線色を通常parameterと同じ観測・override lifecycleへ接続する。
Use when:
    layer styleのGUI制御、ParameterKey identity、またはstyle値の表現境界を変更する場合。
Constraints:
    - 描画側のRGB01とstore/GUI側のRGB255を混同しない。
    - GUI値はoverrideが有効な場合だけ適用し、code側のexplicit指定情報を観測へ残す。
    - layer_site_idをgroup identityとして安定させ、frame終端のmerge経路を迂回しない。
"""

from __future__ import annotations

from .frame_params import FrameParamRecord
from .context import current_frame_params, current_param_store
from .key import ParameterKey
from .labels_ops import set_label
from .meta import ParamMeta
from .source import ValueSource
from .style import rgb01_to_rgb255, rgb255_to_rgb01, validate_rgb255

# Layer style を ParamStore 上で group 化するための op 名。
# GUI 側は (op, site_id) を 1 ブロックとして表示し、その中に arg ごとの行を並べる。
LAYER_STYLE_OP = "__layer_style__"

# group 内で個別のパラメータを識別する arg 名。
LAYER_STYLE_LINE_THICKNESS = "line_thickness"
LAYER_STYLE_LINE_COLOR = "line_color"

# GUI 表示/保存に使うメタ情報。
# thickness はスライダ等で扱いやすい「十分に小さい」レンジを既定として持たせる。
LAYER_STYLE_THICKNESS_META = ParamMeta(
    kind="float",
    ui_min=1e-4,
    ui_max=1e-2,
    description="このレイヤーだけに適用する線幅を指定する。",
)
# color は 0..255 の RGB を想定する（UI 側の表現に合わせる）。
LAYER_STYLE_COLOR_META = ParamMeta(
    kind="rgb",
    ui_min=0,
    ui_max=255,
    description="このレイヤーだけに適用する線色を RGB で指定する。",
)


def layer_style_key(layer_site_id: str, arg: str) -> ParameterKey:
    """Layer style 用の ParameterKey を返す。

    Layer style は primitive/effect と同様に `(op, site_id, arg)` で識別する。

    - `op`: `LAYER_STYLE_OP`（Layer style の group）
    - `site_id`: `Layer.site_id`（生成位置由来の安定 ID）
    - `arg`: group 内のパラメータ名（例: `line_thickness`, `line_color`）
    """

    return ParameterKey(op=LAYER_STYLE_OP, site_id=layer_site_id, arg=arg)


def layer_style_records(
    *,
    layer_site_id: str,
    base_line_thickness: float,
    base_line_color_rgb01: tuple[float, float, float],
    effective_line_thickness: float,
    effective_line_color_rgb01: tuple[float, float, float],
    line_thickness_source: ValueSource,
    line_color_source: ValueSource,
    explicit_line_thickness: bool,
    explicit_line_color: bool,
) -> list[FrameParamRecord]:
    """Layer style の観測レコード（line_thickness/line_color）を返す。

    Notes
    -----
    - `FrameParamRecord.base` は「ストア/GUI 側の表現」に合わせる。
      そのため `base_line_color_rgb01`（0..1 float）は `RGB255`（0..255 int）へ変換して保持する。
    - `effective_*` は override 適用後の最終値、`*_source` はその由来を表す。
      color の effective も runtime 観測では `RGB255` に揃える。
    - `explicit_*` は「コード側が値を明示指定したか」を表すフラグで、
      初期 `override` の方針（コード優先/GUI 優先）に使われる。
    """

    thickness_key = layer_style_key(layer_site_id, LAYER_STYLE_LINE_THICKNESS)
    color_key = layer_style_key(layer_site_id, LAYER_STYLE_LINE_COLOR)

    return [
        FrameParamRecord(
            key=thickness_key,
            base=float(base_line_thickness),
            meta=LAYER_STYLE_THICKNESS_META,
            effective=float(effective_line_thickness),
            source=line_thickness_source,
            explicit=explicit_line_thickness,
        ),
        FrameParamRecord(
            key=color_key,
            base=rgb01_to_rgb255(base_line_color_rgb01),
            meta=LAYER_STYLE_COLOR_META,
            effective=rgb01_to_rgb255(effective_line_color_rgb01),
            source=line_color_source,
            explicit=explicit_line_color,
        ),
    ]


def observe_and_apply_layer_style(
    *,
    layer_site_id: str,
    layer_name: str | None,
    base_line_thickness: float,
    base_line_color_rgb01: tuple[float, float, float],
    explicit_line_thickness: bool,
    explicit_line_color: bool,
) -> tuple[float, tuple[float, float, float]]:
    """Layer style を観測し、必要なら GUI override を適用して描画値を返す。

    この関数は「観測」と「適用」を同じ場所で行うことで、
    呼び出し側（pipeline）が “スタイル解決後の最終値” を 1 回の呼び出しで得られるようにする。

    Parameters
    ----------
    layer_site_id : str
        対象 Layer の site_id。
    layer_name : str | None
        Layer 名。None の場合は label は設定しない。
    base_line_thickness : float
        resolve_layer_style() 後の線幅（base）。
    base_line_color_rgb01 : tuple[float, float, float]
        resolve_layer_style() 後の線色（0..1, base）。
    explicit_line_thickness : bool
        Layer が thickness を明示指定したかどうか。
    explicit_line_color : bool
        Layer が color を明示指定したかどうか。

    Returns
    -------
    tuple[float, tuple[float, float, float]]
        `(thickness, color_rgb01)`。ParamStore 上で `override=True` の場合のみ GUI 値を採用する。

    Notes
    -----
    - `parameter_recording_muted()` 中でも Layer style は観測/適用する（現状維持）。
    - ParamStore が無いコンテキストでも、FrameParamsBuffer があれば records/labels を蓄積する。
      （例: `parameter_context_from_snapshot()` で実行したとき）
    """

    # まずはコード側で解決した base を採用し、必要なら store の override で置き換える。
    thickness = float(base_line_thickness)
    color = base_line_color_rgb01

    frame_params = current_frame_params()
    store = current_param_store()
    if layer_name is not None:
        # (op, site_id) のラベルは GUI のヘッダ表示に使う。
        # store があるなら即時に反映し、無い場合は buffer に積んで呼び出し側へ返す。
        if store is not None:
            set_label(store, op=LAYER_STYLE_OP, site_id=layer_site_id, label=layer_name)
        elif frame_params is not None:
            frame_params.set_label(op=LAYER_STYLE_OP, site_id=layer_site_id, label=layer_name)

    thickness_source: ValueSource = "code"
    color_source: ValueSource = "code"
    if store is not None:
        # override=True のときだけ UI 値を採用する（override=False は base を優先）。
        thickness_state = store.get_state(
            layer_style_key(layer_site_id, LAYER_STYLE_LINE_THICKNESS)
        )
        if thickness_state is not None and thickness_state.override:
            thickness = float(thickness_state.ui_value)
            thickness_source = "ui"

        color_state = store.get_state(
            layer_style_key(layer_site_id, LAYER_STYLE_LINE_COLOR)
        )
        if color_state is not None and color_state.override:
            rgb255 = validate_rgb255(color_state.ui_value)
            color = rgb255_to_rgb01(rgb255)
            color_source = "ui"

    if frame_params is not None:
        # store が無い worker でも、最終 effective/source を含む同じ観測形を残す。
        frame_params.records.extend(
            layer_style_records(
                layer_site_id=layer_site_id,
                base_line_thickness=base_line_thickness,
                base_line_color_rgb01=base_line_color_rgb01,
                effective_line_thickness=thickness,
                effective_line_color_rgb01=color,
                line_thickness_source=thickness_source,
                line_color_source=color_source,
                explicit_line_thickness=explicit_line_thickness,
                explicit_line_color=explicit_line_color,
            )
        )

    return thickness, color
