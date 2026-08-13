"""
Purpose:
    Geometry 評価の結果または external dependency 解決に必要な最小の immutable 設定を固定する。
Use when:
    evaluator が観測する設定、または evaluation scope の束縛を変更・調査する場合。
Constraints:
    - UI、MIDI、output path など full ``RuntimeConfig`` の application policy を含めない。
    - 変更可能な font file の内容は埋め込まず、lookup 時の external dependency identity に分ける。
    - binding は execution context に限定し、mutable global config にしない。
See:
    grafix.core.evaluation_context
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class EvaluationConfig:
    """Geometry の値または external dependency 解決へ影響する設定。"""

    font_dirs: tuple[Path, ...]

    def __post_init__(self) -> None:
        if type(self.font_dirs) is not tuple or any(
            not isinstance(path, Path) for path in self.font_dirs
        ):
            raise TypeError("font_dirs は Path の tuple である必要があります")


DEFAULT_EVALUATION_CONFIG = EvaluationConfig(font_dirs=())

_CURRENT_EVALUATION_CONFIG: ContextVar[EvaluationConfig] = ContextVar(
    "grafix_current_evaluation_config",
    default=DEFAULT_EVALUATION_CONFIG,
)


@contextmanager
def bind_evaluation_config(config: EvaluationConfig) -> Iterator[None]:
    """DAG evaluator scope に確定済みの評価設定を束縛する。"""

    if type(config) is not EvaluationConfig:
        raise TypeError("config は exact EvaluationConfig である必要があります")
    token = _CURRENT_EVALUATION_CONFIG.set(config)
    try:
        yield
    finally:
        _CURRENT_EVALUATION_CONFIG.reset(token)


def current_evaluation_config() -> EvaluationConfig:
    """現在の評価設定を返す。未束縛時は filesystem 非依存の既定値を返す。"""

    return _CURRENT_EVALUATION_CONFIG.get()


__all__ = [
    "DEFAULT_EVALUATION_CONFIG",
    "EvaluationConfig",
    "bind_evaluation_config",
    "current_evaluation_config",
]
