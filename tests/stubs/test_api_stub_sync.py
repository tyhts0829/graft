"""`grafix.api.__init__.pyi` が最新生成結果と一致することのテスト。"""

from __future__ import annotations

import importlib
from pathlib import Path


def test_api_stub_sync(monkeypatch) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(repo_root))
    monkeypatch.syspath_prepend(str(repo_root / "src"))

    gen = importlib.import_module("tools.gen_g_stubs")
    expected = gen.generate_stubs_str()

    stub_path = repo_root / "src" / "grafix" / "api" / "__init__.pyi"
    actual = stub_path.read_text(encoding="utf-8")
    assert actual == expected

