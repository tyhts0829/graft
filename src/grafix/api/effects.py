# どこで: `src/grafix/api/effects.py`。
# 何を: effect 適用パイプラインを組み立てる公開名前空間 E を提供する。
# なぜ: effect 専用のファサードに分離し、責務を明確化するため。

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable, Literal

from ._param_resolution import resolve_api_params, set_api_label
from ._operation_selector import (
    FrozenParamsByTarget,
    freeze_effect_params_by_target,
    resolve_effect_selection,
    validate_effect_selector_n_inputs,
)
from grafix.core.geometry import Geometry
from grafix.core.operation_catalog import (
    OperationCatalog,
    current_operation_catalog,
)
from grafix.core.operation_declaration import EffectStepRef, OpDeclaration
from grafix.core.operation_selector import SelectorSpec
from grafix.core.parameters import (
    caller_site_id,
    current_effect_order_snapshot,
    current_frame_params,
)
from grafix.core.parameters.context import current_param_recording_enabled
from grafix.core.parameters.effects import (
    EffectStepTopology,
    resolve_effective_steps,
)
from grafix.core.parameters.identity import identity_string
from grafix.core.value_validation import exact_bool

from ._op_validation import validate_operation_kwargs
from ._operation_info import operation_info
from ._unset import _UNSET_TARGET, _UnsetTarget
from .operation_info import OperationInfo


@dataclass(frozen=True, slots=True, init=False)
class _EffectOperationStep:
    """通常 effect の canonical immutable step。"""

    declaration: OpDeclaration
    args: tuple[tuple[str, Any], ...]
    site_id: str

    @property
    def op(self) -> str:
        """step 作成時に固定した operation 名。"""

        return self.declaration.name

    @property
    def ref(self) -> EffectStepRef:
        """step 作成時に固定した evaluation/schema 参照。"""

        return self.declaration.effect_step_ref

    @property
    def n_inputs(self) -> int:
        """step 作成時に固定した入力数。"""

        return self.declaration.n_inputs

    @property
    def parameter_op(self) -> str:
        """Parameter topology で使う operation 名。"""

        return self.op


@dataclass(frozen=True, slots=True, init=False)
class _EffectSelectorStep:
    """DAG 構築時に実 effect step へ lower する selector 設定。"""

    target: str
    target_explicit: bool
    n_inputs: int
    params_by_target: FrozenParamsByTarget
    site_id: str

    @property
    def selector(self) -> SelectorSpec:
        """step 作成時に固定した selector schema。"""

        return self.params_by_target.selector

    @property
    def catalog(self) -> OperationCatalog:
        """selector と同時に固定した operation catalog。"""

        return self.params_by_target.catalog

    @property
    def parameter_op(self) -> str:
        """Parameter topology で使う selector operation 名。"""

        return self.selector.op


_EffectStep = _EffectOperationStep | _EffectSelectorStep


@dataclass(frozen=True, slots=True)
class _LoweredEffectStep:
    """通常 step / selector step を同じ DAG 構築形へ lower した値。"""

    parameter_op: str
    op: str
    args: tuple[tuple[str, Any], ...]
    site_id: str
    n_inputs: int
    ref: EffectStepRef
    cache_policy: Literal["content", "none"]


def _make_effect_operation_step(
    *,
    declaration: OpDeclaration,
    params: dict[str, Any],
    site_id: str,
) -> _EffectOperationStep:
    """検証済み通常 effect 引数を immutable step に固定する。"""

    step = object.__new__(_EffectOperationStep)
    if type(declaration) is not OpDeclaration or declaration.kind != "effect":
        raise TypeError("effect step には exact effect OpDeclaration が必要です")
    object.__setattr__(step, "declaration", declaration)
    object.__setattr__(step, "args", tuple(sorted(params.items())))
    object.__setattr__(
        step,
        "site_id",
        identity_string(site_id, name="effect step site_id"),
    )
    return step


def _make_effect_selector_step(
    *,
    target: str,
    target_explicit: bool,
    n_inputs: int,
    params_by_target: Mapping[str, Mapping[str, Any]] | None,
    site_id: str,
    catalog: OperationCatalog | None = None,
) -> _EffectSelectorStep:
    """公開 selector 引数を検証済み immutable step へ変換する。"""

    target_explicit_b = exact_bool(
        target_explicit,
        name="effect selector target_explicit",
    )
    count = validate_effect_selector_n_inputs(n_inputs)
    selected_catalog = current_operation_catalog() if catalog is None else catalog
    target_s, frozen_params = freeze_effect_params_by_target(
        identity_string(target, name="effect selector target"),
        params_by_target,
        n_inputs=count,
        catalog=selected_catalog,
    )
    step = object.__new__(_EffectSelectorStep)
    object.__setattr__(step, "target", target_s)
    object.__setattr__(
        step,
        "target_explicit",
        target_explicit_b,
    )
    object.__setattr__(step, "n_inputs", count)
    object.__setattr__(step, "params_by_target", frozen_params)
    object.__setattr__(
        step,
        "site_id",
        identity_string(site_id, name="effect selector site_id"),
    )
    return step


def _lower_effect_step(
    step: _EffectStep,
) -> _LoweredEffectStep:
    """通常/selector step を一つの immutable DAG 引数形へ lower する。"""

    if isinstance(step, _EffectSelectorStep):
        catalog = step.catalog
        selected = resolve_effect_selection(
            target=step.target,
            target_explicit=step.target_explicit,
            n_inputs=step.n_inputs,
            params_by_target=step.params_by_target,
            site_id=step.site_id,
        )
        declaration = catalog.resolve("effect", selected.target).declaration
        return _LoweredEffectStep(
            parameter_op=step.parameter_op,
            op=selected.target,
            args=tuple(sorted(selected.params.items())),
            site_id=step.site_id,
            n_inputs=step.n_inputs,
            ref=declaration.effect_step_ref,
            cache_policy=declaration.cache_policy,
        )

    # 通常 effect は factory lookup 時の exact immutable declaration を保持する。
    # 後から同名 declaration が登録されても、旧 schema の引数を新 evaluator へ
    # 混ぜず、Geometry には作成時の evaluation ref を固定する。
    declaration = step.declaration
    current_params = validate_operation_kwargs(
        op=step.op,
        spec=declaration,
        params=dict(step.args),
    )
    resolved = resolve_api_params(
        op=step.op,
        site_id=step.site_id,
        user_params=current_params,
        defaults=declaration.schema.defaults,
        meta=declaration.schema.meta,
    )
    return _LoweredEffectStep(
        parameter_op=step.parameter_op,
        op=step.op,
        args=tuple(sorted(resolved.items())),
        site_id=step.site_id,
        n_inputs=declaration.n_inputs,
        ref=step.ref,
        cache_policy=declaration.cache_policy,
    )


@dataclass(frozen=True, slots=True)
class EffectBuilder:
    """effect 適用パイプラインを表現するビルダ。

    Parameters
    ----------
    steps : tuple[_EffectStep, ...]
        通常 effect または selector の immutable step 列。

    Notes
    -----
    E.scale(...).rotate(...)(g) のようにメソッドチェーンで
    Geometry に対する effect パイプラインを構築する。step と引数はすべて
    immutable tuple に固定されるため、builder は値として比較・hash 化できる。
    """

    steps: tuple[_EffectStep, ...]
    chain_id: str
    label_name: str | None = None

    def __post_init__(self) -> None:
        """step 列と identity を canonical immutable 形に限定する。"""

        if type(self.steps) is not tuple or any(
            type(step) not in {_EffectOperationStep, _EffectSelectorStep}
            for step in self.steps
        ):
            raise TypeError("EffectBuilder.steps は immutable effect step tuple が必要です")
        object.__setattr__(
            self,
            "chain_id",
            identity_string(self.chain_id, name="effect chain_id"),
        )
        if self.label_name is not None:
            object.__setattr__(
                self,
                "label_name",
                identity_string(self.label_name, name="effect label"),
            )

    def __hash__(self) -> int:
        """catalog object identity に依存しない immutable step hash を返す。"""

        step_keys: list[object] = []
        for step in self.steps:
            if isinstance(step, _EffectOperationStep):
                step_keys.append(
                    (
                        "operation",
                        step.ref,
                        step.args,
                        step.site_id,
                    )
                )
            else:
                step_keys.append(
                    (
                        "selector",
                        step.selector.fingerprint,
                        step.target,
                        step.target_explicit,
                        step.n_inputs,
                        step.params_by_target.values,
                        step.site_id,
                    )
                )
        return hash((tuple(step_keys), self.chain_id, self.label_name))

    def __call__(self, geometry: Geometry, *more_geometries: Geometry) -> Geometry:
        """保持している effect 列を Geometry に適用する。

        Parameters
        ----------
        geometry : Geometry
            入力 Geometry（1 つ目）。
        *more_geometries : Geometry
            追加入力 Geometry（multi-input effect 用）。

        Returns
        -------
        Geometry
            すべての effect を適用した Geometry。
        """
        # effect チェーンは「入力 Geometry に対して、steps を順番に wrap していく」だけの処理。
        # ここでは実体変換は行わず、あくまで Geometry DAG（レシピ）を構築する。
        code_topology: list[EffectStepTopology] = []
        for code_index, step in enumerate(self.steps):
            code_topology.append(
                EffectStepTopology(
                    op=step.parameter_op,
                    site_id=step.site_id,
                    n_inputs=step.n_inputs,
                    code_index=code_index,
                )
            )

        recording_enabled = current_param_recording_enabled()
        topology = tuple(code_topology)
        order_snapshot = current_effect_order_snapshot() if recording_enabled else {}
        order_override = order_snapshot.get(self.chain_id)
        if order_override is not None:
            topology_keys = tuple(step.key for step in topology)
            if (
                len(order_override) != len(topology_keys)
                or set(order_override) != set(topology_keys)
            ):
                # source reload で code topology が置換された最初の成功観測は
                # 新 topology を正本とする。context 終了時の merge が旧 override
                # を削除するまで、この evaluation だけ code order を使用する。
                order_override = None
        effective_topology = resolve_effective_steps(
            topology,
            order_override,
        )
        frame_params = current_frame_params()
        if recording_enabled and frame_params is not None:
            frame_params.record_effect_chain(
                chain_id=self.chain_id,
                steps=topology,
            )

        first_inputs = (geometry, *more_geometries)
        result = geometry
        for step_index, topology_step in enumerate(effective_topology):
            step = self.steps[topology_step.code_index]
            lowered = _lower_effect_step(step)
            set_api_label(
                op=lowered.parameter_op,
                site_id=lowered.site_id,
                label=self.label_name,
            )

            # 直前までの result を inputs として 1 段 effect ノードを積む。
            # これを steps の数だけ繰り返すことでチェーン全体の DAG になる。
            n_inputs = lowered.n_inputs
            if step_index == 0:
                if len(first_inputs) != n_inputs:
                    raise TypeError(
                        f"effect {lowered.op!r} は入力 Geometry を {n_inputs} 個必要とします"
                    )
                inputs = first_inputs
            else:
                if n_inputs != 1:
                    raise TypeError(
                        "multi-input effect はチェーンの先頭にのみ使用できます"
                        f": {lowered.op!r}"
                    )
                inputs = (result,)
            result = Geometry._from_canonical_args(
                op=lowered.op,
                operation=lowered.ref.operation,
                inputs=inputs,
                args=lowered.args,
                cache_policy=lowered.cache_policy,
            )
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

        catalog = current_operation_catalog()
        try:
            declaration = catalog.resolve("effect", name).declaration
        except KeyError:
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

            key = params.pop("key", None)
            instance_key = params.pop("instance_key", None)
            shared = params.pop("shared", False)
            params = validate_operation_kwargs(
                op=name,
                spec=declaration,
                params=params,
            )
            site_id = caller_site_id(
                skip=1,
                key=key,
                instance_key=instance_key,
                shared=shared,
            )
            new_steps = self.steps + (
                _make_effect_operation_step(
                    declaration=declaration,
                    params=params,
                    site_id=site_id,
                ),
            )
            return EffectBuilder(
                steps=new_steps,
                chain_id=self.chain_id,
                label_name=self.label_name,
            )

        return factory

    def select(
        self,
        *,
        target: str | _UnsetTarget = _UNSET_TARGET,
        n_inputs: Literal[1] = 1,
        params_by_target: Mapping[str, Mapping[str, Any]] | None = None,
        key: str | int | None = None,
        instance_key: str | int | None = None,
        shared: bool = False,
    ) -> "EffectBuilder":
        """チェーン末尾へ arity 互換な effect selector を追加する。

        Parameters
        ----------
        target : str, optional
            code 側の初期 effect 名。Parameter GUI から上書きできる。
        n_inputs : int, optional
            選択候補の入力 Geometry 数。チェーン中段では 1 のみ指定できる。
        params_by_target : Mapping[str, Mapping[str, Any]] or None, optional
            effect 名ごとの base keyword 引数。
        key : str or int or None, optional
            コード移動に強い semantic parameter identity。
        instance_key : str or int or None, optional
            loop/comprehension の反復 instance identity。
        shared : bool, optional
            同じ semantic site を反復呼び出し間で共有するか。

        Returns
        -------
        EffectBuilder
            selector step を追加した immutable builder。
        """

        count = validate_effect_selector_n_inputs(n_inputs)
        if self.steps and count != 1:
            raise TypeError(
                "multi-input effect selector はチェーンの先頭にのみ使用できます"
            )
        site_id = caller_site_id(
            skip=1,
            key=key,
            instance_key=instance_key,
            shared=shared,
        )
        target_explicit = target is not _UNSET_TARGET
        target_name = (
            identity_string(target, name="effect selector target")
            if target_explicit
            else "rotate"
        )
        catalog = current_operation_catalog()
        step = _make_effect_selector_step(
            target=target_name,
            target_explicit=target_explicit,
            n_inputs=count,
            params_by_target=params_by_target,
            site_id=site_id,
            catalog=catalog,
        )
        return EffectBuilder(
            steps=(*self.steps, step),
            chain_id=self.chain_id,
            label_name=self.label_name,
        )


class EffectNamespace:
    """effect ビルダを提供する名前空間。

    Attributes
    ----------
    <name> : Callable[..., EffectBuilder]
        登録済み effect 名ごとのビルダファクトリ。
        例: E.scale(scale=(2.0, 2.0, 2.0))(g) -> Geometry(op="scale", inputs=(g,), params=...)
    """

    def catalog(self) -> tuple[OperationInfo, ...]:
        """登録済み effect の catalog を名前順で返す。

        Returns
        -------
        tuple[OperationInfo, ...]
            名前、説明、引数、source を含む immutable entry の列。
        """

        return tuple(
            operation_info(entry)
            for entry in current_operation_catalog().public_entries(kind="effect")
        )

    def describe(self, name: str) -> OperationInfo:
        """effect の catalog entry を名前で取得する。

        Parameters
        ----------
        name : str
            effect 名。

        Returns
        -------
        OperationInfo
            evaluator を含まない immutable inspection value。

        Raises
        ------
        KeyError
            ``name`` が未登録の場合。
        """

        name_s = identity_string(name, name="effect name")
        catalog = current_operation_catalog()
        try:
            return operation_info(catalog.resolve("effect", name_s))
        except KeyError:
            raise KeyError(f"未登録の effect: {name_s!r}")

    def select(
        self,
        *,
        target: str | _UnsetTarget = _UNSET_TARGET,
        n_inputs: int = 1,
        params_by_target: Mapping[str, Mapping[str, Any]] | None = None,
        key: str | int | None = None,
        instance_key: str | int | None = None,
        shared: bool = False,
    ) -> EffectBuilder:
        """登録済み effect を選択する builder を返す。

        Parameters
        ----------
        target : str, optional
            code 側の初期 effect 名。Parameter GUI から上書きできる。
        n_inputs : int, optional
            選択候補と適用時の入力 Geometry 数。
        params_by_target : Mapping[str, Mapping[str, Any]] or None, optional
            effect 名ごとの base keyword 引数。
        key : str or int or None, optional
            コード移動に強い semantic parameter identity。
        instance_key : str or int or None, optional
            loop/comprehension の反復 instance identity。
        shared : bool, optional
            同じ semantic site を反復呼び出し間で共有するか。

        Returns
        -------
        EffectBuilder
            適用時に選択 target へ lower する immutable builder。
        """

        site_id = caller_site_id(
            skip=1,
            key=key,
            instance_key=instance_key,
            shared=shared,
        )
        target_explicit = target is not _UNSET_TARGET
        target_name = (
            identity_string(target, name="effect selector target")
            if target_explicit
            else "rotate"
        )
        catalog = current_operation_catalog()
        step = _make_effect_selector_step(
            target=target_name,
            target_explicit=target_explicit,
            n_inputs=n_inputs,
            params_by_target=params_by_target,
            site_id=site_id,
            catalog=catalog,
        )
        return EffectBuilder(
            steps=(step,),
            chain_id=site_id,
            label_name=self._pending_label,
        )

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

        catalog = current_operation_catalog()
        try:
            declaration = catalog.resolve("effect", name).declaration
        except KeyError:
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

            key = params.pop("key", None)
            instance_key = params.pop("instance_key", None)
            shared = params.pop("shared", False)
            params = validate_operation_kwargs(
                op=name,
                spec=declaration,
                params=params,
            )
            site_id = caller_site_id(
                skip=1,
                key=key,
                instance_key=instance_key,
                shared=shared,
            )
            return EffectBuilder(
                steps=(
                    _make_effect_operation_step(
                        declaration=declaration,
                        params=params,
                        site_id=site_id,
                    ),
                ),
                chain_id=site_id,
                label_name=self._pending_label,
            )

        return factory

    def __call__(self, name: str | None = None) -> "EffectNamespace":
        ns = EffectNamespace()
        ns._pending_label = (
            None if name is None else identity_string(name, name="effect label")
        )
        return ns

    _pending_label: str | None = None


E = EffectNamespace()
"""effect 適用パイプラインを構築する公開名前空間。"""

__all__ = ["E"]
