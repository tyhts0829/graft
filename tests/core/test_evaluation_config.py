from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from grafix.core.evaluation_config import (
    EvaluationConfig,
    bind_evaluation_config,
    current_evaluation_config,
)
from grafix.core.evaluation_context import evaluation_fingerprint
from grafix.runtime_config_loader import runtime_config


def _evaluation_config(font_dirs: tuple[Path, ...]) -> EvaluationConfig:
    return EvaluationConfig(font_dirs=font_dirs)


def test_evaluation_fingerprint_ignores_ui_export_and_midi_config() -> None:
    runtime = runtime_config()
    unrelated = replace(
        runtime,
        window_pos_draw=(runtime.window_pos_draw[0] + 1, runtime.window_pos_draw[1]),
        png_scale=runtime.png_scale + 1.0,
        midi_inputs=(("Different", "14bit"),),
    )

    first = evaluation_fingerprint(
        quality="final",
        config=_evaluation_config(runtime.font_dirs),
    )
    second = evaluation_fingerprint(
        quality="final",
        config=_evaluation_config(unrelated.font_dirs),
    )

    assert first == second


def test_evaluation_fingerprint_changes_with_font_dirs() -> None:
    first = evaluation_fingerprint(
        quality="final",
        config=_evaluation_config((Path("fonts-a"),)),
    )
    second = evaluation_fingerprint(
        quality="final",
        config=_evaluation_config((Path("fonts-b"),)),
    )

    assert first != second


def test_evaluation_config_binding_is_nested_and_restored() -> None:
    default = current_evaluation_config()
    outer = _evaluation_config((Path("outer"),))
    inner = _evaluation_config((Path("inner"),))

    with bind_evaluation_config(outer):
        assert current_evaluation_config() is outer
        with bind_evaluation_config(inner):
            assert current_evaluation_config() is inner
        assert current_evaluation_config() is outer

    assert current_evaluation_config() is default


def test_evaluation_config_rejects_mutable_or_non_path_dirs() -> None:
    with pytest.raises(TypeError, match="tuple"):
        EvaluationConfig(font_dirs=[])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="Path"):
        EvaluationConfig(font_dirs=("fonts",))  # type: ignore[arg-type]
