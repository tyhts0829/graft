# src/api/api.py
# ルート api パッケージとして公開する G/E/L 名前空間 API の実装モジュール。
# Geometry レシピ DAG を直接扱わずに、primitive/effect 名から Geometry を組み立てるための薄いファサードを提供する。

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence, Tuple

from src.core.effect_registry import effect_registry
from src.core.geometry import Geometry
from src.core.primitive_registry import primitive_registry
from src.effects import scale as _effect_scale  # noqa: F401

# primitive/effect 実装モジュールをインポートしてレジストリに登録させる。
from src.primitives import circle as _primitive_circle  # noqa: F401
from src.render.layer import Layer

GeometryFactory = Callable[..., Geometry]


class PrimitiveNamespace:
    """primitive Geometry ノードを生成する名前空間。

    Attributes
    ----------
    <name> : Callable[..., Geometry]
        登録済み primitive 名ごとのファクトリ。
        例: G.circle(r=1.0) -> Geometry(op="circle", inputs=(), args=...)
    """

    def __getattr__(self, name: str) -> GeometryFactory:
        """primitive 名に対応する Geometry ファクトリを返す。

        Parameters
        ----------
        name : str
            primitive 名。

        Returns
        -------
        Callable[..., Geometry]
            Geometry ノードを生成する関数。

        Raises
        ------
        AttributeError
            未登録の primitive 名が指定された場合。
        """
        if name.startswith("_"):
            raise AttributeError(name)

        if name not in primitive_registry:
            raise AttributeError(f"未登録の primitive: {name!r}")

        def factory(**params: Any) -> Geometry:
            """primitive Geometry ノードを生成する。

            Parameters
            ----------
            **params : Any
                primitive に渡すパラメータ辞書。

            Returns
            -------
            Geometry
                生成された Geometry ノード。
            """

            return Geometry.create(op=name, params=params)

        return factory


@dataclass(frozen=True, slots=True)
class EffectBuilder:
    """effect 適用パイプラインを表現するビルダ。

    Parameters
    ----------
    steps : tuple[tuple[str, dict[str, Any]], ...]
        適用する effect 名とパラメータの列。

    Notes
    -----
    E.scale(...).rotate(...)(g) のようにメソッドチェーンで
    Geometry に対する effect パイプラインを構築する。
    """

    steps: Tuple[Tuple[str, dict[str, Any]], ...]

    def __call__(self, geometry: Geometry) -> Geometry:
        """保持している effect 列を Geometry に適用する。

        Parameters
        ----------
        geometry : Geometry
            入力 Geometry。

        Returns
        -------
        Geometry
            すべての effect を適用した Geometry。
        """
        result = geometry
        for op, params in self.steps:
            result = Geometry.create(op=op, inputs=(result,), params=params)
        return result

    def __getattr__(self, name: str) -> Callable[..., "EffectBuilder"]:
        """effect 名に対応するチェーン用ファクトリを返す。

        Parameters
        ----------
        name : str
            effect 名。

        Returns
        -------
        Callable[..., EffectBuilder]
            追加の effect を連結した新しい EffectBuilder を返す関数。

        Raises
        ------
        AttributeError
            未登録の effect 名が指定された場合。
        """
        if name.startswith("_"):
            raise AttributeError(name)

        if name not in effect_registry:
            raise AttributeError(f"未登録の effect: {name!r}")

        def factory(**params: Any) -> "EffectBuilder":
            """effect を 1 つ追加した EffectBuilder を生成する。

            Parameters
            ----------
            **params : Any
                effect に渡すパラメータ辞書。

            Returns
            -------
            EffectBuilder
                既存の steps に 1 つ追加したビルダ。
            """

            new_steps = self.steps + ((name, dict(params)),)
            return EffectBuilder(steps=new_steps)

        return factory


class EffectNamespace:
    """effect ビルダを提供する名前空間。

    Attributes
    ----------
    <name> : Callable[..., EffectBuilder]
        登録済み effect 名ごとのビルダファクトリ。
        例: E.scale(s=2.0)(g) -> Geometry(op="scale", inputs=(g,), args=...)
    """

    def __getattr__(self, name: str) -> Callable[..., EffectBuilder]:
        """effect 名に対応する EffectBuilder ファクトリを返す。

        Parameters
        ----------
        name : str
            effect 名。

        Returns
        -------
        Callable[..., EffectBuilder]
            EffectBuilder を返す関数。

        Raises
        ------
        AttributeError
            未登録の effect 名が指定された場合。
        """
        if name.startswith("_"):
            raise AttributeError(name)

        if name not in effect_registry:
            raise AttributeError(f"未登録の effect: {name!r}")

        def factory(**params: Any) -> EffectBuilder:
            """単一 effect からなる EffectBuilder を生成する。

            Parameters
            ----------
            **params : Any
                effect に渡すパラメータ辞書。

            Returns
            -------
            EffectBuilder
                1 つの effect を保持するビルダ。
            """

            return EffectBuilder(steps=((name, dict(params)),))

        return factory


G = PrimitiveNamespace()
"""primitive Geometry ノードを生成する公開名前空間。"""

E = EffectNamespace()
"""effect 適用パイプラインを構築する公開名前空間。"""


class LayerHelper:
    """Geometry にスタイルを付けて Layer のリストを生成するヘルパ。"""

    def __call__(
        self,
        geometry_or_list: Geometry | Sequence[Geometry],
        *,
        color: tuple[float, float, float] | None = None,
        thickness: float | None = None,
        name: str | None = None,
    ) -> list[Layer]:
        """単体/複数の Geometry から Layer を生成する。

        Parameters
        ----------
        geometry_or_list : Geometry or Sequence[Geometry]
            入力 Geometry または Geometry の列。
        color : tuple[float, float, float] or None, optional
            RGB 色。None の場合は既定値に委譲。
        thickness : float or None, optional
            線幅。None の場合は既定値に委譲。0 以下は拒否。
        name : str or None, optional
            Layer 名（任意）。

        Returns
        -------
        list[Layer]
            生成された Layer のリスト。

        Raises
        ------
        TypeError
            Geometry 以外が渡された場合。
        ValueError
            thickness が 0 以下の場合。
        """
        if thickness is not None and thickness <= 0:
            raise ValueError("thickness は正の値である必要がある")

        # geometry_or_list を Geometry のリストに正規化する。
        geometries: list[Geometry]
        if isinstance(geometry_or_list, Geometry):
            geometries = [geometry_or_list]
        elif isinstance(geometry_or_list, Sequence):
            geometries = []
            for g in geometry_or_list:
                if not isinstance(g, Geometry):
                    raise TypeError(
                        f"L には Geometry だけを渡してください: {type(g)!r}"
                    )
                geometries.append(g)
        else:
            raise TypeError(
                f"L は Geometry またはその列のみを受け付けます: {type(geometry_or_list)!r}"
            )

        return [
            Layer(geometry=g, color=color, thickness=thickness, name=name)
            for g in geometries
        ]


L = LayerHelper()
"""Geometry を Layer 化する公開ヘルパ。"""

__all__ = ["E", "G", "L"]
