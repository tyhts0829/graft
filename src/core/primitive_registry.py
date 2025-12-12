# src/core/primitive_registry.py
# Geometry の primitive ノードに対応する生成関数レジストリ。
# op 名から RealizedGeometry 生成関数を引けるようにする。

from __future__ import annotations

from collections.abc import ItemsView
from typing import Any, Callable, Mapping

from src.core.realized_geometry import RealizedGeometry
from src.parameters.meta import ParamMeta, infer_meta_from_value

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

    def register(
        self,
        name: str,
        func: PrimitiveFunc | None = None,
        *,
        overwrite: bool = False,
        meta: dict[str, ParamMeta] | None = None,
    ):
        """primitive を登録する（関数またはデコレータとして使用可能）。

        Parameters
        ----------
        name : str
            op 名。
        func : PrimitiveFunc
            実体配列を生成する関数。
        overwrite : bool, optional
            既存エントリがある場合に上書きするかどうか。

        Raises
        ------
        ValueError
            overwrite が False のときに同名のエントリが既に存在する場合。
        """
        if func is None:
            # デコレータとして使用された場合。
            def decorator(f: PrimitiveFunc) -> PrimitiveFunc:
                self.register(name, f, overwrite=overwrite, meta=meta)
                return f

            return decorator

        if not overwrite and name in self._items:
            raise ValueError(f"primitive '{name}' は既に登録されている")
        self._items[name] = func
        if meta is not None:
            self._meta[name] = meta
        return func

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


primitive_registry = PrimitiveRegistry()
"""グローバルな primitive レジストリインスタンス。"""


def primitive(
    func: Callable[..., RealizedGeometry] | None = None,
    *,
    overwrite: bool = False,
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

    def decorator(f: Callable[..., RealizedGeometry]) -> Callable[..., RealizedGeometry]:
        def wrapper(args: tuple[tuple[str, Any], ...]) -> RealizedGeometry:
            params: dict[str, Any] = dict(args)
            return f(**params)

        primitive_registry.register(f.__name__, wrapper, overwrite=overwrite, meta=meta)
        return f

    if func is None:
        return decorator
    return decorator(func)


def register_primitive(
    name: str,
    func: PrimitiveFunc,
    *,
    overwrite: bool = False,
) -> None:
    """グローバルレジストリに primitive を登録する。

    Parameters
    ----------
    name : str
        op 名。
    func : PrimitiveFunc
        実体配列を生成する関数。
    overwrite : bool, optional
        既存エントリがある場合に上書きするかどうか。
    """
    primitive_registry.register(name, func, overwrite=overwrite)


def get_primitive(name: str) -> PrimitiveFunc:
    """グローバルレジストリから primitive を取得する。

    Parameters
    ----------
    name : str
        op 名。

    Returns
    -------
    PrimitiveFunc
        対応する生成関数。
    """
    return primitive_registry.get(name)
