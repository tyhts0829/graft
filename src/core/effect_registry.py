# src/core/effect_registry.py
# Geometry の effect ノードに対応する実体変換関数レジストリ。
# op 名から RealizedGeometry 変換関数を引けるようにする。

from __future__ import annotations

from collections.abc import ItemsView
from typing import Any, Callable, Mapping, Sequence

from src.core.realized_geometry import RealizedGeometry
from src.parameters.meta import ParamMeta

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

    def register(
        self,
        name: str,
        func: EffectFunc | None = None,
        *,
        overwrite: bool = False,
        meta: dict[str, ParamMeta] | None = None,
    ):
        """effect を登録する（関数またはデコレータとして使用可能）。

        Parameters
        ----------
        name : str
            op 名。
        func : EffectFunc
            実体配列に effect を適用する関数。
        overwrite : bool, optional
            既存エントリがある場合に上書きするかどうか。

        Raises
        ------
        ValueError
            overwrite が False のときに同名のエントリが既に存在する場合。
        """
        if func is None:
            # デコレータとして使用された場合。
            def decorator(f: EffectFunc) -> EffectFunc:
                self.register(name, f, overwrite=overwrite, meta=meta)
                return f

            return decorator

        if not overwrite and name in self._items:
            raise ValueError(f"effect '{name}' は既に登録されている")
        self._items[name] = func
        if meta is not None:
            self._meta[name] = meta
        return func

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


effect_registry = EffectRegistry()
"""グローバルな effect レジストリインスタンス。"""


def effect(
    func: Callable[..., RealizedGeometry] | None = None,
    *,
    overwrite: bool = False,
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

    def decorator(f: Callable[..., RealizedGeometry]) -> Callable[..., RealizedGeometry]:
        def wrapper(
            inputs: Sequence[RealizedGeometry],
            args: tuple[tuple[str, Any], ...],
        ) -> RealizedGeometry:
            params: dict[str, Any] = dict(args)
            return f(inputs, **params)

        effect_registry.register(f.__name__, wrapper, overwrite=overwrite, meta=meta)
        return f

    if func is None:
        return decorator
    return decorator(func)


def register_effect(
    name: str,
    func: EffectFunc,
    *,
    overwrite: bool = False,
) -> None:
    """グローバルレジストリに effect を登録する。

    Parameters
    ----------
    name : str
        op 名。
    func : EffectFunc
        実体配列に effect を適用する関数。
    overwrite : bool, optional
        既存エントリがある場合に上書きするかどうか。
    """
    effect_registry.register(name, func, overwrite=overwrite)


def get_effect(name: str) -> EffectFunc:
    """グローバルレジストリから effect を取得する。

    Parameters
    ----------
    name : str
        op 名。

    Returns
    -------
    EffectFunc
        対応する適用関数。
    """
    return effect_registry.get(name)
