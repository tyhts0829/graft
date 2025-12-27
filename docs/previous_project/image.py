"""
どこで: `engine.export.image`。
何を: 現在の描画ウィンドウ内容を PNG として保存するラッパ（最小実装）。
なぜ: ワンアクションでスクリーンショットを得られるようにするため。

Stage 4: オーバーレイ含む低コストパス（window バッファ保存）のみ実装。
高解像度/オーバーレイ除外/透過背景は将来の FBO 経由パスで対応する。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from util.paths import ensure_screenshots_dir


def save_png(
    window: Any,
    path: Path | None = None,
    *,
    scale: float = 1.0,
    include_overlay: bool = True,
    transparent: bool = False,
    mgl_context: object | None = None,
    draw: Callable[[], None] | None = None,
    name_prefix: Optional[str] = None,
    width_mm: Optional[float] = None,
    height_mm: Optional[float] = None,
) -> Path:
    """現在のウィンドウ内容を PNG として保存する。

    Parameters
    ----------
    window : pyglet.window.Window
        対象ウィンドウ。
    path : Path | None
        出力先パス。None の場合は既定の `screenshots/` に命名規則
        `{name_prefix}_{W}x{H}_{yymmdd_hhmmss}.png`（prefix なし: `YYYYmmdd_HHMMSS_{W}x{H}.png`）で保存。
    scale : float, default 1.0
        画像のスケール。1.0 以外は現段階では未対応（NotImplementedError）。
    include_overlay : bool, default True
        True の場合はウィンドウの見た目そのまま（HUD含む）を保存。
        False は現段階では未対応（NotImplementedError）。
    transparent : bool, default False
        透過背景。現段階では未対応（NotImplementedError）。

    Returns
    -------
    Path
        保存先のファイルパス。
    """
    if include_overlay:
        # 画面バッファをそのまま保存するパス。pyglet が必要。
        try:
            import pyglet  # type: ignore
        except Exception as e:  # pragma: no cover - 実行時依存
            raise RuntimeError(f"pyglet の利用に失敗: {e}") from e
        if scale != 1.0:
            raise NotImplementedError("scale != 1.0 は未対応（将来 FBO にて対応予定）")
        if transparent:
            raise NotImplementedError("transparent=True は未対応（将来 FBO にて対応予定）")
    else:
        # オフスクリーン描画（FBO）でラインのみを描く
        if mgl_context is None or draw is None:
            raise ValueError("include_overlay=False では mgl_context と draw が必須です")

    if path is None:
        out_dir = ensure_screenshots_dir()
        # W/H はキャンバス寸法 [mm] を優先。未指定時はピクセルから推定（既存挙動に近い）。
        if width_mm and width_mm > 0 and height_mm and height_mm > 0:
            dims = f"{int(round(width_mm))}x{int(round(height_mm))}"
        else:
            # ピクセルベース（従来）。include_overlay=True では scale=1.0 固定。
            scale_tag = 1.0 if include_overlay else float(scale)
            w = int(round(float(getattr(window, "width", 0)) * scale_tag))
            h = int(round(float(getattr(window, "height", 0)) * scale_tag))
            dims = f"{w}x{h}" if w > 0 and h > 0 else "unknownWxH"

        if name_prefix:
            ts = datetime.now().strftime("%y%m%d_%H%M%S")
            base = f"{name_prefix}_{dims}_{ts}"
        else:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            base = f"{ts}_{dims}"
        path = _unique_path(out_dir / f"{base}.png")

    if include_overlay:
        # バッファからそのまま保存（RGBA）
        try:
            buffer = pyglet.image.get_buffer_manager().get_color_buffer()
            buffer.save(str(path))
        except Exception as e:  # pyglet が未初期化/ヘッドレスなど
            raise RuntimeError(f"PNG 保存に失敗: {e}") from e
        return path
    else:
        # FBO 経由で高解像度（overlay なし）を描画
        # 型を緩く扱う（mypy: Any）、実行時は moderngl.Context を想定
        assert mgl_context is not None
        assert draw is not None
        ctx = mgl_context  # type: ignore[assignment]

        # ビューポートと既定FBOを退避
        try:
            old_viewport = ctx.viewport  # type: ignore[attr-defined]
        except Exception:
            old_viewport = None

        width = int(round(float(window.width) * float(scale)))
        height = int(round(float(window.height) * float(scale)))
        if width <= 0 or height <= 0:
            raise ValueError("出力解像度が不正です（width/height <= 0）")

        fbo = None
        try:
            fbo = ctx.simple_framebuffer((width, height), components=4)  # type: ignore[attr-defined]
            fbo.use()
            # 背景色を取得（RenderWindowの _bg_color があれば利用）
            bg = getattr(window, "_bg_color", (1.0, 1.0, 1.0, 1.0))
            r, g, b, a = bg if not transparent else (bg[0], bg[1], bg[2], 0.0)
            fbo.clear(r, g, b, a)
            # ビューポートをFBOサイズへ
            ctx.viewport = (0, 0, width, height)  # type: ignore[attr-defined]
            # ラインのみ描画
            draw()
            # ピクセルを読み出し（上下反転ピッチで pyglet に渡す）
            data = fbo.read(components=4, alignment=1)
        except Exception as e:
            raise RuntimeError(f"オフスクリーン描画に失敗: {e}") from e
        finally:
            # 後片付け（ビューポートとデフォルトFBOへ戻す）
            try:
                if old_viewport is not None:
                    ctx.viewport = old_viewport  # type: ignore[attr-defined]
                # 既定フレームバッファへ
                ctx.screen.use()  # type: ignore[attr-defined]
            except Exception:
                pass
            try:
                if fbo is not None:
                    fbo.release()
            except Exception:
                pass

        try:
            import pyglet  # type: ignore

            img = pyglet.image.ImageData(width, height, "RGBA", data, pitch=width * 4)
            img.save(str(path))
        except Exception as e:
            raise RuntimeError(f"PNG 書き出しに失敗: {e}") from e
        return path


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    i = 1
    while True:
        cand = parent / f"{stem}-{i}{suffix}"
        if not cand.exists():
            return cand
        i += 1
