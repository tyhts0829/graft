"""
Purpose:
    G/Eが共有するparameter解決とauthoring label記録の境界を提供する。
Use when:
    CODE/UI/MIDI解決の呼び出し方、明示引数、またはlabel観測を変更する場合。
Constraints:
    - parameter recordingがmuteされた区間ではstore/frame observationを追加しない。
    - labelを受けた場合、利用可能なstoreまたはframe bufferへ記録し、無言で破棄しない。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from grafix.core.parameters import current_frame_params, current_param_store, resolve_params
from grafix.core.parameters.context import current_param_recording_enabled
from grafix.core.parameters.identity import identity_string
from grafix.core.parameters.labels_ops import set_label
from grafix.core.parameters.meta import ParamMeta

_NO_STORE_FOR_LABEL_ERROR = "ParamStore が利用できないコンテキストで name 指定は使えません"


def set_api_label(*, op: str, site_id: str, label: str | None) -> None:
    """API 層の name ラベルを ParamStore に保存する。

    Parameters
    ----------
    op : str
        primitive/effect の op 名。
    site_id : str
        呼び出し箇所 ID。
    label : str | None
        付与するラベル。None の場合は何もしない。
    """

    if label is None or not current_param_recording_enabled():
        return
    store = current_param_store()
    if store is not None:
        set_label(store, op=op, site_id=site_id, label=label)
        return
    frame_params = current_frame_params()
    if frame_params is not None:
        frame_params.set_label(op=op, site_id=site_id, label=label)
        return
    raise RuntimeError(_NO_STORE_FOR_LABEL_ERROR)


def resolve_api_params(
    *,
    op: str,
    site_id: str,
    user_params: dict[str, Any],
    defaults: Mapping[str, Any],
    meta: Mapping[str, ParamMeta],
    explicit_args: set[str] | None = None,
) -> dict[str, Any]:
    """API 層の kwargs を解決し、Geometry.create 用の値を返す。

    Notes
    -----
    - defaults で省略引数を補完し、ユーザー指定で上書きした base_params を作る。
    - parameter_context 内で meta がある場合のみ resolve_params を呼び、観測レコードを積む。
    """

    resolved_explicit_args = (
        set(user_params)
        if explicit_args is None
        else {
            identity_string(arg, name="explicit argument")
            for arg in explicit_args
        }
    )
    base_params = dict(defaults)
    base_params.update(user_params)
    if current_param_recording_enabled() and current_frame_params() is not None and meta:
        return resolve_params(
            op=op,
            params=base_params,
            meta=meta,
            site_id=site_id,
            explicit_args=resolved_explicit_args,
        )
    return base_params


__all__ = ["resolve_api_params", "set_api_label"]
