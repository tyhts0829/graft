# src/core/effect_registry.py
# Geometry の effect ノードに対応する実体変換関数レジストリ。
# op 名から RealizedGeometry 変換関数を引けるようにする。

from __future__ import annotations

import inspect
from collections.abc import ItemsView
from typing import Any, Callable, Sequence

from grafix.core.realized_geometry import RealizedGeometry, concat_realized_geometries
from grafix.core.parameters.meta import ParamMeta

EffectFunc = Callable[
    [Sequence[RealizedGeometry], tuple[tuple[str, Any], ...]],
    RealizedGeometry,
]


class EffectRegistry:
    """effect Geometry のレシピ名と適用関数を対応付けるレジストリ。

    Notes
    -----
    登録された関数のシグネチャは
    ``func(inputs: Sequence[RealizedGeometry], args: tuple[tuple[str, Any], ...]) -> RealizedGeometry``
    を想定する。inputs は通常 1 要素だが、将来の複合 effect に備えて列として扱う。
    """

    def __init__(self) -> None:
        """空のレジストリを初期化する。"""
        self._items: dict[str, EffectFunc] = {}
        self._meta: dict[str, dict[str, ParamMeta]] = {}
        self._defaults: dict[str, dict[str, Any]] = {}
        self._n_inputs: dict[str, int] = {}
        self._param_order: dict[str, tuple[str, ...]] = {}

    def _register(
        self,
        name: str,
        func: EffectFunc,
        *,
        overwrite: bool = True,
        n_inputs: int = 1,
        param_order: Sequence[str] | None = None,
        meta: dict[str, ParamMeta] | None = None,
        defaults: dict[str, Any] | None = None,
    ) -> None:
        """effect を登録する（内部用）。

        Notes
        -----
        登録は `@effect` デコレータ経由に統一する。
        このメソッドはデコレータ実装の内部からのみ呼ぶ。
        """
        if not overwrite and name in self._items:
            raise ValueError(f"effect '{name}' は既に登録されている")
        self._items[name] = func
        self._n_inputs[name] = int(n_inputs)
        self._param_order[name] = (
            tuple(str(a) for a in param_order) if param_order is not None else ()
        )
        if meta is not None:
            self._meta[name] = meta
        if defaults is not None:
            self._defaults[name] = defaults

    def get(self, name: str) -> EffectFunc:
        """op 名に対応する effect を取得する。

        Parameters
        ----------
        name : str
            op 名。

        Returns
        -------
        EffectFunc
            対応する適用関数。

        Raises
        ------
        KeyError
            未登録の op 名が指定された場合。
        """
        return self._items[name]

    def __contains__(self, name: object) -> bool:
        """指定された名前が登録済みかどうかを返す。"""
        return name in self._items

    def __getitem__(self, name: str) -> EffectFunc:
        """辞書風に effect を取得するショートカット。"""
        return self.get(name)

    def items(self) -> ItemsView[str, EffectFunc]:
        """登録済みエントリの (name, func) ビューを返す。"""
        return self._items.items()

    def get_meta(self, name: str) -> dict[str, ParamMeta]:
        """op 名に対応する ParamMeta 辞書を取得する。"""
        return dict(self._meta.get(name, {}))

    def get_defaults(self, name: str) -> dict[str, Any]:
        """op 名に対応するデフォルト引数辞書を取得する。"""
        return dict(self._defaults.get(name, {}))

    def get_param_order(self, name: str) -> tuple[str, ...]:
        """op 名に対応する GUI 用の引数順序を返す。"""

        return tuple(self._param_order.get(name, ()))

    def get_n_inputs(self, name: str) -> int:
        """op 名に対応する入力 Geometry 数（arity）を返す。"""
        return int(self._n_inputs.get(name, 1))


effect_registry = EffectRegistry()
"""グローバルな effect レジストリインスタンス。"""


def effect(
    func: Callable[..., RealizedGeometry] | None = None,
    *,
    overwrite: bool = True,
    n_inputs: int = 1,
    meta: dict[str, ParamMeta] | None = None,
):
    """グローバル effect レジストリ用デコレータ。

    関数名をそのまま op 名として登録する。

    Parameters
    ----------
    func : EffectFunc or None, optional
        デコレート対象の関数。引数なしデコレータ利用時は None。
    overwrite : bool, optional
        既存エントリがある場合に上書きするかどうか。

    Examples
    --------
    @effect
    def scale(inputs, *, s=1.0):
        ...
    """

    if meta is not None and "bypass" in meta:
        raise ValueError("effect の予約引数 'bypass' は meta に含められない")

    n_inputs_i = int(n_inputs)
    if n_inputs_i < 1:
        raise ValueError("n_inputs は 1 以上である必要がある")

    def _defaults_from_signature(
        f: Callable[..., RealizedGeometry],
        param_meta: dict[str, ParamMeta],
    ) -> dict[str, Any]:
        sig = inspect.signature(f)
        defaults: dict[str, Any] = {}
        for arg in param_meta.keys():
            param = sig.parameters.get(arg)
            if param is None:
                raise ValueError(
                    f"effect '{f.__name__}' の meta 引数がシグネチャに存在しない: {arg!r}"
                )
            if param.default is inspect._empty:
                raise ValueError(
                    f"effect '{f.__name__}' の meta 引数は default 必須: {arg!r}"
                )
            if param.default is None:
                raise ValueError(
                    f"effect '{f.__name__}' の meta 引数 default に None は使えない: {arg!r}"
                )
            defaults[arg] = param.default
        return defaults

    def decorator(
        f: Callable[..., RealizedGeometry],
    ) -> Callable[..., RealizedGeometry]:
        module = str(f.__module__)
        if meta is None and (
            module.startswith("grafix.core.effects.") or module.startswith("core.effects.")
        ):
            raise ValueError(f"組み込み effect は meta 必須: {f.__module__}.{f.__name__}")

        meta_with_bypass = (
            {"bypass": ParamMeta(kind="bool"), **meta} if meta is not None else None
        )

        def wrapper(
            inputs: Sequence[RealizedGeometry],
            args: tuple[tuple[str, Any], ...],
        ) -> RealizedGeometry:
            params: dict[str, Any] = dict(args)
            bypass = bool(params.pop("bypass", False))
            if bypass:
                if not inputs:
                    return concat_realized_geometries()
                if len(inputs) == 1:
                    return inputs[0]
                return concat_realized_geometries(*inputs)
            return f(inputs, **params)

        defaults = None
        if meta is not None:
            defaults = _defaults_from_signature(f, meta)
            defaults = {"bypass": False, **defaults}
            sig = inspect.signature(f)
            meta_keys = set(meta.keys())
            sig_order = [name for name in sig.parameters if name in meta_keys]
            param_order = ("bypass", *sig_order)
        else:
            param_order = None
        effect_registry._register(
            f.__name__,
            wrapper,
            overwrite=overwrite,
            n_inputs=n_inputs_i,
            param_order=param_order,
            meta=meta_with_bypass,
            defaults=defaults,
        )
        return f

    if func is None:
        return decorator
    return decorator(func)
