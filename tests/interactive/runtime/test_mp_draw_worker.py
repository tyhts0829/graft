"""mp-draw worker entrypoint の spawn pickle 契約。"""

from __future__ import annotations

import pickle

from grafix.interactive.runtime._mp_draw_worker import _draw_worker_main


def test_worker_entrypoint_is_module_top_level_and_picklable() -> None:
    restored = pickle.loads(pickle.dumps(_draw_worker_main))

    assert _draw_worker_main.__qualname__ == "_draw_worker_main"
    assert restored is _draw_worker_main
