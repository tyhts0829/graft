# どこで: `src/api/primitives.py`。
# 何を: primitive Geometry ノードを生成する公開名前空間 G を提供する。
# なぜ: primitive 専用のファサードに分離し、責務を明確化するため。

from __future__ import annotations

from typing import Any, Callable

from src.core.geometry import Geometry
from src.core.primitive_registry import primitive_registry
from src.parameters import current_param_snapshot, current_frame_params, caller_site_id, resolve_params

# primitive 実装モジュールをインポートしてレジストリに登録させる。
from src.primitives import circle as _primitive_circle  # noqa: F401


class PrimitiveNamespace:
    """primitive Geometry ノードを生成する名前空間。

    Attributes
    ----------
    <name> : Callable[..., Geometry]
        登録済み primitive 名ごとのファクトリ。
        例: G.circle(r=1.0) -> Geometry(op="circle", inputs=(), params=...)
    """

    def __getattr__(self, name: str) -> Callable[..., Geometry]:
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

            site_id = caller_site_id(skip=1)

            # meta を取得（未登録は空）
            meta = primitive_registry.get_meta(name)
            # コンテキストが無ければ素通し
            if current_param_snapshot() and current_frame_params() is not None:
                resolved, param_steps = resolve_params(
                    op=name,
                    params=params,
                    meta=meta,
                    site_id=site_id,
                )
            else:
                resolved = params
                param_steps = {}

            return Geometry.create(op=name, params=resolved, param_steps=param_steps)

        return factory


G = PrimitiveNamespace()
"""primitive Geometry ノードを生成する公開名前空間。"""

__all__ = ["G"]
