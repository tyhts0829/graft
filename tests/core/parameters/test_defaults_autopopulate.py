import numpy as np
import pytest

from grafix.api import E, G
from grafix.core.effect_registry import effect
from grafix.core.primitive_registry import primitive
from grafix.core.realized_geometry import RealizedGeometry
from grafix.core.parameters import ParamMeta, ParamStore, parameter_context


def _empty_geometry() -> RealizedGeometry:
    coords = np.zeros((0, 3), dtype=np.float32)
    offsets = np.zeros((1,), dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def test_polygon_defaults_recorded_when_no_kwargs():
    store = ParamStore()

    with parameter_context(store=store, cc_snapshot=None):
        G.polygon()

    snapshot = store.snapshot()
    polygon_args = {key.arg for key in snapshot.keys() if key.op == "polygon"}
    assert polygon_args == {"n_sides", "phase", "center", "scale"}


def test_effect_defaults_recorded_when_no_kwargs():
    store = ParamStore()

    with parameter_context(store=store, cc_snapshot=None):
        g = G.polygon()
        E.scale()(g)

    snapshot = store.snapshot()
    scale_args = {key.arg for key in snapshot.keys() if key.op == "scale"}
    assert scale_args == {"auto_center", "pivot", "scale"}


def test_meta_default_none_is_rejected():
    meta = {"x": ParamMeta(kind="float", ui_min=0.0, ui_max=1.0)}

    with pytest.raises(ValueError, match="None"):

        @primitive(meta=meta)
        def _tmp_none_default_primitive(*, x: float | None = None) -> RealizedGeometry:
            return _empty_geometry()

    with pytest.raises(ValueError, match="None"):

        @effect(meta=meta)
        def _tmp_none_default_effect(inputs, *, x: float | None = None) -> RealizedGeometry:
            _ = inputs
            return _empty_geometry()
