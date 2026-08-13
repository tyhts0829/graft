"""
Purpose:
    pyglet window固有のGL/Cocoa参照を、native window close前に安全に解放する。
Use when:
    interactive windowのteardown、context activation、またはplatform固有resource leakを扱う場合。
Constraints:
    - GL objectは所有windowのcontextがcurrentな場合だけ明示deleteする。
    - cleanupを最後まで試し、最初のerrorを保持したままwindowを必ずcloseする。
Side effects:
    GL resource、ctypes cache、Cocoa text-input参照、native windowを変更する。
"""

from __future__ import annotations

import ctypes
import sys
from typing import Any

from grafix.core.lifecycle import CleanupErrors

_CONTEXT_ATTRIBUTE_MISSING = object()


def activate_pyglet_window_context(window: Any) -> bool:
    """生存中の pyglet window context を current にする。

    ``CocoaWindow.switch_to()`` は close 後の ``context is None`` を例外なく無視する。
    その状態を成功と誤認して別 context 上で GL delete しないよう、実 pyglet window
    では context の生存を先に確認する。最小 fake/protocol は従来どおり
    ``switch_to()`` の成否を契約とする。
    """

    context = getattr(window, "context", _CONTEXT_ATTRIBUTE_MISSING)
    if context is None:
        return False
    window.switch_to()
    return True


def _drop_reference(owner: Any, name: str) -> None:
    """存在する参照だけを ``None`` へ置き換える。"""

    if getattr(owner, name, None) is not None:
        setattr(owner, name, None)


def _detach_dynamic_ubo_view(ubo: Any) -> None:
    """終了する既定 UBO 専用の動的 ctypes view 型を pointer cache から外す。

    pyglet 2.1 の ``UniformBlock._introspect_uniforms()`` は window ごとに新しい
    ``ctypes.Structure`` を作り、``pointer(view)`` がその型を process-global cache
    へ登録する。既定 UBO は window 専用なので、view/pointer instance を切った後、
    cache が同じ pointer 型を指す場合だけその一件を除く。
    """

    view = getattr(ubo, "view", None)
    view_pointer = getattr(ubo, "_view_ptr", None)
    view_type = None if view is None else type(view)
    pointer_type = None if view_pointer is None else type(view_pointer)
    _drop_reference(ubo, "_view_ptr")
    _drop_reference(ubo, "view")
    if view_type is None or pointer_type is None:
        return
    pointer_cache = getattr(ctypes, "_pointer_type_cache", None)
    if isinstance(pointer_cache, dict) and pointer_cache.get(view_type) is pointer_type:
        pointer_cache.pop(view_type, None)


def _delete_default_gl_resources(window: Any) -> None:
    """pyglet 2.1 が生成した既定 UBO / program を current context 上で解放する。"""

    errors = CleanupErrors()

    ubo = getattr(window, "ubo", None)
    buffer = None if ubo is None else getattr(ubo, "buffer", None)
    if buffer is not None and getattr(buffer, "id", None) is not None:
        delete_buffer = getattr(buffer, "delete", None)
        if callable(delete_buffer):
            errors.attempt(delete_buffer, "delete pyglet default UBO")
    if ubo is not None:
        errors.attempt(
            lambda: _detach_dynamic_ubo_view(ubo),
            "detach pyglet default UBO view",
        )
    errors.attempt(lambda: _drop_reference(window, "ubo"), "drop pyglet default UBO")

    program = getattr(window, "_default_program", None)
    if program is not None and getattr(program, "id", None) is not None:
        delete_program = getattr(program, "delete", None)
        if callable(delete_program):
            errors.attempt(delete_program, "delete pyglet default program")
    errors.attempt(
        lambda: _drop_reference(window, "_default_program"),
        "drop pyglet default program",
    )
    errors.raise_if_any()


def _detach_cocoa_text_input(window: Any) -> None:
    """pyglet 2.1 Cocoa text view から閉じる Python window への参照を切る。"""

    if sys.platform != "darwin":
        return
    view = getattr(window, "_nsview", None)
    text_view = None if view is None else getattr(view, "_textview", None)
    if text_view is not None:
        _drop_reference(text_view, "_window")


def close_pyglet_window(window: Any) -> None:
    """pyglet 2.1 の既定 resource を解放してから window を必ず閉じる。

    pyglet 2.1 は window ごとに既定 UBO / shader program を生成するが、通常の
    ``Window.close()`` では明示解放しない。また macOS の text-input view は native
    dealloc まで Python window を保持する。依存ライブラリ固有の private adapter を
    この境界へ隔離し、Grafix が所有する window を同一手順で終了する。
    """

    errors = CleanupErrors()
    context_active = False
    try:
        context_active = activate_pyglet_window_context(window)
    except BaseException as error:
        errors.record(error, "activate pyglet GL context")

    if context_active:
        errors.attempt(
            lambda: _delete_default_gl_resources(window),
            "release pyglet default GL resources",
        )
    else:
        # 別 context で raw GL delete は行わない。native context の destroy に任せつつ、
        # Python object graph だけは切って close 後の保持を防ぐ。
        ubo = getattr(window, "ubo", None)
        if ubo is not None:
            errors.attempt(
                lambda: _detach_dynamic_ubo_view(ubo),
                "detach pyglet default UBO view",
            )
        errors.attempt(lambda: _drop_reference(window, "ubo"), "drop pyglet default UBO")
        errors.attempt(
            lambda: _drop_reference(window, "_default_program"),
            "drop pyglet default program",
        )

    errors.attempt(
        lambda: _detach_cocoa_text_input(window),
        "detach Cocoa text input",
    )
    errors.attempt(window.close, "close pyglet window")
    errors.raise_if_any()


__all__ = ["activate_pyglet_window_context", "close_pyglet_window"]
