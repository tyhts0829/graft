# src/core/primitive_registry.py
# Geometry の primitive ノードに対応する生成関数レジストリ。
# op 名から RealizedGeometry 生成関数を引けるようにする。

from __future__ import annotations

from typing import Any, Callable, Mapping

from src.core.realized_geometry import RealizedGeometry

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

    def register(
        self,
        name: str,
        func: PrimitiveFunc | None = None,
        *,
        overwrite: bool = False,
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
                self.register(name, f, overwrite=overwrite)
                return f

            return decorator

        if not overwrite and name in self._items:
            raise ValueError(f"primitive '{name}' は既に登録されている")
        self._items[name] = func
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

    def items(self) -> Mapping[str, PrimitiveFunc].items:  # type: ignore[valid-type]
        """登録済みエントリの (name, func) ビューを返す。"""
        return self._items.items()


primitive_registry = PrimitiveRegistry()
"""グローバルな primitive レジストリインスタンス。"""


def primitive(
    func: PrimitiveFunc | None = None,
    *,
    overwrite: bool = False,
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
    def circle(args):
        ...
    """

    def decorator(f: PrimitiveFunc) -> PrimitiveFunc:
        primitive_registry.register(f.__name__, f, overwrite=overwrite)
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
