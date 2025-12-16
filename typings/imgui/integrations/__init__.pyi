# どこで: `typings/imgui/integrations/__init__.pyi`。
# 何を: mypy 用に `imgui.integrations` を「全面 Any」として扱う最小 stub。
# なぜ: `imgui.integrations.*` が stub/py.typed を持たず `import-untyped` になるのを避けるため。

from typing import Any


def __getattr__(name: str) -> Any: ...

