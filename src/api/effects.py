# どこで: `src/api/effects.py`。
# 何を: effect 適用パイプラインを組み立てる公開名前空間 E を提供する。
# なぜ: effect 専用のファサードに分離し、責務を明確化するため。

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from src.core.effect_registry import effect_registry
from src.core.geometry import Geometry

# effect 実装モジュールをインポートしてレジストリに登録させる。
from src.effects import scale as _effect_scale  # noqa: F401
from src.effects import rotate as _effect_rotate  # noqa: F401
from src.parameters import (
    caller_site_id,
    current_frame_params,
    current_param_store,
    resolve_params,
)


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

    steps: tuple[tuple[str, dict[str, Any], str], ...]
    label_name: str | None = None

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
        # effect チェーンは「入力 Geometry に対して、steps を順番に wrap していく」だけの処理。
        # ここでは実体変換は行わず、あくまで Geometry DAG（レシピ）を構築する。
        result = geometry
        for op, params, site_id in self.steps:
            # site_id は「その effect ステップが宣言された呼び出し箇所」。
            # 例: E.scale(...).rotate(...)(g) の scale と rotate を別の GUI 行として扱うため、
            # apply（__call__）時点ではなく「ステップ追加時点」で固定された site_id を使う。
            meta = effect_registry.get_meta(op)
            defaults = effect_registry.get_defaults(op)

            # explicit_args は「ユーザーがこのステップに明示的に渡した kwargs」。
            # defaults で補完された引数は explicit_args に含まれない。
            explicit_args = set(params.keys())

            # base_params は「デフォルト補完 → ユーザー指定で上書き」。
            # 省略した引数も観測され、GUI 行が空になりにくい。
            base_params = dict(defaults)
            base_params.update(params)

            # parameter_context 内なら、base/GUI/CC を解決して effective を確定し、
            # FrameParamsBuffer に観測レコード（explicit など）を積む。
            if current_frame_params() is not None:
                resolved = resolve_params(
                    op=op,
                    params=base_params,
                    meta=meta,
                    site_id=site_id,
                    explicit_args=explicit_args,
                )
            else:
                # parameter_context 外では ParamStore を参照せず、base をそのまま使う。
                resolved = base_params

            # E(name="...") で付与されたラベルは、各ステップの (op, site_id) に保存する。
            # GUI 側でヘッダ表示などに使う想定。
            if self.label_name is not None:
                store = current_param_store()
                if store is None:
                    raise RuntimeError(
                        "ParamStore が利用できないコンテキストで name 指定は使えません"
                    )
                store.set_label(op, site_id, self.label_name)

            # 直前までの result を inputs として 1 段 effect ノードを積む。
            # これを steps の数だけ繰り返すことでチェーン全体の DAG になる。
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
            return EffectBuilder(steps=new_steps, label_name=self.label_name)

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
            return EffectBuilder(
                steps=((name, dict(params), site_id),), label_name=self._pending_label
            )

        return factory

    def __call__(self, name: str | None = None) -> "EffectNamespace":
        ns = EffectNamespace()
        ns._pending_label = name  # type: ignore[attr-defined]
        return ns

    _pending_label: str | None = None


E = EffectNamespace()
"""effect 適用パイプラインを構築する公開名前空間。"""

__all__ = ["E"]
