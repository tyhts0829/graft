from __future__ import annotations

from pathlib import Path

from grafix.devtools.generate_stub import _ROOT_STUB, generate_stubs_str


def test_generated_stubs_export_render_session_types() -> None:
    api_stub = generate_stubs_str()

    for name in (
        "Color",
        "ExportFormat",
        "ExportResult",
        "Frame",
        "FrameStyle",
        "ParameterLoadState",
        "RealizedLayer",
        "RenderOptions",
        "RenderSession",
        "RenderSessionMetadata",
        "RuntimeConfig",
    ):
        assert f"{name} as {name}" in api_stub
        assert f"{name} as {name}" in _ROOT_STUB

    for name in (
        "GCodeParams",
        "Geometry",
        "GeometryCacheKey",
        "Layer",
        "ParamMeta",
        "RealizedGeometry",
    ):
        assert f"{name} as {name}" in api_stub

    for name in ("render", "render_variation_batch", "run", "save"):
        assert f"{name} as {name}" not in api_stub
        assert f"{name} as {name}" in _ROOT_STUB

    assert "config_fallback" not in api_stub
    assert "export as export" not in api_stub
    assert "export as export" not in _ROOT_STUB
    assert "Export as Export" not in api_stub
    assert "Export as Export" not in _ROOT_STUB


def test_generated_stubs_export_operation_info_explicitly() -> None:
    api_stub = generate_stubs_str()

    assert (
        "from grafix.api.operation_info import OperationInfo as OperationInfo"
        in api_stub
    )
    assert "OperationInfo as OperationInfo" in _ROOT_STUB
    assert "'OperationInfo'" in api_stub
    assert '"OperationInfo"' in _ROOT_STUB


def test_root_stub_exports_canvas_sizes_without_adding_them_to_api_stub() -> None:
    api_stub = generate_stubs_str()
    names = (
        "A2",
        "A2_LANDSCAPE",
        "A3",
        "A3_LANDSCAPE",
        "A4",
        "A4_LANDSCAPE",
        "A5",
        "A5_LANDSCAPE",
        "A6",
        "A6_LANDSCAPE",
        "SQUARE",
    )

    for name in names:
        assert f"{name} as {name}" in _ROOT_STUB
        assert f'"{name}"' in _ROOT_STUB
        assert f"{name} as {name}" not in api_stub


def test_checked_in_root_stub_matches_generator_template() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    checked_in = (repo_root / "typings/grafix/__init__.pyi").read_text(encoding="utf-8")

    assert checked_in == _ROOT_STUB
