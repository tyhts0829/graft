# どこで: `src/grafix/api/primitives.py`。
# 何を: primitive Geometry ノードを生成する公開名前空間 G を提供する。
# なぜ: primitive 専用のファサードに分離し、責務を明確化するため。

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from grafix.core.geometry import Geometry
from grafix.core.operation_catalog import current_operation_catalog
from grafix.core.operation_selector import selector_spec as build_selector_spec
from grafix.core.parameters import caller_site_id
from grafix.core.parameters.identity import identity_string

from ._op_validation import validate_operation_kwargs
from ._operation_selector import (
    freeze_params_by_target,
    resolve_primitive_selection,
)
from ._operation_info import operation_info
from ._param_resolution import resolve_api_params, set_api_label
from ._unset import _UNSET_TARGET, _UnsetTarget
from .operation_info import OperationInfo


class PrimitiveNamespace:
    """primitive Geometry ノードを生成する名前空間。

    Attributes
    ----------
    <name> : Callable[..., Geometry]
        登録済み primitive 名ごとのファクトリ。
        例: G.circle(radius=1.0) -> Geometry
    """

    def catalog(self) -> tuple[OperationInfo, ...]:
        """登録済み primitive の catalog を名前順で返す。

        Returns
        -------
        tuple[OperationInfo, ...]
            名前、説明、引数、source を含む immutable entry の列。
        """

        return tuple(
            operation_info(entry)
            for entry in current_operation_catalog().public_entries(kind="primitive")
        )

    def describe(self, name: str) -> OperationInfo:
        """primitive の catalog entry を名前で取得する。

        Parameters
        ----------
        name : str
            primitive 名。

        Returns
        -------
        OperationInfo
            evaluator を含まない immutable inspection value。

        Raises
        ------
        KeyError
            ``name`` が未登録の場合。
        """

        name_s = identity_string(name, name="primitive name")
        catalog = current_operation_catalog()
        try:
            return operation_info(catalog.resolve("primitive", name_s))
        except KeyError:
            raise KeyError(f"未登録の primitive: {name_s!r}")

    def select(
        self,
        *,
        target: str | _UnsetTarget = _UNSET_TARGET,
        params_by_target: Mapping[str, Mapping[str, Any]] | None = None,
        key: str | int | None = None,
        instance_key: str | int | None = None,
        shared: bool = False,
    ) -> Geometry:
        """登録済み primitive を選択して実 target の Geometry を生成する。

        Parameters
        ----------
        target : str, optional
            code 側の初期 primitive 名。Parameter GUI から上書きできる。
        params_by_target : Mapping[str, Mapping[str, Any]] or None, optional
            primitive 名ごとの base keyword 引数。
        key : str or int or None, optional
            コード移動に強い semantic parameter identity。
        instance_key : str or int or None, optional
            loop/comprehension の反復 instance identity。
        shared : bool, optional
            同じ semantic site を反復呼び出し間で共有するか。

        Returns
        -------
        Geometry
            選択された実 primitive を op に持つ Geometry。
        """

        catalog = current_operation_catalog()
        selector = build_selector_spec(catalog, kind="primitive", n_inputs=0)
        frozen_params = freeze_params_by_target(
            params_by_target,
            kind="primitive",
            catalog=catalog,
            selector=selector,
        )
        site_id = caller_site_id(
            skip=1,
            key=key,
            instance_key=instance_key,
            shared=shared,
        )
        target_explicit = target is not _UNSET_TARGET
        target_name = (
            identity_string(target, name="primitive selector target")
            if target_explicit
            else "circle"
        )
        selected = resolve_primitive_selection(
            target=target_name,
            target_explicit=target_explicit,
            params_by_target=frozen_params,
            site_id=site_id,
            catalog=catalog,
            selector=selector,
        )
        set_api_label(
            op=selected.selector_op,
            site_id=site_id,
            label=self._pending_label,
        )
        declaration = catalog.resolve("primitive", selected.target).declaration
        return Geometry._from_canonical_args(
            op=selected.target,
            operation=declaration.ref,
            inputs=(),
            args=tuple(sorted(selected.params.items())),
            cache_policy=declaration.cache_policy,
        )

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

        catalog = current_operation_catalog()
        try:
            declaration = catalog.resolve("primitive", name).declaration
        except KeyError:
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

            key = params.pop("key", None)
            instance_key = params.pop("instance_key", None)
            shared = params.pop("shared", False)
            params = validate_operation_kwargs(
                op=name,
                spec=declaration,
                params=params,
            )

            # key は semantic site、instance_key は loop/comprehension 内の個体を表す。
            # shared=True は個体 suffix を付けず、同じ semantic site を意図的に共有する。
            site_id = caller_site_id(
                skip=1,
                key=key,
                instance_key=instance_key,
                shared=shared,
            )

            # ParamStore が利用できるコンテキスト（parameter_context）内なら、
            # G(name="...") のラベル情報を (op, site_id) に紐づけて保存する。
            # GUI 側でヘッダ表示に利用する想定。
            set_api_label(op=name, site_id=site_id, label=self._pending_label)

            # meta: GUI 表示対象や UI レンジなどの情報（組み込み primitive は meta ありを前提）。
            # defaults: meta に含まれる引数について、関数シグネチャから抽出した安全なデフォルト値。
            # これにより、G.circle() のように kwargs を省略しても ParamStore にキーが観測され、
            # GUI が空になりにくい。
            resolved = resolve_api_params(
                op=name,
                site_id=site_id,
                user_params=params,
                defaults=declaration.schema.defaults,
                meta=declaration.schema.meta,
            )
            # 値は operation validator / parameter resolver で canonical 化済み。
            # 再走査せず、署名計算だけを行う core-owned factory へ渡す。
            return Geometry._from_canonical_args(
                op=name,
                operation=declaration.ref,
                inputs=(),
                args=tuple(sorted(resolved.items())),
                cache_policy=declaration.cache_policy,
            )

        return factory

    def __call__(self, name: str | None = None) -> "PrimitiveNamespace":
        ns = PrimitiveNamespace()
        ns._pending_label = (
            None if name is None else identity_string(name, name="primitive label")
        )
        return ns

    _pending_label: str | None = None


G = PrimitiveNamespace()
"""primitive Geometry ノードを生成する公開名前空間。"""

__all__ = ["G"]
