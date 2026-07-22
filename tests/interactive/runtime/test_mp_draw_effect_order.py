from __future__ import annotations

import time

import pytest

from grafix.api.effects import EffectBuilder, _make_effect_operation_step
from grafix.core.builtins import builtin_operation_catalog
from grafix.core.geometry import Geometry
from grafix.core.parameters.effects import EffectStepKey
from grafix.runtime_config_loader import runtime_config
from grafix.interactive.runtime.mp_draw import DrawResult, MpDraw


_WAIT_TIMEOUT_S = 8.0
_CHAIN_ID = "mp-effect-order-chain"
_ROTATE_KEY: EffectStepKey = ("rotate", "mp-effect-order-rotate")
_TRANSLATE_KEY: EffectStepKey = ("translate", "mp-effect-order-translate")

_OPERATIONS = builtin_operation_catalog()
_BUILDER = EffectBuilder(
    steps=(
        _make_effect_operation_step(
            declaration=_OPERATIONS.resolve("effect", "rotate").declaration,
            params={"rotation": (0.0, 0.0, 20.0)},
            site_id=_ROTATE_KEY[1],
        ),
        _make_effect_operation_step(
            declaration=_OPERATIONS.resolve("effect", "translate").declaration,
            params={"delta": (2.0, 3.0, 4.0)},
            site_id=_TRANSLATE_KEY[1],
        ),
    ),
    chain_id=_CHAIN_ID,
)


def _effect_order_draw(_t: float) -> Geometry:
    return _BUILDER(Geometry.create(op="mp-effect-order-source"))


def _effect_order_then_fail(_t: float) -> Geometry:
    _BUILDER(Geometry.create(op="mp-effect-order-source"))
    raise RuntimeError("draw failed after topology observation")


def _wait_for_revision(mp_draw: MpDraw, revision: int) -> DrawResult:
    deadline = time.monotonic() + _WAIT_TIMEOUT_S
    while time.monotonic() < deadline:
        result = mp_draw.poll_latest()
        if result is not None and result.snapshot_revision == revision:
            return result
        time.sleep(0.01)
    pytest.fail(f"mp-draw result timeout: revision={revision}")


@pytest.mark.parametrize("n_worker", [1, 2])
def test_effect_order_snapshot_reaches_worker_and_controls_dag(
    n_worker: int,
) -> None:
    reverse_order = {
        _CHAIN_ID: (
            _TRANSLATE_KEY,
            _ROTATE_KEY,
        )
    }
    mp_draw = MpDraw(
        _effect_order_draw,
        n_worker=n_worker,
        effective_config=runtime_config(),
    )
    try:
        mp_draw.submit(
            t=0.0,
            snapshot_revision=7,
            snapshot={},
            effect_order_snapshot=reverse_order,
            epoch=0,
            quality="draft",
        )
        reordered = _wait_for_revision(mp_draw, 7)

        assert reordered.error is None
        geometry = reordered.layers[0].geometry
        assert geometry.op == "rotate"
        assert geometry.inputs[0].op == "translate"
        assert geometry.inputs[0].inputs[0].op == "mp-effect-order-source"
        assert len(reordered.effect_chains) == 1
        assert reordered.effect_chains[0].chain_id == _CHAIN_ID

        mp_draw.submit(
            t=1.0,
            snapshot_revision=8,
            snapshot={},
            effect_order_snapshot={},
            epoch=0,
            quality="draft",
        )
        code_order = _wait_for_revision(mp_draw, 8)

        assert code_order.error is None
        geometry = code_order.layers[0].geometry
        assert geometry.op == "translate"
        assert geometry.inputs[0].op == "rotate"
        assert geometry.inputs[0].inputs[0].op == "mp-effect-order-source"
    finally:
        mp_draw.close()


def test_failed_worker_result_discards_partial_effect_topology() -> None:
    mp_draw = MpDraw(
        _effect_order_then_fail,
        n_worker=1,
        effective_config=runtime_config(),
    )
    try:
        mp_draw.submit(
            t=0.0,
            snapshot_revision=1,
            snapshot={},
            effect_order_snapshot={
                _CHAIN_ID: (
                    _TRANSLATE_KEY,
                    _ROTATE_KEY,
                )
            },
            epoch=0,
            quality="draft",
        )
        result = _wait_for_revision(mp_draw, 1)
    finally:
        mp_draw.close()

    assert result.error is not None
    assert "draw failed after topology observation" in result.error
    assert result.effect_chains == ()
