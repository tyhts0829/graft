"""`from grafix import cc` が利用できることのテスト。"""

from __future__ import annotations

from grafix import cc
from grafix.core.parameters.context import parameter_context_from_snapshot


def test_cc_is_indexable_without_keyerror() -> None:
    assert cc[0] == 0.0
    assert cc[1] == 0.0


def test_cc_reads_from_parameter_context_cc_snapshot() -> None:
    with parameter_context_from_snapshot({}, cc_snapshot={0: 0.25}):
        assert cc[0] == 0.25
        assert cc[1] == 0.0

