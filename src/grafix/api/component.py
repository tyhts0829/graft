# どこで: `src/grafix/api/component.py`。
# 何を: `@component` デコレータ（公開引数だけを Parameter GUI に出し、関数本体は自動で mute）を提供する。
# なぜ: 作り込んだ形状を関数として再利用しつつ、GUI を “公開パラメータ” だけに保つため。

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from functools import wraps
from typing import Any, ParamSpec, TypeVar

from grafix.core.component_registry import component_registry
from grafix.core.parameters import caller_site_id, current_frame_params, current_param_store
from grafix.core.parameters.context import (
    current_param_recording_enabled,
    parameter_recording_muted,
)
from grafix.core.parameters.labels_ops import set_label
from grafix.core.parameters.meta import ParamMeta
from grafix.core.parameters.meta_spec import meta_dict_from_user
from grafix.core.parameters.resolver import resolve_params

P = ParamSpec("P")
R = TypeVar("R")


def _defaults_from_signature(
    func: Callable[..., object],
    meta: dict[str, ParamMeta],
) -> dict[str, Any]:
    sig = inspect.signature(func)
    defaults: dict[str, Any] = {}
    for arg in meta.keys():
        param = sig.parameters.get(arg)
        if param is None:
            raise ValueError(
                f"@component meta 引数がシグネチャに存在しません: {func.__name__}.{arg}"
            )
        if param.default is inspect._empty:
            raise ValueError(
                f"@component meta 引数は default 必須です: {func.__name__}.{arg}"
            )
        if param.default is None:
            raise ValueError(
                f"@component meta 引数 default に None は使えません: {func.__name__}.{arg}"
            )
        defaults[arg] = param.default
    return defaults


def _component_site_id(base_site_id: str, key: object | None) -> str:
    if key is None:
        return str(base_site_id)
    if isinstance(key, (str, int)):
        return f"{base_site_id}|{key}"
    raise TypeError("component の key は str|int|None である必要があります")


def _maybe_set_label(*, op: str, site_id: str, label: str) -> None:
    store = current_param_store()
    if store is not None:
        set_label(store, op=op, site_id=site_id, label=label)
        return
    frame_params = current_frame_params()
    if frame_params is not None:
        frame_params.set_label(op=op, site_id=site_id, label=label)


def component(
    *,
    meta: Mapping[str, ParamMeta | Mapping[str, object]],
    op: str | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """コンポーネント関数を Parameter GUI 向けにラップするデコレータ。

    Parameters
    ----------
    meta : Mapping[str, ParamMeta | Mapping[str, object]]
        GUI に公開する引数のメタ情報。ここに含めた引数だけが GUI/永続化の対象になる。

        dict spec の形式:
        - kind: str（必須）
        - ui_min/ui_max: object（任意）
        - choices: Sequence[str] | None（任意）
    op : str | None
        ParamStore 上の op 名。省略時は `component.<func_name>` を使用する。

    Notes
    -----
    - 公開対象は `meta` に含まれる引数のみ。
    - 関数本体は自動で mute され、内部の `G.*` / `E.*` の観測（GUI/永続化）を行わない。
    - `name=` と `key=` を予約引数として使える（GUI には出さない）。
      `key` は同一呼び出し箇所から複数回生成する場合の衝突回避に使う。
    """

    meta_norm = meta_dict_from_user(meta)
    reserved = {"name", "key"}
    if reserved & set(meta_norm.keys()):
        bad = ", ".join(sorted(reserved & set(meta_norm.keys())))
        raise ValueError(f"@component meta に予約引数は含められません: {bad}")

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        component_op = f"component.{func.__name__}" if op is None else str(op)
        sig = inspect.signature(func)
        _defaults_from_signature(func, meta_norm)
        meta_keys = set(meta_norm.keys())
        sig_order = [name for name in sig.parameters if name in meta_keys]
        component_registry._register(
            component_op,
            display_op=str(func.__name__),
            meta=meta_norm,
            param_order=tuple(sig_order),
            overwrite=True,
        )

        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            bound = sig.bind(*args, **kwargs)
            explicit_keys = set(bound.arguments.keys())
            bound.apply_defaults()

            # GUI 非公開の予約引数
            name = bound.arguments.get("name", None)
            key = bound.arguments.get("key", None)

            base_site_id = caller_site_id(skip=1)
            site_id = _component_site_id(base_site_id, key)

            # group header 名は、指定が無ければ関数名を使う（GUI 未使用時は何もしない）。
            if current_param_recording_enabled():
                label = str(func.__name__) if name is None else str(name)
                _maybe_set_label(op=component_op, site_id=site_id, label=label)

            # 公開引数だけ解決する（recording が無効なら素の値で通す）。
            public_params = {k: bound.arguments[k] for k in meta_keys}
            resolved_params = public_params
            explicit_public = meta_keys & explicit_keys
            if (
                current_param_recording_enabled()
                and current_frame_params() is not None
                and meta_norm
            ):
                resolved_params = resolve_params(
                    op=component_op,
                    params=public_params,
                    meta=meta_norm,
                    site_id=site_id,
                    explicit_args=set(explicit_public),
                )

            for k, v in resolved_params.items():
                bound.arguments[k] = v

            # 本体は常に mute（内部の G/E は GUI/永続化の対象外）
            with parameter_recording_muted():
                return func(*bound.args, **bound.kwargs)

        return wrapper

    return decorator


__all__ = ["component"]
