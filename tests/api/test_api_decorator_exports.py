"""`grafix.api` が `primitive/effect` デコレータを公開していることのテスト。"""

from __future__ import annotations

import importlib
from pathlib import Path


def test_api_exports_primitive_and_effect(monkeypatch) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(repo_root / "src"))

    api = importlib.import_module("grafix.api")
    assert callable(getattr(api, "primitive", None))
    assert callable(getattr(api, "effect", None))
