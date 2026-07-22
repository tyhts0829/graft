"""シーン正規化ヘルパのテスト。"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

import pytest

from grafix.core.geometry import Geometry
from grafix.core.layer import Layer
from grafix.core.scene import normalize_scene


def _g(name: str = "circle") -> Geometry:
    return Geometry.create(name, params={"r": 1.0})


def test_normalize_scene_wraps_geometry() -> None:
    g = _g()
    layers = normalize_scene(g)
    assert len(layers) == 1
    assert isinstance(layers[0], Layer)
    assert layers[0].geometry is g
    assert layers[0].site_id == "implicit:1"


def test_normalize_scene_flattens_nested_lists_and_tuples() -> None:
    g1, g2 = _g("circle"), _g("circle")
    layer1 = Layer(geometry=_g("circle"), site_id="layer:1", thickness=0.01)
    layers = normalize_scene([g1, (layer1, [g2])])
    assert [layer.geometry for layer in layers] == [g1, layer1.geometry, g2]
    assert [layer.site_id for layer in layers] == ["implicit:1", "layer:1", "implicit:2"]


class _CustomSequence(Sequence[Geometry]):
    def __init__(self, item: Geometry) -> None:
        self._item = item

    def __getitem__(self, index: int | slice) -> Geometry | Sequence[Geometry]:
        if isinstance(index, slice):
            return [self._item][index]
        return [self._item][index]

    def __len__(self) -> int:
        return 1


def _items(item: Geometry) -> Iterator[Geometry]:
    yield item


@pytest.mark.parametrize(
    "scene",
    [
        123,
        "scene",
        b"scene",
        _CustomSequence(_g()),
        {_g()},
        _items(_g()),
    ],
)
def test_normalize_scene_rejects_non_scene_containers(scene: object) -> None:
    with pytest.raises(TypeError):
        normalize_scene(scene)  # type: ignore[arg-type]
