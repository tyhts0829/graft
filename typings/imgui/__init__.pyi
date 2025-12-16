# どこで: `typings/imgui/__init__.pyi`。
# 何を: mypy 用に `imgui` を「全面 Any」として扱う最小 stub。
# なぜ: `imgui.get_io` 等が型定義に存在せず `attr-defined` が大量に出るのを避けるため。

from typing import Any


def __getattr__(name: str) -> Any: ...

