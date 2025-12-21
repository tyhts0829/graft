# どこで: `src/grafix/interactive/runtime/scene_runner.py`。
# 何を: parameter_context + (sync / mp-draw) で `realize_scene()` を実行し realized_layers を返す。
# なぜ: draw の実行戦略（mp/sync/録画中の例外）を 1 箇所に固定するため。

from __future__ import annotations

from collections.abc import Callable

from grafix.core.layer import LayerStyleDefaults
from grafix.core.parameters import ParamStore, current_frame_params, current_param_snapshot, parameter_context
from grafix.core.pipeline import RealizedLayer, realize_scene
from grafix.core.scene import SceneItem
from grafix.interactive.runtime.mp_draw import MpDraw
from grafix.interactive.runtime.perf import PerfCollector


class SceneRunner:
    """このフレームで描くべき realized_layers を生成する。"""

    def __init__(
        self,
        draw: Callable[[float], SceneItem],
        *,
        perf: PerfCollector,
        n_worker: int,
    ) -> None:
        self._draw = draw
        self._perf = perf
        self._mp_draw: MpDraw | None = MpDraw(draw, n_worker=int(n_worker)) if int(n_worker) > 1 else None

    def run(
        self,
        t: float,
        *,
        store: ParamStore,
        cc_snapshot: dict[int, float] | None,
        defaults: LayerStyleDefaults,
        recording: bool,
    ) -> list[RealizedLayer]:
        """シーンを実行して realized_layers を返す。"""

        perf = self._perf
        with parameter_context(store, cc_snapshot=cc_snapshot):
            mp_draw = None if recording else self._mp_draw
            if mp_draw is None:
                draw_fn = self._draw
                if perf.enabled:

                    def draw_fn_timed(t_arg: float) -> SceneItem:
                        with perf.section("draw"):
                            return self._draw(t_arg)

                    draw_fn = draw_fn_timed
                with perf.section("scene"):
                    return realize_scene(draw_fn, t, defaults)

            mp_draw.submit(
                t=t,
                snapshot=current_param_snapshot(),
                cc_snapshot=cc_snapshot,
            )

            new_result = mp_draw.poll_latest()
            if new_result is not None:
                if new_result.error is not None:
                    raise RuntimeError(
                        "mp-draw worker で例外が発生しました:\n" f"{new_result.error}"
                    )
                frame_params = current_frame_params()
                if frame_params is not None:
                    frame_params.records.extend(new_result.records)
                    frame_params.labels.extend(new_result.labels)

            layers = mp_draw.latest_layers()
            if layers is None:
                return []

            def draw_from_mp(_t_arg: float) -> SceneItem:
                return layers

            with perf.section("scene"):
                return realize_scene(draw_from_mp, t, defaults)

    def close(self) -> None:
        """mp-draw worker を終了する。"""

        if self._mp_draw is not None:
            self._mp_draw.close()
            self._mp_draw = None

