# どこで: `src/grafix/api/primitives.py`。
# 何を: primitive Geometry ノードを生成する公開名前空間 G を提供する。
# なぜ: primitive 専用のファサードに分離し、責務を明確化するため。

from __future__ import annotations

from typing import Any, Callable

from grafix.core.geometry import Geometry
from grafix.core.parameters import caller_site_id
from grafix.core.primitive_registry import primitive_registry

# primitive 実装モジュールをインポートしてレジストリに登録させる。
from grafix.core.primitives import grid as _primitive_grid  # noqa: F401
from grafix.core.primitives import line as _primitive_line  # noqa: F401
from grafix.core.primitives import polygon as _primitive_polygon  # noqa: F401
from grafix.core.primitives import polyhedron as _primitive_polyhedron  # noqa: F401
from grafix.core.primitives import sphere as _primitive_sphere  # noqa: F401
from grafix.core.primitives import text as _primitive_text  # noqa: F401
from grafix.core.primitives import torus as _primitive_torus  # noqa: F401

from ._param_resolution import resolve_api_params, set_api_label


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

            # site_id は「呼び出し箇所」を識別するための安定 ID。
            # 同じ行（同じ呼び出し箇所）からの呼び出しであれば、フレームが変わっても同一になる。
            site_id = caller_site_id(skip=1)

            # ParamStore が利用できるコンテキスト（parameter_context）内なら、
            # G(name="...") のラベル情報を (op, site_id) に紐づけて保存する。
            # GUI 側でヘッダ表示に利用する想定。
            set_api_label(op=name, site_id=site_id, label=self._pending_label)

            # meta: GUI 表示対象や UI レンジなどの情報（組み込み primitive は meta ありを前提）。
            # defaults: meta に含まれる引数について、関数シグネチャから抽出した安全なデフォルト値。
            # これにより、G.circle() のように kwargs を省略しても ParamStore にキーが観測され、
            # GUI が空になりにくい。
            meta = primitive_registry.get_meta(name)
            defaults = primitive_registry.get_defaults(name)

            resolved = resolve_api_params(
                op=name,
                site_id=site_id,
                user_params=params,
                defaults=defaults,
                meta=meta,
            )
            # resolved は Geometry.create に渡され、正規化・署名化される。
            # primitive は inputs を持たないため op と params のみでノードが確定する。
            return Geometry.create(op=name, params=resolved)

        return factory

    def __call__(self, name: str | None = None) -> "PrimitiveNamespace":
        ns = PrimitiveNamespace()
        ns._pending_label = name  # type: ignore[attr-defined]
        return ns

    _pending_label: str | None = None


G = PrimitiveNamespace()
"""primitive Geometry ノードを生成する公開名前空間。"""

__all__ = ["G"]
