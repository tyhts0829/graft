"""
どこで: `src/grafix/core/scene.py`。
何を: user_draw の戻り値を `list[Layer]` に正規化するヘルパを提供する。
なぜ: 描画・エクスポートの全経路で共通のシーン表現を使えるようにするため。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeAlias

from grafix.core.geometry import Geometry
from grafix.core.layer import Layer

SceneItem: TypeAlias = Geometry | Layer | Sequence["SceneItem"]


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
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
            for child in item:
                _walk(child)  # type: ignore[arg-type]
            return
        raise TypeError(f"normalize_scene で処理できない型: {type(item)!r}")

    _walk(scene)
    return result
