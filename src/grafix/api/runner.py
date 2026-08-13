"""
Purpose:
    interactive previewの正規signatureを持つ、import負荷の小さい公開wrapperを提供する。
Use when:
    `run()`の公開引数、default、またはheavy applicationへの委譲を変更する場合。
Constraints:
    - module importやsignature inspectionではGUI/runtimeを初期化しない。
    - heavy `_runner_application`は`run()`呼び出し時にだけimportする。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from grafix.core.runtime_config import RuntimeConfig
from grafix.core.runtime_limits import (
    DEFAULT_RUNTIME_LIMIT_PROFILES,
    RuntimeLimitProfiles,
)
from grafix.core.scene import SceneItem


def run(
    draw: Callable[[float], SceneItem],
    *,
    config_path: str | Path | None = None,
    config: RuntimeConfig | None = None,
    run_id: str | None = None,
    background_color: tuple[float, float, float] = (1.0, 1.0, 1.0),
    line_thickness: float = 0.001,
    line_color: tuple[float, float, float] = (0.0, 0.0, 0.0),
    render_scale: float | None = None,
    canvas_size: tuple[int, int] = (800, 800),
    parameter_gui: bool = True,
    parameter_persistence: bool = True,
    midi_port_name: str | None = "auto",
    midi_mode: str = "7bit",
    n_worker: int = 1,
    evaluation_timeout: float | None = 5.0,
    fps: float = 60.0,
    seed: int | None = None,
    runtime_limit_profiles: RuntimeLimitProfiles = DEFAULT_RUNTIME_LIMIT_PROFILES,
) -> None:
    """``draw(t)`` のシーンを interactive window へ描画する。

    Parameters
    ----------
    draw : Callable[[float], SceneItem]
        フレーム経過秒を受け取り、描画するシーンを返す関数。
    config_path : str, Path or None, optional
        設定ファイルの明示パス。``config`` との同時指定はできない。
    config : RuntimeConfig or None, optional
        呼び出し元で確定済みの設定。
    run_id : str or None, optional
        出力と session の識別子。
    background_color : tuple[float, float, float], optional
        背景色 RGB。
    line_thickness : float, optional
        Layer で未指定の場合の線幅。
    line_color : tuple[float, float, float], optional
        Layer で未指定の場合の線色 RGB。
    render_scale : float or None, optional
        preview のピクセル倍率。数値を指定した場合は、保存済みの
        WorkspaceState よりも現在の倍率によるサイズを優先する。画面を
        超える場合のみ、アスペクト比を保って縮小する。``None`` では
        保存済み preview サイズを復元し、保存状態がなければ 1.0 倍を
        使用する。
    canvas_size : tuple[int, int], optional
        論理キャンバス寸法。
    parameter_gui : bool, optional
        Parameter GUI を開くか。
    parameter_persistence : bool, optional
        ParameterStore の復元と保存を有効にするか。
    midi_port_name : str or None, optional
        MIDI 入力ポート名。``"auto"`` は自動選択、``None`` は無効。
    midi_mode : {"7bit", "14bit"}, optional
        MIDI CC の解釈モード。
    n_worker : int, optional
        background draw worker 数。``0`` は main process で同期評価する。
    evaluation_timeout : float or None, optional
        background draw 一回の timeout 秒。
    fps : float, optional
        目標フレームレート。0 以下では frame pacing を行わない。
    seed : int or None, optional
        capture provenance に記録する作品 seed。
    runtime_limit_profiles : RuntimeLimitProfiles, optional
        preview/final の resource 上限。

    Notes
    -----
    GUI と config loader は、この関数が呼ばれるまで import しない。
    """

    from grafix.api._runner_application import _run_interactive_application

    _run_interactive_application(
        draw,
        config_path=config_path,
        config=config,
        config_fallback=None,
        run_id=run_id,
        background_color=background_color,
        line_thickness=line_thickness,
        line_color=line_color,
        render_scale=render_scale,
        canvas_size=canvas_size,
        parameter_gui=parameter_gui,
        parameter_persistence=parameter_persistence,
        midi_port_name=midi_port_name,
        midi_mode=midi_mode,
        n_worker=n_worker,
        evaluation_timeout=evaluation_timeout,
        fps=fps,
        seed=seed,
        runtime_limit_profiles=runtime_limit_profiles,
    )


__all__ = ["run"]
