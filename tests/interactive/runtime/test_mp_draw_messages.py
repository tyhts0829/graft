"""MpDraw queue message と immutable DTO の pure validation 契約。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any, cast

import pytest

from grafix.core.geometry import Geometry
from grafix.core.layer import Layer
from grafix.core.parameters import (
    EffectStepTopology,
    FrameEffectChainRecord,
    FrameLabelRecord,
    FrameParamRecord,
    MidiFrameSnapshot,
    ParameterKey,
    ParamMeta,
)
from grafix.interactive.runtime.mp_draw import (
    DrawResult,
    _SnapshotAck,
    _SnapshotUpdate,
    _TaskRejected,
    _TaskStarted,
    _WorkerReady,
)


class _StringSubclass(str):
    pass


class _TupleSubclass(tuple[object, ...]):
    pass


class _ParameterKeySubclass(ParameterKey):
    pass


class _ParamMetaSubclass(ParamMeta):
    pass


class _EffectStepTopologySubclass(EffectStepTopology):
    pass


def _valid_frame_param_record() -> FrameParamRecord:
    return FrameParamRecord(
        key=ParameterKey(op="line", site_id="site", arg="length"),
        base=1.0,
        meta=ParamMeta(kind="float"),
        effective=1.0,
        source="code",
        explicit=True,
    )


@pytest.mark.parametrize(
    ("field", "value", "error_type", "message"),
    [
        pytest.param(
            "key",
            object(),
            TypeError,
            "ParameterKey",
            id="key-object",
        ),
        pytest.param(
            "key",
            _ParameterKeySubclass(op="line", site_id="site", arg="length"),
            TypeError,
            "ParameterKey",
            id="key-subclass",
        ),
        pytest.param(
            "meta",
            object(),
            TypeError,
            "ParamMeta",
            id="meta-object",
        ),
        pytest.param(
            "meta",
            _ParamMetaSubclass(kind="float"),
            TypeError,
            "ParamMeta",
            id="meta-subclass",
        ),
        pytest.param(
            "base",
            "1.0",
            TypeError,
            "float parameter value",
            id="base-string",
        ),
        pytest.param(
            "effective",
            float("inf"),
            ValueError,
            "finite",
            id="effective-infinite",
        ),
        pytest.param(
            "source",
            _StringSubclass("code"),
            TypeError,
            "source",
            id="source-string-subclass",
        ),
        pytest.param(
            "source",
            "worker",
            ValueError,
            "source",
            id="source-unknown",
        ),
        pytest.param(
            "explicit",
            1,
            TypeError,
            "explicit",
            id="explicit-int",
        ),
    ],
)
def test_frame_param_record_rejects_noncanonical_payload(
    field: str,
    value: object,
    error_type: type[Exception],
    message: str,
) -> None:
    with pytest.raises(error_type, match=message):
        replace(_valid_frame_param_record(), **{field: value})  # type: ignore[arg-type]


def test_frame_param_record_normalizes_values_using_meta_contract() -> None:
    record = replace(_valid_frame_param_record(), base=1, effective=2)

    assert record.base == 1.0
    assert type(record.base) is float
    assert record.effective == 2.0
    assert type(record.effective) is float


def test_frame_label_record_rejects_label_string_subclass() -> None:
    with pytest.raises(TypeError, match="label"):
        FrameLabelRecord(
            op="line",
            site_id="site",
            label=_StringSubclass("Line"),
        )


@pytest.mark.parametrize(
    "steps",
    [
        pytest.param([], id="list"),
        pytest.param(
            _TupleSubclass(
                (
                    EffectStepTopology(
                        op="scale",
                        site_id="site",
                        n_inputs=1,
                        code_index=0,
                    ),
                )
            ),
            id="tuple-subclass",
        ),
        pytest.param(
            (
                _EffectStepTopologySubclass(
                    op="scale",
                    site_id="site",
                    n_inputs=1,
                    code_index=0,
                ),
            ),
            id="item-subclass",
        ),
    ],
)
def test_frame_effect_chain_record_requires_canonical_steps(
    steps: object,
) -> None:
    with pytest.raises(TypeError, match="steps"):
        FrameEffectChainRecord(
            chain_id="chain",
            steps=cast(Any, steps),
        )


@pytest.mark.parametrize(
    ("arguments", "error_type", "message"),
    [
        pytest.param(
            {"source": _StringSubclass("midi_live")},
            TypeError,
            "source",
            id="source-string-subclass",
        ),
        pytest.param(
            {"source": "recorded"},
            ValueError,
            "source",
            id="source-unknown",
        ),
        pytest.param(
            {"entries": [(1, 0.5)]},
            TypeError,
            "entries",
            id="entries-list",
        ),
        pytest.param(
            {"entries": _TupleSubclass(((1, 0.5),))},
            TypeError,
            "entries",
            id="entries-tuple-subclass",
        ),
        pytest.param(
            {"entries": (_TupleSubclass((1, 0.5)),)},
            TypeError,
            "MIDI entry",
            id="entry-tuple-subclass",
        ),
        pytest.param(
            {"entries": ((True, 0.5),)},
            TypeError,
            "MIDI CC番号",
            id="cc-bool",
        ),
        pytest.param(
            {"entries": ((1, "0.5"),)},
            TypeError,
            "MIDI CC値",
            id="value-string",
        ),
        pytest.param(
            {"entries": ((1, float("nan")),)},
            ValueError,
            "MIDI CC値",
            id="value-nan",
        ),
    ],
)
def test_midi_frame_snapshot_rejects_noncanonical_queue_payload(
    arguments: dict[str, object],
    error_type: type[Exception],
    message: str,
) -> None:
    values: dict[str, object] = {
        "source": "midi_live",
        "entries": (),
    }
    values.update(arguments)

    with pytest.raises(error_type, match=message):
        MidiFrameSnapshot(**cast(Any, values))


def _valid_draw_result() -> DrawResult:
    return DrawResult(
        frame_id=1,
        t=0.0,
        epoch=0,
        generation=0,
        snapshot_revision=0,
        layers=(),
        records=(),
        labels=(),
        effect_chains=(),
    )


@pytest.mark.parametrize(
    ("field", "value", "error_type"),
    [
        pytest.param("frame_id", True, TypeError, id="frame-id-bool"),
        pytest.param("t", "0.0", TypeError, id="t-string"),
        pytest.param("t", float("inf"), ValueError, id="t-infinite"),
        pytest.param("epoch", 0.5, TypeError, id="epoch-fractional"),
        pytest.param("generation", "0", TypeError, id="generation-string"),
        pytest.param(
            "snapshot_revision",
            True,
            TypeError,
            id="revision-bool",
        ),
        pytest.param("layers", [], TypeError, id="layers-list"),
        pytest.param("layers", (object(),), TypeError, id="layers-item"),
        pytest.param("records", [], TypeError, id="records-list"),
        pytest.param("records", (object(),), TypeError, id="records-item"),
        pytest.param("labels", [], TypeError, id="labels-list"),
        pytest.param("labels", (object(),), TypeError, id="labels-item"),
        pytest.param(
            "effect_chains",
            [],
            TypeError,
            id="effect-chains-list",
        ),
        pytest.param(
            "effect_chains",
            (object(),),
            TypeError,
            id="effect-chains-item",
        ),
        pytest.param(
            "error",
            _StringSubclass("failure"),
            TypeError,
            id="error-string-subclass",
        ),
        pytest.param("worker_pid", 1.5, TypeError, id="pid-fractional"),
        pytest.param("diagnostics", [], TypeError, id="diagnostics-list"),
        pytest.param(
            "diagnostics",
            (object(),),
            TypeError,
            id="diagnostics-item",
        ),
        pytest.param("worker_lag_ms", True, TypeError, id="lag-bool"),
        pytest.param(
            "worker_lag_ms",
            float("inf"),
            ValueError,
            id="lag-infinite",
        ),
        pytest.param(
            "worker_lag_ms",
            -0.1,
            ValueError,
            id="lag-negative",
        ),
    ],
)
def test_draw_result_rejects_noncanonical_queue_payload(
    field: str,
    value: object,
    error_type: type[Exception],
) -> None:
    with pytest.raises(error_type, match=field):
        replace(_valid_draw_result(), **{field: value})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param(
            "layers",
            (
                Layer(
                    geometry=Geometry.create(op="concat"),
                    site_id="layer",
                ),
            ),
            id="layers",
        ),
        pytest.param(
            "records",
            (_valid_frame_param_record(),),
            id="records",
        ),
        pytest.param(
            "labels",
            (
                FrameLabelRecord(
                    op="line",
                    site_id="site",
                    label="Line",
                ),
            ),
            id="labels",
        ),
        pytest.param(
            "effect_chains",
            (
                FrameEffectChainRecord(
                    chain_id="chain",
                    steps=(
                        EffectStepTopology(
                            op="scale",
                            site_id="site",
                            n_inputs=1,
                            code_index=0,
                        ),
                    ),
                ),
            ),
            id="effect-chains",
        ),
    ],
)
def test_draw_result_error_rejects_success_payload(
    field: str,
    value: object,
) -> None:
    error_result = replace(_valid_draw_result(), error="draw failed")

    with pytest.raises(ValueError, match="error result"):
        replace(error_result, **{field: value})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("factory", "field", "error_type"),
    [
        pytest.param(
            lambda: _SnapshotUpdate(
                revision=0.5,  # type: ignore[arg-type]
                snapshot={},
                effect_order_snapshot={},
                generation=0,
            ),
            "revision",
            TypeError,
            id="snapshot-update-revision",
        ),
        pytest.param(
            lambda: _SnapshotUpdate(
                revision=0,
                snapshot=[],  # type: ignore[arg-type]
                effect_order_snapshot={},
                generation=0,
            ),
            "snapshot",
            TypeError,
            id="snapshot-update-container",
        ),
        pytest.param(
            lambda: _SnapshotAck(
                worker="worker",
                pid=1,
                requested_revision=0,
                applied_revision=0,
                status=_StringSubclass("applied"),
                generation=0,
            ),
            "status",
            TypeError,
            id="snapshot-ack-string-subclass",
        ),
        pytest.param(
            lambda: _SnapshotAck(
                worker="worker",
                pid=1,
                requested_revision=0,
                applied_revision=0,
                status="ignored",
                generation=0,
            ),
            "status",
            ValueError,
            id="snapshot-ack-unknown-status",
        ),
        pytest.param(
            lambda: _TaskRejected(
                frame_id=1,
                worker="worker",
                pid=1,
                requested_revision=0,
                applied_revision="0",  # type: ignore[arg-type]
                reason="unknown",
                generation=0,
            ),
            "applied_revision",
            TypeError,
            id="task-rejected-revision",
        ),
        pytest.param(
            lambda: _TaskRejected(
                frame_id=1,
                worker="worker",
                pid=1,
                requested_revision=0,
                applied_revision=None,
                reason="future",
                generation=0,
            ),
            "reason",
            ValueError,
            id="task-rejected-unknown-reason",
        ),
        pytest.param(
            lambda: _TaskStarted(
                frame_id=1,
                worker="worker",
                pid=1.0,  # type: ignore[arg-type]
                generation=0,
            ),
            "pid",
            TypeError,
            id="task-started-pid",
        ),
        pytest.param(
            lambda: _WorkerReady(
                worker=1,  # type: ignore[arg-type]
                pid=1,
                generation=0,
            ),
            "worker",
            TypeError,
            id="worker-ready-name",
        ),
    ],
)
def test_worker_control_dtos_reject_noncanonical_payload(
    factory: Callable[[], object],
    field: str,
    error_type: type[Exception],
) -> None:
    with pytest.raises(error_type, match=field):
        factory()


def test_draw_result_uses_keyword_constructor() -> None:
    result = DrawResult(
        frame_id=2,
        t=0.0,
        epoch=0,
        generation=0,
        snapshot_revision=0,
        layers=(),
        records=(),
        labels=(),
        effect_chains=(),
        error="draw error",
    )

    assert result.error == "draw error"
    assert result.t == pytest.approx(0.0)
    assert result.epoch == 0
    assert result.snapshot_revision == 0
