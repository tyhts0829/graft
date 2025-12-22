"""ユーザー定義 primitive/effect が meta 無しの場合、GUI（ParamStore.snapshot）に出ないことのテスト。"""

from __future__ import annotations

import numpy as np

from grafix.api import E, G
from grafix.core.effect_registry import effect
from grafix.core.primitive_registry import primitive
from grafix.core.realized_geometry import RealizedGeometry
from grafix.core.parameters import ParamStore, parameter_context
from grafix.core.parameters.snapshot_ops import store_snapshot


def _empty_geometry() -> RealizedGeometry:
    coords = np.zeros((0, 3), dtype=np.float32)
    offsets = np.zeros((1,), dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def user_defined_no_meta_primitive(*, x: float = 1.0) -> RealizedGeometry:
    _ = x
    return _empty_geometry()


@effect
def user_defined_no_meta_effect(
    inputs, *, y: float = 1.0
) -> RealizedGeometry:
    _ = y
    return inputs[0] if inputs else _empty_geometry()


def test_user_defined_ops_without_meta_do_not_appear_in_snapshot() -> None:
    store = ParamStore()

    with parameter_context(store=store, cc_snapshot=None):
        g = G.user_defined_no_meta_primitive(x=2.0)
        E.user_defined_no_meta_effect(y=3.0)(g)

    assert store_snapshot(store) == {}
