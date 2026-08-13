"""
Purpose:
    user draw の Geometry/Layer/ネスト container を、共通 pipeline が扱う順序付き Layer 列へ正規化する。
Use when:
    draw 戻り値の受理型、flatten 順、暗黙 Layer の parameter identity を変更・調査する場合。
Constraints:
    - 再帰 container として受理するのは list/tuple だけとし、generator・set・任意 ``Sequence`` を暗黙に展開しない。
    - input の描画順を維持する。
    - bare Geometry の implicit ``site_id`` は出現順で安定化し、GeometryId の変化に連動させない。
See:
    grafix.core.pipeline
"""

from __future__ import annotations

from typing import TypeAlias

from grafix.core.geometry import Geometry
from grafix.core.layer import Layer

SceneItem: TypeAlias = Geometry | Layer | list["SceneItem"] | tuple["SceneItem", ...]


def normalize_scene(scene: SceneItem) -> list[Layer]:
    """Geometry/Layer/ネスト列を `list[Layer]` にフラット化する。

    Parameters
    ----------
    scene : SceneItem
        user_draw が返す Geometry / Layer / それらのネスト列。

    Returns
    -------
    list[Layer]
        描画順を保った Layer の一次元リスト。

    Notes
    -----
    - `Geometry` は暗黙に `Layer` へ包む。このとき `Layer.site_id` は
      ``"implicit:{index}"``（index は 1..N の連番）とする。
      目的: parameter_gui の Layer style 行（line_thickness/line_color）を、
      Geometry 内容（`Geometry.id`）の変化に影響されず安定化させる。

    Raises
    ------
    TypeError
        未対応の型が含まれる場合。
    """

    result: list[Layer] = []
    implicit_index = 0

    def _walk(item: SceneItem) -> None:
        nonlocal implicit_index
        if isinstance(item, Layer):
            result.append(item)
            return
        if isinstance(item, Geometry):
            implicit_index += 1
            result.append(Layer(geometry=item, site_id=f"implicit:{implicit_index}"))
            return
        if isinstance(item, (list, tuple)):
            for child in item:
                _walk(child)
            return
        raise TypeError(f"normalize_scene で処理できない型: {type(item)!r}")

    _walk(scene)
    return result
