# どこで: `src/api/effects.py`。
# 何を: effect 適用パイプラインを組み立てる公開名前空間 E を提供する。
# なぜ: effect 専用のファサードに分離し、責務を明確化するため。

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Tuple

from src.core.effect_registry import effect_registry
from src.core.geometry import Geometry
from src.parameters import (
    caller_site_id,
    current_frame_params,
    resolve_params,
)

# effect 実装モジュールをインポートしてレジストリに登録させる。
from src.effects import scale as _effect_scale  # noqa: F401


@dataclass(frozen=True, slots=True)
class EffectBuilder:
    """effect 適用パイプラインを表現するビルダ。

    Parameters
    ----------
    steps : tuple[tuple[str, dict[str, Any], str], ...]
        適用する effect 名とパラメータの列。

    Notes
    -----
    E.scale(...).rotate(...)(g) のようにメソッドチェーンで
    Geometry に対する effect パイプラインを構築する。
    """

    steps: Tuple[Tuple[str, dict[str, Any], str], ...]

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
        for op, params, site_id in self.steps:
            meta = effect_registry.get_meta(op)
            if current_frame_params() is not None:
                resolved = resolve_params(
                    op=op,
                    params=params,
                    meta=meta,
                    site_id=site_id,
                )
            else:
                resolved = params
            result = Geometry.create(op=op, inputs=(result,), params=resolved)
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

            site_id = caller_site_id(skip=1)
            new_steps = self.steps + ((name, dict(params), site_id),)
            return EffectBuilder(steps=new_steps)

        return factory


class EffectNamespace:
    """effect ビルダを提供する名前空間。

    Attributes
    ----------
    <name> : Callable[..., EffectBuilder]
        登録済み effect 名ごとのビルダファクトリ。
        例: E.scale(s=2.0)(g) -> Geometry(op="scale", inputs=(g,), params=...)
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

            site_id = caller_site_id(skip=1)
            return EffectBuilder(steps=((name, dict(params), site_id),))

        return factory


E = EffectNamespace()
"""effect 適用パイプラインを構築する公開名前空間。"""

__all__ = ["E"]
