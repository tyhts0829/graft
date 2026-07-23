"""root の cc と headless render/export 公開契約を確認する。"""

from __future__ import annotations

from pathlib import Path

import pytest

from grafix import (
    ExportResult,
    Frame,
    RenderOptions,
    RenderSession,
    cc,
    render,
    save,
)
from grafix.core.parameters import MidiFrameSnapshot
from grafix.core.parameters.context import parameter_context_from_snapshot


def test_cc_is_indexable_without_keyerror() -> None:
    assert cc[0] == 0.0
    assert cc[1] == 0.0


def test_cc_reads_from_parameter_context_cc_snapshot() -> None:
    snapshot = MidiFrameSnapshot.from_mapping({0: 0.25}, source="midi_live")
    with parameter_context_from_snapshot({}, cc_snapshot=snapshot):
        assert cc[0] == 0.25
        assert cc[1] == 0.0


@pytest.mark.parametrize("invalid", [True, "1", 1.0])
def test_cc_rejects_non_integer_keys(invalid: object) -> None:
    with pytest.raises(TypeError, match="整数"):
        cc[invalid]  # type: ignore[index]
    with pytest.raises(TypeError, match="整数"):
        cc.get(invalid)  # type: ignore[arg-type]


@pytest.mark.parametrize("invalid", [-1, 128, 999])
def test_cc_rejects_out_of_range_keys(invalid: int) -> None:
    with pytest.raises(ValueError, match="0\\.\\.127"):
        cc[invalid]
    with pytest.raises(ValueError, match="0\\.\\.127"):
        cc.get(invalid)


def test_root_exports_headless_render_and_export_contract(tmp_path: Path) -> None:
    frame = render(lambda _t: [], options=RenderOptions(canvas_size=(32, 24)))
    result = save(frame, tmp_path / "frame.svg")

    assert isinstance(frame, Frame)
    assert isinstance(result, ExportResult)
    assert RenderSession.__module__ == "grafix.api.render"
    assert result.path == tmp_path / "frame.svg"
    assert result.path.is_file()
    assert result.manifest_path is not None
    assert result.manifest_path.is_file()
