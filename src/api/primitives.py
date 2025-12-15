# どこで: `src/api/primitives.py`。
# 何を: primitive Geometry ノードを生成する公開名前空間 G を提供する。
# なぜ: primitive 専用のファサードに分離し、責務を明確化するため。

from __future__ import annotations

from typing import Any, Callable

from src.core.geometry import Geometry
from src.core.primitive_registry import primitive_registry
from src.parameters import (
    caller_site_id,
    current_frame_params,
    current_param_store,
    resolve_params,
)

# primitive 実装モジュールをインポートしてレジストリに登録させる。
from src.primitives import circle as _primitive_circle  # noqa: F401
from src.primitives import grid as _primitive_grid  # noqa: F401
from src.primitives import line as _primitive_line  # noqa: F401
from src.primitives import polygon as _primitive_polygon  # noqa: F401
from src.primitives import polyhedron as _primitive_polyhedron  # noqa: F401
from src.primitives import sphere as _primitive_sphere  # noqa: F401
from src.primitives import torus as _primitive_torus  # noqa: F401


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
            store = current_param_store()
            if self._pending_label is not None:
                if store is None:
                    raise RuntimeError(
                        "ParamStore が利用できないコンテキストで name 指定は使えません"
                    )
                store.set_label(name, site_id, self._pending_label)

            # meta: GUI 表示対象や UI レンジなどの情報（組み込み primitive は meta ありを前提）。
            # defaults: meta に含まれる引数について、関数シグネチャから抽出した安全なデフォルト値。
            # これにより、G.circle() のように kwargs を省略しても ParamStore にキーが観測され、
            # GUI が空になりにくい。
            meta = primitive_registry.get_meta(name)
            defaults = primitive_registry.get_defaults(name)

            # explicit_args: ユーザーがこの呼び出しで「明示的に渡した」kwargs のキー集合。
            # 初回観測時の override 初期値を
            #   - 明示引数: override=False（コードの base を優先）
            #   - 省略引数: override=True（GUI の ui_value を優先）
            # とするポリシーに利用する。
            explicit_args = set(params.keys())

            # base_params は「デフォルト補完 → ユーザー指定で上書き」の順で作る。
            # 省略した引数は defaults が入り、明示した引数は params が勝つ。
            base_params = dict(defaults)
            base_params.update(params)

            # parameter_context 内（=現在フレームを観測している）なら、
            # base/GUI/CC を解決して effective 値を確定しつつ、FrameParamsBuffer に記録する。
            if current_frame_params() is not None:
                resolved = resolve_params(
                    op=name,
                    params=base_params,
                    meta=meta,
                    site_id=site_id,
                    explicit_args=explicit_args,
                )
            else:
                # parameter_context 外では ParamStore を使った解決を行わない（単純に base を使う）。
                resolved = base_params
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
