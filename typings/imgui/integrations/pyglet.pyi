# どこで: `typings/imgui/integrations/pyglet.pyi`。
# 何を: mypy 用に `imgui.integrations.pyglet` を「全面 Any」として扱う最小 stub。
# なぜ: pyglet backend の型が無く `import-untyped` / `attr-defined` が発生するのを避けるため。

from typing import Any


def __getattr__(name: str) -> Any: ...

