"""
Purpose:
    Inspector windowとParameter GUIをinteractive runtimeの一frame taskとして配線する。
Use when:
    GUI window lifecycle、catalog reload、autosave tick、parameter revision通知を変更するとき。
Constraints:
    - draw_frameはback bufferだけを更新し、flipは共通window loopへ委ねる。
    - ParameterGUIは構築時からwindowを所有するため、system側で二重にcloseしない。
    - revision通知は値が実際に変わったframeだけを、style/geometry domainへ分類して送る。
    - parameter編集中はautosave debounceを尊重し、保存失敗でpreviewを停止しない。
Side effects:
    GUI window、parameter store、autosave、monitor通知を更新する。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from grafix.interactive.parameter_gui import ParameterGUI, create_parameter_gui_window
from grafix.core.parameters import ParamStore
from grafix.core.parameters.layer_style import LAYER_STYLE_OP
from grafix.core.parameters.style import STYLE_OP
from grafix.core.runtime_config import RuntimeConfig
from grafix.core.value_validation import finite_real
from grafix.interactive.midi import MidiSession

if TYPE_CHECKING:
    from grafix.core.parameters.autosave import ParamStoreAutosave
    from grafix.core.parameters.history import ParamSnapshotSlots, ParamStoreHistory
    from grafix.interactive.transport import TransportClock
    from grafix.interactive.runtime.monitor import RuntimeMonitor
    from grafix.interactive.parameter_gui.variation_panel import (
        VariationThumbnailCapture,
        VariationThumbnailPreview,
    )
    from grafix.interactive.parameter_gui.catalog import ParameterGuiCatalog

_logger = logging.getLogger(__name__)


class ParameterGUIWindowSystem:
    """Parameter GUI（別ウィンドウ）のサブシステム。"""

    def __init__(
        self,
        *,
        effective_config: RuntimeConfig,
        store: ParamStore,
        midi_session: MidiSession | None = None,
        monitor: RuntimeMonitor | None = None,
        transport: TransportClock | None = None,
        transport_fps: float = 60.0,
        history: ParamStoreHistory | None = None,
        snapshot_slots: ParamSnapshotSlots | None = None,
        autosave: ParamStoreAutosave | None = None,
        is_recording: Callable[[], bool] | None = None,
        variation_thumbnail_capture: VariationThumbnailCapture | None = None,
        variation_thumbnail_preview: VariationThumbnailPreview | None = None,
        ui_scale: float = 1.0,
        catalog: ParameterGuiCatalog | None = None,
        catalog_provider: Callable[[], ParameterGuiCatalog] | None = None,
        on_parameter_revision_created: (
            Callable[[int, int, str], None] | None
        ) = None,
    ) -> None:
        """GUI 用の window と ParameterGUI を初期化する。"""

        frame_rate = finite_real(
            transport_fps,
            name="transport_fps",
            minimum=0.0,
            minimum_inclusive=False,
        )
        scale = finite_real(
            ui_scale,
            name="ui_scale",
            minimum=0.0,
            minimum_inclusive=False,
        )
        if catalog_provider is not None and not callable(catalog_provider):
            raise TypeError("catalog_provider は callable または None である必要があります")
        w, h = effective_config.parameter_gui_window_size
        window = create_parameter_gui_window(width=w, height=h, vsync=False)
        self.window = window
        self._store = store
        self._autosave = autosave
        self._monitor = monitor
        self._on_parameter_revision_created = on_parameter_revision_created
        self._catalog_provider = catalog_provider
        # ParameterGUI は constructor 入口から window を所有し、途中失敗時も自ら閉じる。
        # system 側で同じ window を再度閉じず、所有権を一箇所に保つ。
        self._gui = ParameterGUI(
            window,
            effective_config=effective_config,
            store=store,
            midi_session=midi_session,
            monitor=monitor,
            transport=transport,
            transport_fps=frame_rate,
            history=history,
            snapshot_slots=snapshot_slots,
            is_recording=is_recording,
            variation_thumbnail_capture=variation_thumbnail_capture,
            variation_thumbnail_preview=variation_thumbnail_preview,
            ui_scale=scale,
            catalog=catalog,
        )

    def draw_frame(self) -> None:
        """1 フレーム分の GUI を描画する（`flip()` は呼ばない）。"""

        store = self._store
        catalog_provider = self._catalog_provider
        if catalog_provider is not None:
            self._gui.replace_catalog(catalog_provider())
        revision_before = int(store.revision)
        value_revision_before = int(store.value_revision)
        input_started_ns = time.monotonic_ns()
        self._gui.draw_frame()
        revision_after = int(store.revision)
        value_revision_after = int(store.value_revision)
        callback = self._on_parameter_revision_created
        if (
            callback is not None
            and revision_after != revision_before
            and value_revision_after != value_revision_before
        ):
            changed_keys = store.value_changes_since(value_revision_before)
            domains = (
                {"geometry"}
                if changed_keys is None
                else {
                    (
                        "style"
                        if key.op in {STYLE_OP, LAYER_STYLE_OP}
                        else "geometry"
                    )
                    for key in changed_keys
                }
            )
            for domain in sorted(domains):
                callback(revision_after, input_started_ns, domain)
        autosave = self._autosave
        if autosave is not None:
            try:
                autosave.tick(
                    suspended=bool(self._gui.parameter_edit_active),
                )
            except Exception:
                # preview は継続する。helper 側の debounce により毎 frame の再試行も避ける。
                _logger.exception("Failed to autosave ParameterStore: %s", autosave.path)
            finally:
                monitor = self._monitor
                if monitor is not None:
                    monitor.set_autosave(
                        status=autosave.status,
                        error=autosave.last_error,
                        source=str(autosave.path),
                    )

    def close(self) -> None:
        """GUI を終了し、ウィンドウを破棄する。"""

        self._gui.close()
