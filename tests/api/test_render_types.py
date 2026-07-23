from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from grafix import (
    Color,
    ExportFormat,
    ExportResult,
    Frame,
    RenderOptions,
    RenderSession,
    export,
    render,
)
from grafix.api.render import ExportResult as RenderExportResult
from grafix.core.export_result import ExportResult as CoreExportResult
from grafix.core.parameters.style_resolver import FrameStyle


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("#09f", (0.0, 0.6, 1.0)),
        ("#FF0080", (1.0, 0.0, 128.0 / 255.0)),
        ("Rebecca Purple", (102.0 / 255.0, 51.0 / 255.0, 153.0 / 255.0)),
        ((255, 0, 128), (1.0, 0.0, 128.0 / 255.0)),
        ((1.0, 0.25, 0.0), (1.0, 0.25, 0.0)),
    ],
)
def test_color_normalizes_supported_inputs(value: object, expected: tuple[float, ...]) -> None:
    assert Color(value).rgb01 == pytest.approx(expected)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value",
    [
        "#12",
        "not-a-color",
        (256, 0, 0),
        (-1, 0, 0),
        (1.1, 0.0, 0.0),
        (float("nan"), 0.0, 0.0),
        (True, 0, 0),
        (0, 0),
    ],
)
def test_color_rejects_ambiguous_or_out_of_range_values(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        Color(value)  # type: ignore[arg-type]


def test_render_options_normalizes_colors_and_uses_single_thickness_default() -> None:
    options = RenderOptions(
        canvas_size=(120, 80),
        background_color="#fff",
        line_color=(255, 0, 0),
    )

    assert options.background_color.rgb01 == (1.0, 1.0, 1.0)
    assert options.line_color.rgb01 == (1.0, 0.0, 0.0)
    assert options.line_thickness == pytest.approx(0.001)


@pytest.mark.parametrize("thickness", [0.0, -0.1, float("inf"), float("nan")])
def test_render_options_rejects_invalid_thickness(thickness: float) -> None:
    with pytest.raises(ValueError, match="line_thickness"):
        RenderOptions(line_thickness=thickness)


@pytest.mark.parametrize("thickness", [True, "0.1"])
def test_render_options_rejects_non_numeric_thickness(thickness: object) -> None:
    with pytest.raises(TypeError, match="line_thickness"):
        RenderOptions(line_thickness=thickness)  # type: ignore[arg-type]


@pytest.mark.parametrize("t", [True, "1.0"])
def test_render_session_rejects_non_numeric_time(t: object) -> None:
    with RenderSession(lambda _t: ()) as session:
        with pytest.raises(TypeError, match="t"):
            session.render(t)  # type: ignore[arg-type]


def test_render_options_and_result_are_immutable() -> None:
    options = RenderOptions()
    result = ExportResult(
        path=Path("art.svg"),
        format=ExportFormat.SVG,
        manifest_path=Path("art.svg.capture.json"),
    )

    with pytest.raises(FrozenInstanceError):
        options.line_thickness = 0.5  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.path = Path("other.svg")  # type: ignore[misc]


def test_export_result_requires_keyword_only_complete_success_metadata() -> None:
    with pytest.raises(TypeError):
        ExportResult(  # type: ignore[misc]
            Path("art.svg"),
            ExportFormat.SVG,
            Path("art.svg.capture.json"),
        )
    with pytest.raises(TypeError):
        ExportResult(path=Path("art.svg"), format=ExportFormat.SVG)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "path": "art.svg",
            "format": ExportFormat.SVG,
            "manifest_path": Path("art.svg.capture.json"),
        },
        {
            "path": Path("art.svg"),
            "format": ExportFormat.SVG,
            "manifest_path": "art.svg.capture.json",
        },
    ],
)
def test_export_result_rejects_implicit_path_coercion(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(TypeError, match="Path"):
        ExportResult(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("drawing.svg", ExportFormat.SVG),
        ("drawing.PNG", ExportFormat.PNG),
        ("drawing.gcode", ExportFormat.GCODE),
    ],
)
def test_export_format_is_inferred_from_suffix(path: str, expected: ExportFormat) -> None:
    assert ExportFormat.from_path(path) is expected


def test_export_format_rejects_suffix_mismatch() -> None:
    with pytest.raises(ValueError, match="一致しません"):
        ExportFormat.resolve("drawing.svg", ExportFormat.PNG)
    with pytest.raises(ValueError, match="一致しません"):
        ExportResult(
            path=Path("drawing.svg"),
            format=ExportFormat.PNG,
            manifest_path=Path("drawing.svg.capture.json"),
        )


def test_export_format_rejects_string_as_a_second_input_shape() -> None:
    with pytest.raises(TypeError, match="ExportFormat"):
        ExportFormat.resolve("drawing.svg", "svg")  # type: ignore[arg-type]


def test_new_render_api_is_exported_from_root() -> None:
    import grafix

    assert grafix.Color is Color
    assert grafix.ExportResult is CoreExportResult
    assert RenderExportResult is CoreExportResult
    assert grafix.RenderOptions is RenderOptions
    assert Color.__module__ == "grafix.core.render_options"
    assert ExportResult.__module__ == "grafix.core.export_result"
    assert Frame.__module__ == "grafix.api.render"
    assert RenderOptions.__module__ == "grafix.core.render_options"
    assert RenderSession.__module__ == "grafix.api.render"
    assert render.__module__ == "grafix.api.render"
    assert export.__module__ == "grafix.api.export"


def test_side_effect_export_constructor_is_not_public() -> None:
    import grafix
    import grafix.api

    assert not hasattr(grafix, "Export")
    assert not hasattr(grafix.api, "Export")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"bg_color_rgb01": [0.0, 0.0, 0.0]},
        {"global_line_color_rgb01": (0.0, 0.0, float("inf"))},
        {"global_thickness": True},
    ],
)
def test_frame_style_validates_direct_construction(
    kwargs: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "bg_color_rgb01": (1.0, 1.0, 1.0),
        "global_line_color_rgb01": (0.0, 0.0, 0.0),
        "global_thickness": 0.001,
    }
    values.update(kwargs)
    with pytest.raises((TypeError, ValueError)):
        FrameStyle(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "changes",
    [
        {"layers": []},
        {"options": object()},
        {"style": object()},
        {"metadata": object()},
        {"provenance": object()},
    ],
)
def test_frame_rejects_noncanonical_composition_values(
    changes: dict[str, object],
) -> None:
    with RenderSession(lambda _t: ()) as session:
        frame = session.render(0.0)
    with pytest.raises(TypeError):
        replace(frame, **changes)


def test_frame_time_must_match_provenance() -> None:
    with RenderSession(lambda _t: ()) as session:
        frame = session.render(1.0)
    with pytest.raises(ValueError, match="provenance.frame.t"):
        replace(frame, t=2.0)


@pytest.mark.parametrize(
    "changes",
    [
        {"config_path": "config.yaml"},
        {"effective_config": object()},
        {"parameter_source": " CODE "},
        {"parameter_store_path": "parameters.json"},
        {"parameter_load_state": object()},
        {"provenance": object()},
    ],
)
def test_render_session_metadata_rejects_noncanonical_values(
    changes: dict[str, object],
) -> None:
    with RenderSession(lambda _t: ()) as session:
        metadata = session.metadata
    with pytest.raises((TypeError, ValueError)):
        replace(metadata, **changes)
