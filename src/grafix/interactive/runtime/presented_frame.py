"""表示フレームと capture provenance の同一寿命 state を管理する。"""

from __future__ import annotations

from dataclasses import dataclass, replace

from grafix.core.capture_provenance import CaptureProvenance
from grafix.core.gcode_params import GCodeParams
from grafix.core.parameters import ParamStore
from grafix.core.pipeline import RealizedLayer
from grafix.core.preview_quality import PreviewQuality
from grafix.export.capture_provenance import CaptureProvenanceBuilder
from grafix.interactive.runtime.export_job_system import (
    CaptureExportSnapshot,
    FrameExportSnapshot,
)


@dataclass(frozen=True, slots=True)
class _FrameProvenanceToken:
    """preview frame と provenance の生成条件を遅延評価用に固定する。"""

    builder: CaptureProvenanceBuilder
    store: ParamStore
    frame_index: int
    quality: PreviewQuality
    store_revision: int
    effective_revision: int


@dataclass(frozen=True, slots=True)
class PresentedFrame:
    """renderer へ渡す一回分の immutable presentation。"""

    layers: tuple[RealizedLayer, ...]
    t: float
    snapshot_revision: int | None
    frame_id: int | None
    scene_serial: int
    fresh: bool


@dataclass(frozen=True, slots=True)
class FramePublication:
    """表示成功後に recording/capture へ公開する値。"""

    provenance: CaptureProvenance | None
    capture_snapshot: CaptureExportSnapshot | None


class PresentedFrameState:
    """表示中 frame と、その provenance/capture binding 条件を所有する。

    SceneRunner の評価結果は :meth:`accept_evaluation` で取り込み、renderer へ渡す
    値は :meth:`prepare` で確定する。実際の描画が完了した後に :meth:`publish` を
    呼ぶことで、表示 metadata、preview snapshot、provenance token が同時に進む。
    """

    def __init__(
        self,
        *,
        store: ParamStore,
        provenance_builder: CaptureProvenanceBuilder,
    ) -> None:
        self._store = store
        self._provenance_builder = provenance_builder
        self._provenance_frame_index = 0
        self._layers: tuple[RealizedLayer, ...] = ()
        self._t = 0.0
        self._snapshot_revision: int | None = None
        self._frame_id: int | None = None
        self._scene_serial = 0
        self._export_snapshot: FrameExportSnapshot | None = None
        self._export_provenance_token: _FrameProvenanceToken | None = None

    @property
    def layers(self) -> tuple[RealizedLayer, ...]:
        """最後に SceneRunner から受理した layer を返す。"""

        return self._layers

    @property
    def t(self) -> float:
        """現在表示対象となる最後の実評価時刻を返す。"""

        return self._t

    @property
    def snapshot_revision(self) -> int | None:
        """最後に表示を完了した parameter snapshot revision を返す。"""

        return self._snapshot_revision

    @property
    def frame_id(self) -> int | None:
        """最後に表示を完了した SceneRunner frame ID を返す。"""

        return self._frame_id

    @property
    def scene_serial(self) -> int:
        """fresh な表示を完了するたびに進む renderer serial を返す。"""

        return self._scene_serial

    @property
    def export_snapshot(self) -> FrameExportSnapshot | None:
        """最後に表示を完了した fresh frame の preview snapshot を返す。"""

        return self._export_snapshot

    @property
    def provenance_token(self) -> _FrameProvenanceToken | None:
        """最新 preview snapshot に対応する遅延 provenance token を返す。"""

        return self._export_provenance_token

    @property
    def provenance_builder(self) -> CaptureProvenanceBuilder:
        """今後の fresh frame に使う provenance builder を返す。"""

        return self._provenance_builder

    @property
    def provenance_frame_index(self) -> int:
        """次に発行する provenance frame index を返す。"""

        return self._provenance_frame_index

    def replace_provenance_builder(self, builder: CaptureProvenanceBuilder) -> None:
        """今後の frame 用 builder を source reload 世代へ交換する。"""

        self._provenance_builder = builder

    def accept_evaluation(
        self,
        layers: list[RealizedLayer],
        *,
        realized_t: float | None,
    ) -> tuple[RealizedLayer, ...]:
        """SceneRunner が返した last-good layer と実評価時刻を取り込む。"""

        self._layers = tuple(layers)
        if realized_t is not None:
            self._t = float(realized_t)
        return self._layers

    def prepare(
        self,
        *,
        fresh: bool,
        snapshot_revision: int | None,
        frame_id: int | None,
    ) -> PresentedFrame:
        """renderer admission に使う presentation を副作用なしで確定する。"""

        revision = (
            self._snapshot_revision
            if snapshot_revision is None
            else int(snapshot_revision)
        )
        if fresh and revision is None:
            raise RuntimeError("fresh scene output did not publish a snapshot revision")
        if self._layers and revision is None:
            raise RuntimeError("realized scene output did not publish a snapshot revision")
        return PresentedFrame(
            layers=self._layers,
            t=float(self._t),
            snapshot_revision=revision,
            frame_id=None if frame_id is None else int(frame_id),
            scene_serial=self._scene_serial + int(bool(fresh)),
            fresh=bool(fresh),
        )

    def publish(
        self,
        frame: PresentedFrame,
        *,
        quality: PreviewQuality,
        canvas_size: tuple[int, int],
        background_color_rgb01: tuple[float, float, float],
        gcode_params: GCodeParams,
        capture_pending: bool,
        recording_needs_provenance: bool,
    ) -> FramePublication:
        """描画済み presentation を表示/capture/recording state へ反映する。"""

        provenance: CaptureProvenance | None = None
        capture_snapshot: CaptureExportSnapshot | None = None
        token: _FrameProvenanceToken | None = None
        snapshot: FrameExportSnapshot | None = None

        if frame.fresh:
            token = self._new_provenance_token(
                quality=quality,
                snapshot_revision=frame.snapshot_revision,
            )
            if capture_pending or recording_needs_provenance:
                provenance = self._materialize_provenance(token, t=frame.t)
                if provenance is None:
                    raise RuntimeError(
                        "fresh frame parameters changed before provenance materialization"
                    )
            snapshot = FrameExportSnapshot(
                layers=frame.layers,
                canvas_size=canvas_size,
                background_color_rgb01=background_color_rgb01,
                t=frame.t,
                provenance=None,
                gcode_params=gcode_params,
            )
            if capture_pending:
                if provenance is None:
                    raise RuntimeError("capture binding requires frame provenance")
                capture_snapshot = CaptureExportSnapshot.from_snapshot(
                    replace(snapshot, provenance=provenance)
                )

        self._snapshot_revision = frame.snapshot_revision
        self._frame_id = frame.frame_id
        self._scene_serial = frame.scene_serial
        if token is not None and snapshot is not None:
            self._commit_provenance_token(token)
            self._export_snapshot = snapshot
            self._export_provenance_token = token

        return FramePublication(
            provenance=provenance,
            capture_snapshot=capture_snapshot,
        )

    def frame_provenance(
        self,
        *,
        t: float,
        quality: PreviewQuality,
    ) -> CaptureProvenance:
        """現在の確定 store から独立した一 frame の provenance を生成する。"""

        token = self._new_provenance_token(quality=quality)
        provenance = self._materialize_provenance(token, t=t)
        if provenance is None:
            raise RuntimeError("provenance token no longer matches the parameter store")
        self._commit_provenance_token(token)
        return provenance

    def materialize_capture_snapshot(
        self,
        snapshot: FrameExportSnapshot,
    ) -> CaptureExportSnapshot:
        """preview snapshot を provenance 必須の capture snapshot へ昇格する。"""

        if snapshot.provenance is not None:
            return CaptureExportSnapshot.from_snapshot(snapshot)
        token = self._token_for_snapshot(snapshot)
        provenance = (
            self.frame_provenance(t=snapshot.t, quality="final")
            if token is None
            else self._materialize_provenance(token, t=snapshot.t)
        )
        if token is not None and provenance is None:
            raise RuntimeError(
                "preview snapshot parameters changed before provenance materialization"
            )
        if provenance is None:
            raise RuntimeError("capture provenance を生成できませんでした")
        return CaptureExportSnapshot.from_snapshot(
            replace(snapshot, provenance=provenance)
        )

    def capture_snapshot_for_shutdown(
        self,
        *,
        canvas_size: tuple[int, int],
        background_color_rgb01: tuple[float, float, float],
        gcode_params: GCodeParams,
    ) -> CaptureExportSnapshot | None:
        """最後の表示 frame を返し、stale token の場合だけ再評価を要求する。"""

        snapshot = self._export_snapshot
        if snapshot is not None:
            token = self._token_for_snapshot(snapshot)
            if token is not None and not self._provenance_token_is_current(token):
                return None
            return self.materialize_capture_snapshot(snapshot)
        empty_snapshot = FrameExportSnapshot(
            layers=self._layers,
            canvas_size=canvas_size,
            background_color_rgb01=background_color_rgb01,
            t=float(self._t),
            gcode_params=gcode_params,
        )
        return self.materialize_capture_snapshot(empty_snapshot)

    def _new_provenance_token(
        self,
        *,
        quality: PreviewQuality,
        snapshot_revision: int | None = None,
    ) -> _FrameProvenanceToken:
        store = self._store
        return _FrameProvenanceToken(
            builder=self._provenance_builder,
            store=store,
            frame_index=int(self._provenance_frame_index),
            quality=quality,
            store_revision=(
                int(store.revision)
                if snapshot_revision is None
                else int(snapshot_revision)
            ),
            effective_revision=int(store.effective_revision),
        )

    def _materialize_provenance(
        self,
        token: _FrameProvenanceToken,
        *,
        t: float,
    ) -> CaptureProvenance | None:
        if not self._provenance_token_is_current(token):
            return None
        return token.builder.frame(
            token.store,
            t=float(t),
            frame_index=token.frame_index,
            quality=token.quality,
            origin="interactive",
        )

    def _provenance_token_is_current(self, token: _FrameProvenanceToken) -> bool:
        store = token.store
        if store is not self._store:
            return False
        return (
            int(store.revision) == token.store_revision
            and int(store.effective_revision) == token.effective_revision
        )

    def _commit_provenance_token(self, token: _FrameProvenanceToken) -> None:
        self._provenance_frame_index = max(
            int(self._provenance_frame_index),
            token.frame_index + 1,
        )

    def _token_for_snapshot(
        self,
        snapshot: FrameExportSnapshot,
    ) -> _FrameProvenanceToken | None:
        if snapshot is not self._export_snapshot:
            return None
        return self._export_provenance_token


__all__ = [
    "FramePublication",
    "PresentedFrame",
    "PresentedFrameState",
]
