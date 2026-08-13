"""
Purpose:
    Geometryを描画styleとG-code policyを持つLayerへ束ねる公開名前空間Lを提供する。
Use when:
    layer生成、label/site identity、style指定、または複数Geometryのlayer化を変更する場合。
Constraints:
    - styleと`gcode_optimize`をGeometry DAGおよびgeometry cache identityから分離する。
    - parameter用site identityをGeometry内容の変化から独立させる。
"""

from __future__ import annotations

from typing import Sequence

from grafix.core.geometry import Geometry
from grafix.core.parameters import caller_site_id, current_param_store
from grafix.core.parameters.labels_ops import set_label
from grafix.core.parameters.layer_style import LAYER_STYLE_OP
from grafix.core.layer import Layer


class LayerNamespace:
    """Geometry を Layer 化する名前空間。

    Notes
    -----
    `G/E/P` と同様に、`L(name="...")` で pending なラベルを持つ別インスタンスを返す。
    生成は `L.layer(..., color=..., thickness=...)` で行う。
    """

    def __call__(
        self,
        name: str | None = None,
    ) -> "LayerNamespace":
        if name is not None and not isinstance(name, str):
            raise TypeError(f"L(name=...) は str のみ受け付けます: {type(name)!r}")
        ns = LayerNamespace()
        ns._pending_name = name  # type: ignore[attr-defined]
        return ns

    def layer(
        self,
        geometry_or_list: Geometry | Sequence[Geometry],
        *,
        key: str | int | None = None,
        instance_key: str | int | None = None,
        shared: bool = False,
        color: tuple[float, float, float] | None = None,
        thickness: float | None = None,
        gcode_optimize: bool = True,
    ) -> Layer:
        """単体/複数の Geometry から Layer を生成する。

        Parameters
        ----------
        geometry_or_list : Geometry or Sequence[Geometry]
            入力 Geometry または Geometry の列。
        key : str or int or None, optional
            行移動に影響されない semantic site key。
        instance_key : str or int or None, optional
            loop/comprehension 内で Layer style を個別調整するための instance key。
        shared : bool, optional
            True なら同じ semantic site の反復 Layer で style group を共有する。
            ``instance_key`` との同時指定はできない。
        color : tuple[float, float, float] or None, optional
            RGB 色。None の場合は既定値に委譲。
        thickness : float or None, optional
            線幅。None の場合は既定値に委譲。0 以下は拒否。
        gcode_optimize : bool, optional
            True なら global な G-code 最適化設定群をこの Layer へ適用する。
            False なら並べ替え、反転、bridge をこの Layer では無効にする。

        Returns
        -------
        Layer
            単一の Layer。複数 Geometry は内部で concat する。

        Raises
        ------
        TypeError
            Geometry 以外、または``gcode_optimize``へbool以外が渡された場合。
        ValueError
            thickness が 0 以下の場合、または空リストの場合。
        """

        resolved_name = self._pending_name

        # geometry_or_list を Geometry のリストに正規化する。
        geometries: list[Geometry]
        if isinstance(geometry_or_list, Geometry):
            geometries = [geometry_or_list]
        elif isinstance(geometry_or_list, Sequence):
            geometries = []
            for g in geometry_or_list:
                if not isinstance(g, Geometry):
                    raise TypeError(
                        f"L.layer には Geometry だけを渡してください: {type(g)!r}"
                    )
                geometries.append(g)
        else:
            raise TypeError(
                "L.layer は Geometry またはその列のみを受け付けます:"
                f" {type(geometry_or_list)!r}"
            )

        if not geometries:
            raise ValueError("L.layer に空の Geometry リストは渡せません")

        # site_id は「この Layer が生成された呼び出し箇所」を識別する安定 ID。
        # Layer style（line_thickness/line_color）の行は、この site_id をキーとして保存する。
        site_id = caller_site_id(
            skip=1,
            key=key,
            instance_key=instance_key,
            shared=shared,
        )

        store = current_param_store()
        if store is not None and resolved_name is not None:
            set_label(store, op=LAYER_STYLE_OP, site_id=site_id, label=resolved_name)

        # 複数 Geometry は concat で 1 Layer にまとめる。
        if len(geometries) == 1:
            geometry = geometries[0]
        else:
            geometry = Geometry.concat(geometries)

        return Layer(
            geometry=geometry,
            site_id=site_id,
            color=color,
            thickness=thickness,
            name=resolved_name,
            gcode_optimize=gcode_optimize,
        )

    _pending_name: str | None = None


L = LayerNamespace()
"""Geometry を Layer 化する公開名前空間。"""

__all__ = ["L"]
