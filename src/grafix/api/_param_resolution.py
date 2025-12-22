# どこで: `src/grafix/api/_param_resolution.py`。
# 何を: API 層（G/E）で共通の param 解決と label 設定を提供する。
# なぜ: primitive/effect での重複を減らし、仕様変更時の修正漏れを防ぐため。

from __future__ import annotations

from typing import Any

from grafix.core.parameters import current_frame_params, current_param_store, resolve_params
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

    if label is None:
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
    defaults: dict[str, Any],
    meta: dict[str, ParamMeta],
    chain_id: str | None = None,
    step_index: int | None = None,
) -> dict[str, Any]:
    """API 層の kwargs を解決し、Geometry.create 用の値を返す。

    Notes
    -----
    - defaults で省略引数を補完し、ユーザー指定で上書きした base_params を作る。
    - parameter_context 内で meta がある場合のみ resolve_params を呼び、観測レコードを積む。
    """

    explicit_args = set(user_params.keys())
    base_params = dict(defaults)
    base_params.update(user_params)
    if current_frame_params() is not None and meta:
        return resolve_params(
            op=op,
            params=base_params,
            meta=meta,
            site_id=site_id,
            explicit_args=explicit_args,
            chain_id=chain_id,
            step_index=step_index,
        )
    return base_params


__all__ = ["resolve_api_params", "set_api_label"]
