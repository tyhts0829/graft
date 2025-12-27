# src/core/primitive_registry.py
# Geometry の primitive ノードに対応する生成関数レジストリ。
# op 名から RealizedGeometry 生成関数を引けるようにする。

from __future__ import annotations

import inspect
from collections.abc import ItemsView
from typing import Any, Callable

from grafix.core.realized_geometry import RealizedGeometry
from grafix.core.parameters.meta import ParamMeta

PrimitiveFunc = Callable[[tuple[tuple[str, Any], ...]], RealizedGeometry]


class PrimitiveRegistry:
    """primitive Geometry のレシピ名と生成関数を対応付けるレジストリ。

    Notes
    -----
    登録された関数のシグネチャは
    ``func(args: tuple[tuple[str, Any], ...]) -> RealizedGeometry`` を想定する。
    args は Geometry.args と同じ正規化済み表現を受け取る。
    """

    def __init__(self) -> None:
        """空のレジストリを初期化する。"""
        self._items: dict[str, PrimitiveFunc] = {}
        self._meta: dict[str, dict[str, ParamMeta]] = {}
        self._defaults: dict[str, dict[str, Any]] = {}
        self._param_order: dict[str, tuple[str, ...]] = {}

    def _register(
        self,
        name: str,
        func: PrimitiveFunc,
        *,
        overwrite: bool = True,
        param_order: tuple[str, ...] | None = None,
        meta: dict[str, ParamMeta] | None = None,
        defaults: dict[str, Any] | None = None,
    ) -> None:
        """primitive を登録する（内部用）。

        Notes
        -----
        登録は `@primitive` デコレータ経由に統一する。
        このメソッドはデコレータ実装の内部からのみ呼ぶ。
        """
        if not overwrite and name in self._items:
            raise ValueError(f"primitive '{name}' は既に登録されている")
        self._items[name] = func
        self._param_order[name] = (
            tuple(str(a) for a in param_order) if param_order is not None else ()
        )
        if meta is not None:
            self._meta[name] = meta
        if defaults is not None:
            self._defaults[name] = defaults

    def get(self, name: str) -> PrimitiveFunc:
        """op 名に対応する primitive を取得する。

        Parameters
        ----------
        name : str
            op 名。

        Returns
        -------
        PrimitiveFunc
            対応する生成関数。

        Raises
        ------
        KeyError
            未登録の op 名が指定された場合。
        """
        return self._items[name]

    def __contains__(self, name: object) -> bool:
        """指定された名前が登録済みかどうかを返す。"""
        return name in self._items

    def __getitem__(self, name: str) -> PrimitiveFunc:
        """辞書風に primitive を取得するショートカット。"""
        return self.get(name)

    def items(self) -> ItemsView[str, PrimitiveFunc]:
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


primitive_registry = PrimitiveRegistry()
"""グローバルな primitive レジストリインスタンス。"""


def primitive(
    func: Callable[..., RealizedGeometry] | None = None,
    *,
    overwrite: bool = True,
    meta: dict[str, ParamMeta] | None = None,
):
    """グローバル primitive レジストリ用デコレータ。

    関数名をそのまま op 名として登録する。

    Parameters
    ----------
    func : PrimitiveFunc or None, optional
        デコレート対象の関数。引数なしデコレータ利用時は None。
    overwrite : bool, optional
        既存エントリがある場合に上書きするかどうか。

    Examples
    --------
    @primitive
    def circle(*, r=1.0, cx=0.0, cy=0.0, segments=64):
        ...
    """

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
                    f"primitive '{f.__name__}' の meta 引数がシグネチャに存在しない: {arg!r}"
                )
            if param.default is inspect._empty:
                raise ValueError(
                    f"primitive '{f.__name__}' の meta 引数は default 必須: {arg!r}"
                )
            if param.default is None:
                raise ValueError(
                    f"primitive '{f.__name__}' の meta 引数 default に None は使えない: {arg!r}"
                )
            defaults[arg] = param.default
        return defaults

    def decorator(
        f: Callable[..., RealizedGeometry],
    ) -> Callable[..., RealizedGeometry]:
        module = str(f.__module__)
        if meta is None and (
            module.startswith("grafix.core.primitives.")
            or module.startswith("core.primitives.")
        ):
            raise ValueError(
                f"組み込み primitive は meta 必須: {f.__module__}.{f.__name__}"
            )

        def wrapper(args: tuple[tuple[str, Any], ...]) -> RealizedGeometry:
            params: dict[str, Any] = dict(args)
            return f(**params)

        defaults = None if meta is None else _defaults_from_signature(f, meta)
        param_order = None
        if meta is not None:
            sig = inspect.signature(f)
            meta_keys = set(meta.keys())
            sig_order = [name for name in sig.parameters if name in meta_keys]
            param_order = tuple(sig_order)
        primitive_registry._register(
            f.__name__,
            wrapper,
            overwrite=overwrite,
            param_order=param_order,
            meta=meta,
            defaults=defaults,
        )
        return f

    if func is None:
        return decorator
    return decorator(func)
