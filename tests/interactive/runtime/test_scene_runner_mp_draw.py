"""SceneRunner と MpDraw factory seam 間の dispatch・adoption 契約。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

import pytest

import grafix.interactive.runtime.scene_runner as scene_runner_module
from grafix.core.geometry import Geometry
from grafix.core.layer import LayerStyleDefaults
from grafix.core.parameters import (
    EffectStepTopology,
    FrameEffectChainRecord,
    FrameParamRecord,
    ParameterKey,
    ParamMeta,
    ParamStore,
    begin_effect_chain_generation,
)
from grafix.core.parameters.effect_order_ops import merge_frame_effect_chains
from grafix.core.parameters.layer_style import (
    LAYER_STYLE_LINE_COLOR,
    LAYER_STYLE_LINE_THICKNESS,
    layer_style_key,
)
from grafix.core.parameters.ui_ops import update_state_from_ui
from grafix.core.preview_quality import PreviewQuality, current_preview_quality
from grafix.core.resource_budget import ResourceBudget
from grafix.core.runtime_config import current_runtime_config
from grafix.core.runtime_limits import RuntimeLimitProfiles, RuntimeLimits
from grafix.core.scene import normalize_scene
from grafix.interactive.runtime.mp_draw import (
    DrawResult,
    MpDrawWorkerError,
)
from grafix.interactive.runtime.perf import PerfCollector
from grafix.interactive.runtime.scene_runner import SceneRunner
from grafix.runtime_config_loader import runtime_config
from tests.interactive.runtime.scene_runner_fixture import MpDrawFactoryFixture

_EFFECTIVE_CONFIG = runtime_config()


def _scene_runner(draw: Any, **kwargs: Any) -> SceneRunner:
    return SceneRunner(draw, effective_config=_EFFECTIVE_CONFIG, **kwargs)


def _empty_draw(_t: float) -> Geometry:
    return Geometry.create(op="concat")


def _failing_empty_draw(_t: float) -> Geometry:
    raise RuntimeError("new source evaluation failed")


def _seed_effect_chain(store: ParamStore, chain_id: str) -> None:
    assert merge_frame_effect_chains(
        store,
        [
            FrameEffectChainRecord(
                chain_id=chain_id,
                steps=(
                    EffectStepTopology(
                        op="scale",
                        site_id=f"{chain_id}-site",
                        n_inputs=1,
                        code_index=0,
                    ),
                ),
            )
        ],
        observation_complete=False,
    )


class _DeadMpDraw:
    def __init__(self, error: MpDrawWorkerError) -> None:
        self.error = error
        self.close_calls = 0
        self.last_submitted_frame_id = 0

    def submit(self, **_kwargs: object) -> None:
        raise self.error

    def poll_latest(self) -> None:
        return None

    def latest_successful_result(self) -> None:
        return None

    def begin_epoch(self, epoch: int | None = None) -> int:
        return 0 if epoch is None else int(epoch)

    def close(self) -> None:
        self.close_calls += 1


class _BatchedSuccessThenErrorMpDraw:
    """success frame とより新しい error frame の同時 drain を模す。"""

    def __init__(self) -> None:
        self.success = DrawResult(
            frame_id=10,
            t=0.25,
            epoch=0,
            generation=0,
            snapshot_revision=0,
            layers=tuple(normalize_scene(_empty_draw(0.25))),
            records=(),
            labels=(),
            effect_chains=(),
        )
        self.error = DrawResult(
            frame_id=11,
            t=0.5,
            epoch=0,
            generation=0,
            snapshot_revision=0,
            layers=(),
            records=(),
            labels=(),
            effect_chains=(),
            error="later frame failed",
        )
        self.poll_calls = 0
        self.close_calls = 0
        self.last_submitted_frame_id = 0

    def submit(self, **_kwargs: object) -> None:
        self.last_submitted_frame_id += 1
        return

    def poll_latest(self) -> DrawResult | None:
        self.poll_calls += 1
        if self.poll_calls == 1:
            # MpDraw.poll_latest() は batch の最新 result（ここでは error）を
            # 通知する一方、latest_successful_result() は frame 10 を保持する。
            return self.error
        return None

    def latest_successful_result(self) -> DrawResult:
        return self.success

    def begin_epoch(self, epoch: int | None = None) -> int:
        return 0 if epoch is None else int(epoch)

    def close(self) -> None:
        self.close_calls += 1


class _EpochMpDraw:
    """SceneRunner の epoch 遷移と fresh 待ちを決定的に模す。"""

    def __init__(self, result: DrawResult | None) -> None:
        self.result = result
        self.current_epoch = 0
        self.begin_calls: list[int] = []
        self.submitted_epochs: list[int] = []
        self._published = False
        self.close_calls = 0
        self.last_submitted_frame_id = 0

    def begin_epoch(self, epoch: int | None = None) -> int:
        self.current_epoch = self.current_epoch + 1 if epoch is None else int(epoch)
        self.begin_calls.append(self.current_epoch)
        # cached old result を invalidation する MpDraw の契約を模す。
        self.result = None
        self._published = False
        return self.current_epoch

    def submit(self, **kwargs: object) -> None:
        self.last_submitted_frame_id += 1
        self.submitted_epochs.append(int(cast(int, kwargs["epoch"])))

    def poll_latest(self) -> DrawResult | None:
        if self.result is None or self._published:
            return None
        self._published = True
        return self.result

    def latest_successful_result(self) -> DrawResult | None:
        if self.result is None or self.result.error is not None:
            return None
        return self.result

    def close(self) -> None:
        self.close_calls += 1


class _IdleMpDraw:
    """SceneRunner の dispatch 境界だけを観測する non-blocking fake。"""

    def __init__(self) -> None:
        self.submit_calls: list[dict[str, object]] = []
        self.close_calls = 0
        self.last_submitted_frame_id = 0
        self.begin_calls: list[int] = []

    def submit(self, **kwargs: object) -> None:
        self.last_submitted_frame_id += 1
        self.submit_calls.append(dict(kwargs))

    def poll_latest(self) -> None:
        return None

    def latest_successful_result(self) -> None:
        return None

    def begin_epoch(self, epoch: int | None = None) -> int:
        value = 0 if epoch is None else int(epoch)
        self.begin_calls.append(value)
        return value

    def close(self) -> None:
        self.close_calls += 1


def test_scene_runner_sync_failure_keeps_generation_until_success() -> None:
    runner = _scene_runner(
        _failing_empty_draw,
        perf=PerfCollector(enabled=False),
        n_worker=0,
    )
    store = ParamStore()
    _seed_effect_chain(store, "old-generation-chain")
    begin_effect_chain_generation(store)
    defaults = LayerStyleDefaults(color=(0.0, 0.0, 0.0), thickness=0.01)
    try:
        with pytest.raises(RuntimeError, match="new source evaluation failed"):
            runner.run(
                0.0,
                store=store,
                cc_snapshot=None,
                defaults=defaults,
                recording=False,
                transport_epoch=0,
                quality="draft",
            )
        assert "old-generation-chain" in store.effect_chain_topologies()

        runner.replace_draw(_empty_draw)
        runner.run(
            0.1,
            store=store,
            cc_snapshot=None,
            defaults=defaults,
            recording=False,
            transport_epoch=0,
            quality="draft",
        )
        assert "old-generation-chain" not in store.effect_chain_topologies()
    finally:
        runner.close()


def test_scene_runner_mp_wait_does_not_finish_effect_chain_generation() -> None:
    idle_mp_draw = _IdleMpDraw()
    runner = _scene_runner(
        _empty_draw,
        perf=PerfCollector(enabled=False),
        n_worker=1,
        mp_draw_factory=MpDrawFactoryFixture(idle_mp_draw),
    )
    store = ParamStore()
    _seed_effect_chain(store, "old-generation-chain")
    begin_effect_chain_generation(store)
    try:
        runner.run(
            0.0,
            store=store,
            cc_snapshot=None,
            defaults=LayerStyleDefaults(
                color=(0.0, 0.0, 0.0),
                thickness=0.01,
            ),
            recording=False,
            transport_epoch=0,
            quality="draft",
        )
        assert "old-generation-chain" in store.effect_chain_topologies()
    finally:
        runner.close()


def test_scene_runner_mp_fresh_empty_topology_finishes_effect_chain_generation() -> None:
    epoch_mp = _EpochMpDraw(None)
    runner = _scene_runner(
        _empty_draw,
        perf=PerfCollector(enabled=False),
        n_worker=1,
        mp_draw_factory=MpDrawFactoryFixture(epoch_mp),
    )
    store = ParamStore()
    _seed_effect_chain(store, "old-generation-chain")
    begin_effect_chain_generation(store)
    defaults = LayerStyleDefaults(color=(0.0, 0.0, 0.0), thickness=0.01)
    try:
        runner.run(
            0.0,
            store=store,
            cc_snapshot=None,
            defaults=defaults,
            recording=False,
            transport_epoch=0,
            quality="draft",
        )
        assert "old-generation-chain" in store.effect_chain_topologies()

        epoch_mp.result = DrawResult(
            frame_id=1,
            layers=tuple(normalize_scene(_empty_draw(0.1))),
            records=(),
            labels=(),
            effect_chains=(),
            t=0.1,
            epoch=0,
            generation=0,
            snapshot_revision=store.revision,
        )
        epoch_mp._published = False
        runner.run(
            0.1,
            store=store,
            cc_snapshot=None,
            defaults=defaults,
            recording=False,
            transport_epoch=0,
            quality="draft",
        )
        assert "old-generation-chain" not in store.effect_chain_topologies()
    finally:
        runner.close()


@pytest.mark.parametrize("n_worker", [1, 2])
def test_scene_runner_uses_background_evaluation_for_positive_worker_count(
    n_worker: int,
) -> None:
    """preview の run は positive worker count で user draw を main 実行しない。"""

    draw_calls: list[float] = []

    def draw(t: float) -> Geometry:
        draw_calls.append(float(t))
        return Geometry.create(op="concat")

    idle_mp_draw = _IdleMpDraw()
    factory = MpDrawFactoryFixture(idle_mp_draw)
    runner = _scene_runner(
        draw,
        perf=PerfCollector(enabled=False),
        n_worker=n_worker,
        mp_draw_factory=factory,
    )
    try:
        assert (
            runner.run(
                1.25,
                store=ParamStore(),
                cc_snapshot=None,
                defaults=LayerStyleDefaults(color=(0.0, 0.0, 0.0), thickness=0.01),
                recording=False,
                transport_epoch=0,
                quality="draft",
            )
            == []
        )

        assert factory.calls[0].n_worker == n_worker
        assert factory.calls[0].evaluation_timeout == pytest.approx(5.0)
        assert factory.calls[0].event_callback is None
        assert len(idle_mp_draw.submit_calls) == 1
        assert idle_mp_draw.submit_calls[0]["t"] == pytest.approx(1.25)
        assert draw_calls == []
        assert runner.last_evaluation_succeeded is None
    finally:
        runner.close()

    assert idle_mp_draw.close_calls == 1


def test_scene_runner_passes_evaluation_timeout_to_mp_draw() -> None:
    factory = MpDrawFactoryFixture(_IdleMpDraw())
    runner = _scene_runner(
        _empty_draw,
        perf=PerfCollector(enabled=False),
        n_worker=1,
        evaluation_timeout=0.25,
        mp_draw_factory=factory,
    )
    try:
        assert factory.calls[0].evaluation_timeout == pytest.approx(0.25)
    finally:
        runner.close()


def test_scene_runner_replace_draw_retires_old_worker_and_keeps_configuration() -> None:
    first_draw = _empty_draw

    def second_draw(_t: float) -> Geometry:
        return Geometry.create(op="concat")

    first_worker = _IdleMpDraw()
    second_worker = _IdleMpDraw()
    factory = MpDrawFactoryFixture(first_worker, second_worker)
    runner = _scene_runner(
        first_draw,
        perf=PerfCollector(enabled=False),
        n_worker=2,
        evaluation_timeout=0.75,
        mp_draw_factory=factory,
    )
    runner.replace_draw(second_draw)

    assert first_worker.close_calls == 1
    assert [call.draw for call in factory.calls] == [first_draw, second_draw]
    assert [call.n_worker for call in factory.calls] == [2, 2]
    assert [call.evaluation_timeout for call in factory.calls] == [0.75, 0.75]
    assert factory.calls[0].effective_config is factory.calls[1].effective_config

    runner.close()
    assert second_worker.close_calls == 1


def test_scene_runner_replace_draw_failure_keeps_current_worker() -> None:
    current_worker = _IdleMpDraw()
    factory = MpDrawFactoryFixture(current_worker, RuntimeError("spawn failed"))
    runner = _scene_runner(
        _empty_draw,
        perf=PerfCollector(enabled=False),
        n_worker=1,
        mp_draw_factory=factory,
    )

    with pytest.raises(RuntimeError, match="spawn failed"):
        runner.replace_draw(lambda _t: Geometry.create(op="concat"))

    assert len(factory.calls) == 2
    assert current_worker.close_calls == 0
    runner.close()
    assert current_worker.close_calls == 1


def test_scene_runner_zero_runs_synchronously_without_constructing_worker() -> None:
    draw_calls: list[float] = []

    def draw(t: float) -> Geometry:
        draw_calls.append(float(t))
        return Geometry.create(op="concat")

    factory = MpDrawFactoryFixture()
    runner = _scene_runner(
        draw,
        perf=PerfCollector(enabled=False),
        n_worker=0,
        mp_draw_factory=factory,
    )
    try:
        runner.run(
            2.5,
            store=ParamStore(),
            cc_snapshot=None,
            defaults=LayerStyleDefaults(color=(0.0, 0.0, 0.0), thickness=0.01),
            recording=False,
            transport_epoch=0,
            quality="draft",
        )
        assert draw_calls == [2.5]
        assert runner.last_evaluation_succeeded is True
        assert runner.last_evaluation_t == pytest.approx(2.5)
        assert runner.last_realized_snapshot_revision == 0
        assert factory.calls == []
    finally:
        runner.close()


def test_scene_runner_uses_explicit_quality_for_preview_and_recording() -> None:
    qualities: list[str] = []

    def draw(_t: float) -> Geometry:
        qualities.append(current_preview_quality())
        return Geometry.create(op="concat")

    runner = _scene_runner(draw, perf=PerfCollector(enabled=False), n_worker=0)
    try:
        cases: tuple[tuple[int, bool, PreviewQuality], ...] = (
            (0, False, "draft"),
            (1, True, "final"),
        )
        for transport_epoch, recording, quality in cases:
            runner.run(
                0.0,
                store=ParamStore(),
                cc_snapshot=None,
                defaults=LayerStyleDefaults(
                    color=(0.0, 0.0, 0.0),
                    thickness=0.01,
                ),
                recording=recording,
                transport_epoch=transport_epoch,
                quality=quality,
            )
    finally:
        runner.close()

    assert qualities == ["draft", "final"]


def test_scene_runner_binds_explicit_runtime_config_during_sync_draw() -> None:
    effective_config = replace(
        _EFFECTIVE_CONFIG,
        output_dir=_EFFECTIVE_CONFIG.output_dir / "sync-runner-config",
    )
    observed: list[object] = []

    def draw(_t: float) -> Geometry:
        observed.append(current_runtime_config())
        return Geometry.create(op="concat")

    runner = SceneRunner(
        draw,
        perf=PerfCollector(enabled=False),
        n_worker=0,
        effective_config=effective_config,
    )
    try:
        runner.run(
            0.0,
            store=ParamStore(),
            cc_snapshot=None,
            defaults=LayerStyleDefaults(
                color=(0.0, 0.0, 0.0),
                thickness=0.01,
            ),
            recording=False,
            transport_epoch=0,
            quality="draft",
        )
    finally:
        runner.close()

    assert observed == [effective_config]


def test_scene_runner_rejects_non_final_quality_while_recording() -> None:
    runner = _scene_runner(
        _empty_draw,
        perf=PerfCollector(enabled=False),
        n_worker=0,
    )
    try:
        with pytest.raises(ValueError, match="quality='final'"):
            runner.run(
                0.0,
                store=ParamStore(),
                cc_snapshot=None,
                defaults=LayerStyleDefaults(
                    color=(0.0, 0.0, 0.0),
                    thickness=0.01,
                ),
                recording=True,
                transport_epoch=0,
                quality="draft",
            )
    finally:
        runner.close()


def test_scene_runner_rejects_negative_worker_count() -> None:
    with pytest.raises(ValueError, match="0 以上"):
        _scene_runner(_empty_draw, perf=PerfCollector(enabled=False), n_worker=-1)


@pytest.mark.parametrize("n_worker", [True, 1.0, "1"])
def test_scene_runner_rejects_implicitly_convertible_worker_count(
    n_worker: object,
) -> None:
    with pytest.raises(TypeError, match="n_worker.*int"):
        _scene_runner(
            _empty_draw,
            perf=PerfCollector(enabled=False),
            n_worker=n_worker,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "timeout",
    [True, "1", 0.0, -1.0, float("inf"), float("nan")],
)
def test_scene_runner_validates_timeout_in_synchronous_mode(
    timeout: object,
) -> None:
    expected_error = TypeError if isinstance(timeout, (bool, str)) else ValueError
    with pytest.raises(expected_error, match="evaluation_timeout"):
        _scene_runner(
            _empty_draw,
            perf=PerfCollector(enabled=False),
            n_worker=0,
            evaluation_timeout=timeout,  # type: ignore[arg-type]
        )


def test_recording_remains_synchronous_with_background_preview_configured() -> None:
    draw_calls: list[float] = []

    def draw(t: float) -> Geometry:
        draw_calls.append(float(t))
        return Geometry.create(op="concat")

    idle_mp_draw = _IdleMpDraw()
    runner = _scene_runner(
        draw,
        perf=PerfCollector(enabled=False),
        n_worker=1,
        mp_draw_factory=MpDrawFactoryFixture(idle_mp_draw),
    )
    try:
        runner.run(
            3.75,
            store=ParamStore(),
            cc_snapshot=None,
            defaults=LayerStyleDefaults(color=(0.0, 0.0, 0.0), thickness=0.01),
            recording=True,
            transport_epoch=0,
            quality="final",
        )

        assert draw_calls == [3.75]
        assert idle_mp_draw.submit_calls == []
        assert runner.last_evaluation_succeeded is True
        assert runner.last_evaluation_t == pytest.approx(3.75)
    finally:
        runner.close()


def test_scene_runner_propagates_worker_death_without_sync_fallback() -> None:
    draw_calls = 0

    def draw(_t: float) -> Geometry:
        nonlocal draw_calls
        draw_calls += 1
        return Geometry.create(op="concat")

    error = MpDrawWorkerError(worker="dead", pid=123, exitcode=7)
    dead_mp_draw = _DeadMpDraw(error)
    runner = _scene_runner(
        draw,
        perf=PerfCollector(enabled=False),
        n_worker=1,
        mp_draw_factory=MpDrawFactoryFixture(dead_mp_draw),
    )

    with pytest.raises(MpDrawWorkerError) as exc_info:
        runner.run(
            0.0,
            store=ParamStore(),
            cc_snapshot=None,
            defaults=LayerStyleDefaults(color=(0.0, 0.0, 0.0), thickness=0.01),
            recording=False,
            transport_epoch=0,
            quality="draft",
        )

    assert exc_info.value is error
    assert draw_calls == 0

    runner.close()
    runner.close()
    assert dead_mp_draw.close_calls == 1


def test_scene_runner_couples_output_time_to_batched_success_before_later_error() -> None:
    """後続 error の `t` ではなく、実際に realize した success の `t` を返す。"""

    batched = _BatchedSuccessThenErrorMpDraw()
    runner = _scene_runner(
        _empty_draw,
        perf=PerfCollector(enabled=False),
        n_worker=1,
        mp_draw_factory=MpDrawFactoryFixture(batched),
    )
    store = ParamStore()
    defaults = LayerStyleDefaults(color=(0.0, 0.0, 0.0), thickness=0.01)
    try:
        with pytest.raises(RuntimeError, match="later frame failed"):
            runner.run(
                1.0,
                store=store,
                cc_snapshot=None,
                defaults=defaults,
                recording=False,
                transport_epoch=0,
                quality="draft",
            )

        # error frame 自身は realized output ではないため、その t=0.5 を
        # capture/manifest 用状態へ進めない。
        assert runner.last_evaluation_succeeded is False
        assert runner.last_evaluation_t is None
        assert runner.last_realized_t is None

        realized = runner.run(
            1.1,
            store=store,
            cc_snapshot=None,
            defaults=defaults,
            recording=False,
            transport_epoch=0,
            quality="draft",
        )

        assert realized
        # error より後に到着した回復ではないため、error 解除用
        # status は None のまま。一方、出力時刻は実際に realize した
        # frame 10 の t=0.25 を返す。
        assert runner.last_evaluation_succeeded is None
        assert runner.last_output_updated is True
        assert runner.last_evaluation_t is None
        assert runner.last_realized_t == pytest.approx(0.25)
    finally:
        runner.close()

    assert batched.close_calls == 1


def test_scene_runner_does_not_rerealize_same_mp_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = DrawResult(
        frame_id=1,
        layers=tuple(normalize_scene(_empty_draw(1.0))),
        records=(),
        labels=(),
        t=1.0,
        epoch=0,
        generation=0,
        snapshot_revision=0,
        effect_chains=(),
    )
    mp_draw = _EpochMpDraw(result)
    original_realize_scene = scene_runner_module.realize_scene
    realize_calls = 0

    def counted_realize_scene(*args: object, **kwargs: object) -> Any:
        nonlocal realize_calls
        realize_calls += 1
        return cast(Any, original_realize_scene)(*args, **kwargs)

    monkeypatch.setattr(
        scene_runner_module,
        "realize_scene",
        counted_realize_scene,
    )
    runner = _scene_runner(
        _empty_draw,
        perf=PerfCollector(enabled=False),
        n_worker=1,
        mp_draw_factory=MpDrawFactoryFixture(mp_draw),
    )
    store = ParamStore()
    defaults = LayerStyleDefaults(color=(0.0, 0.0, 0.0), thickness=0.01)
    try:
        first = runner.run(
            1.0,
            store=store,
            cc_snapshot=None,
            defaults=defaults,
            recording=False,
            transport_epoch=0,
            quality="draft",
        )
        assert runner.last_output_updated is True
        color_key = layer_style_key("implicit:1", LAYER_STYLE_LINE_COLOR)
        thickness_key = layer_style_key(
            "implicit:1",
            LAYER_STYLE_LINE_THICKNESS,
        )
        color_meta = store.get_meta(color_key)
        thickness_meta = store.get_meta(thickness_key)
        assert color_meta is not None
        assert thickness_meta is not None
        assert update_state_from_ui(
            store,
            color_key,
            (255, 0, 0),
            meta=color_meta,
            override=True,
        )[0]
        assert update_state_from_ui(
            store,
            thickness_key,
            0.02,
            meta=thickness_meta,
            override=True,
        )[0]
        second = runner.run(
            2.0,
            store=store,
            cc_snapshot=None,
            defaults=defaults,
            recording=False,
            transport_epoch=0,
            quality="draft",
        )
        third = runner.run(
            3.0,
            store=store,
            cc_snapshot=None,
            defaults=defaults,
            recording=False,
            transport_epoch=0,
            quality="draft",
        )
        assert second[0].realized is first[0].realized
        assert third[0] is second[0]
        assert second[0].cache_key == first[0].cache_key
        assert first[0].color == (0.0, 0.0, 0.0)
        assert second[0].color == (1.0, 0.0, 0.0)
        assert second[0].thickness == pytest.approx(0.02)
        assert realize_calls == 1
        assert runner.last_realized_t == pytest.approx(1.0)
        assert runner.last_output_updated is False
        assert runner.is_waiting_for_fresh_result is True
    finally:
        runner.close()


def test_scene_runner_retains_recording_frame_until_fresh_preview_result() -> None:
    """録画終了時に録画前の mp cache へ巻き戻らない。"""

    old_preview = DrawResult(
        frame_id=1,
        layers=tuple(normalize_scene(_empty_draw(1.0))),
        records=(),
        labels=(),
        t=1.0,
        epoch=0,
        generation=0,
        snapshot_revision=3,
        effect_chains=(),
    )
    epoch_mp = _EpochMpDraw(old_preview)
    runner = _scene_runner(
        _empty_draw,
        perf=PerfCollector(enabled=False),
        n_worker=1,
        mp_draw_factory=MpDrawFactoryFixture(epoch_mp),
    )
    store = ParamStore()
    defaults = LayerStyleDefaults(color=(0.0, 0.0, 0.0), thickness=0.01)
    try:
        runner.run(
            1.0,
            store=store,
            cc_snapshot=None,
            defaults=defaults,
            recording=False,
            transport_epoch=0,
            quality="draft",
        )
        assert runner.last_realized_t == pytest.approx(1.0)
        assert runner.last_realized_snapshot_revision == 3

        # 録画開始でも epoch を進め、以後は同期評価した t=5 の frame を
        # 実表示として保持する。
        recording_layers = runner.run(
            5.0,
            store=store,
            cc_snapshot=None,
            defaults=defaults,
            recording=True,
            transport_epoch=1,
            quality="final",
        )
        assert runner.last_realized_t == pytest.approx(5.0)
        assert runner.last_realized_snapshot_revision == store.revision

        # 録画終了は epoch を進めて old_preview を無効化する。fresh result が
        # 未到着でも同期録画 frame と t の組を維持する。
        waiting_layers = runner.run(
            5.1,
            store=store,
            cc_snapshot=None,
            defaults=defaults,
            recording=False,
            transport_epoch=2,
            quality="draft",
        )
        assert waiting_layers == recording_layers
        assert runner.last_realized_t == pytest.approx(5.0)
        assert runner.last_realized_snapshot_revision == store.revision
        assert runner.last_evaluation_succeeded is None
        assert runner.is_waiting_for_fresh_result is True
        assert epoch_mp.begin_calls == [1, 2]
        assert epoch_mp.submitted_epochs[-1] == 2
    finally:
        runner.close()


def test_scene_runner_seek_epoch_adopts_only_fresh_result_and_time() -> None:
    initial = DrawResult(
        frame_id=10,
        layers=tuple(normalize_scene(_empty_draw(2.0))),
        records=(),
        labels=(),
        t=2.0,
        epoch=0,
        generation=0,
        snapshot_revision=4,
        effect_chains=(),
    )
    epoch_mp = _EpochMpDraw(initial)
    runner = _scene_runner(
        _empty_draw,
        perf=PerfCollector(enabled=False),
        n_worker=1,
        mp_draw_factory=MpDrawFactoryFixture(epoch_mp),
    )
    store = ParamStore()
    defaults = LayerStyleDefaults(color=(0.0, 0.0, 0.0), thickness=0.01)
    try:
        before_seek = runner.run(
            2.0,
            store=store,
            cc_snapshot=None,
            defaults=defaults,
            recording=False,
            transport_epoch=0,
            quality="draft",
        )
        assert runner.last_realized_t == pytest.approx(2.0)
        assert runner.last_realized_snapshot_revision == 4

        waiting = runner.run(
            20.0,
            store=store,
            cc_snapshot=None,
            defaults=defaults,
            recording=False,
            transport_epoch=1,
            quality="draft",
        )
        assert waiting == before_seek
        assert runner.last_realized_t == pytest.approx(2.0)
        assert runner.last_realized_snapshot_revision == 4
        assert runner.is_waiting_for_fresh_result is True

        epoch_mp.result = DrawResult(
            frame_id=11,
            layers=tuple(normalize_scene(_empty_draw(20.0))),
            records=(),
            labels=(),
            t=20.0,
            epoch=1,
            generation=0,
            snapshot_revision=9,
            effect_chains=(),
        )
        epoch_mp._published = False
        fresh = runner.run(
            20.0,
            store=store,
            cc_snapshot=None,
            defaults=defaults,
            recording=False,
            transport_epoch=1,
            quality="draft",
        )
        assert fresh
        assert runner.last_realized_t == pytest.approx(20.0)
        assert runner.last_realized_snapshot_revision == 9
        assert runner.last_evaluation_t == pytest.approx(20.0)
        assert runner.is_waiting_for_fresh_result is False
    finally:
        runner.close()


def test_scene_runner_retries_success_observations_after_realize_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """realize 失敗で rollback された worker 観測を次回に再マージする。"""

    key = ParameterKey(op="line", site_id="worker-site", arg="length")
    record = FrameParamRecord(
        key=key,
        base=2.0,
        meta=ParamMeta(kind="float", ui_min=0.0, ui_max=10.0),
        effective=2.0,
        source="code",
        explicit=True,
    )
    batched = _BatchedSuccessThenErrorMpDraw()
    batched.success = DrawResult(
        frame_id=10,
        t=0.25,
        epoch=0,
        generation=0,
        snapshot_revision=0,
        layers=batched.success.layers,
        records=(record,),
        labels=(),
        effect_chains=(
            FrameEffectChainRecord(
                chain_id="worker-chain",
                steps=(
                    EffectStepTopology(
                        op="line",
                        site_id="worker-site",
                        n_inputs=1,
                        code_index=0,
                    ),
                ),
            ),
        ),
    )

    realize_calls = 0

    def fail_once(*_args: object, **_kwargs: object) -> list[Any]:
        nonlocal realize_calls
        realize_calls += 1
        if realize_calls == 1:
            raise ValueError("main realize failed")
        return []

    monkeypatch.setattr(scene_runner_module, "realize_scene", fail_once)
    runner = _scene_runner(
        _empty_draw,
        perf=PerfCollector(enabled=False),
        n_worker=1,
        mp_draw_factory=MpDrawFactoryFixture(batched),
    )
    store = ParamStore()
    defaults = LayerStyleDefaults(color=(0.0, 0.0, 0.0), thickness=0.01)
    try:
        # batch 終端の error を先に通知し、retained success を次回へ残す。
        with pytest.raises(RuntimeError, match="later frame failed"):
            runner.run(
                1.0,
                store=store,
                cc_snapshot=None,
                defaults=defaults,
                recording=False,
                transport_epoch=0,
                quality="draft",
            )

        # retained success の records は frame buffer へ入るが、main realize 失敗に
        # より parameter_context が frame 全体を rollback する。
        with pytest.raises(ValueError, match="main realize failed"):
            runner.run(
                1.1,
                store=store,
                cc_snapshot=None,
                defaults=defaults,
                recording=False,
                transport_epoch=0,
                quality="draft",
            )
        assert store.get_state(key) is None
        assert "worker-chain" not in store.effect_chain_topologies()
        assert runner._last_merged_mp_success_frame_id is None
        assert runner.last_realized_t is None

        # 同じ success frame を retry し、今回は realize と context commit が成功する。
        assert (
            runner.run(
                1.2,
                store=store,
                cc_snapshot=None,
                defaults=defaults,
                recording=False,
                transport_epoch=0,
                quality="draft",
            )
            == []
        )
        assert store.get_state(key) is not None
        assert "worker-chain" in store.effect_chain_topologies()
        assert runner._last_merged_mp_success_frame_id == 10
        assert runner.last_realized_t == pytest.approx(0.25)
    finally:
        runner.close()


def test_scene_runner_passes_runtime_profiles_to_realize_sessions() -> None:
    budget = ResourceBudget(
        max_output_vertices=123,
        max_output_lines=45,
        max_output_bytes=6_789,
    )
    preview_limits = RuntimeLimits(per_operation=budget, scene=budget)
    final_limits = RuntimeLimits()
    runner = _scene_runner(
        _empty_draw,
        perf=PerfCollector(enabled=False),
        n_worker=0,
        runtime_limit_profiles=RuntimeLimitProfiles(
            preview=preview_limits,
            final=final_limits,
        ),
    )
    try:
        assert runner._realize_sessions["draft"].runtime_limits is preview_limits
        assert runner._realize_sessions["final"].runtime_limits is final_limits
    finally:
        runner.close()
