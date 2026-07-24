from __future__ import annotations

from pathlib import Path

import pytest

from grafix.core.capture_manifest import (
    CAPTURE_MANIFEST_SCHEMA_VERSION,
    CaptureManifest,
    RecordingManifest,
)
from tests.capture_manifest_test_support import _capture_provenance


def test_capture_manifest_serializes_explicit_provenance() -> None:
    manifest = CaptureManifest(
        t=1.25,
        canvas_size=(800, 600),
        format="png",
        artifact_paths=(Path("output/capture.png"),),
        provenance=_capture_provenance(1.25),
        output_size=(800, 600),
    )

    payload = manifest.as_dict()

    assert payload["schema_version"] == CAPTURE_MANIFEST_SCHEMA_VERSION == 3
    assert "t" not in payload
    assert "canvas_size" not in payload
    assert "format" not in payload
    assert "artifact_paths" not in payload
    assert payload["grafix"] == {"version": "test"}
    assert payload["source"]["available"] is False
    assert payload["source"]["unavailable_reason"] == "test fixture has no source file"
    assert payload["git"]["available"] is False
    assert payload["config"]["effective"] == {}
    assert payload["parameters"]["source"] == "test"
    assert payload["parameters"]["snapshot_hash"]["algorithm"] == "sha256"
    assert payload["seed"] is None
    assert payload["frame"] == {
        "t": 1.25,
        "index": None,
        "quality": "final",
        "origin": "headless",
    }
    assert payload["output"] == {
        "format": "png",
        "artifact_paths": ["output/capture.png"],
        "canvas_size": {"width": 800, "height": 600},
        "size": {"width": 800, "height": 600},
    }
    assert payload["recording"] is None


def test_recording_manifest_serializes_pause_policy_and_counts() -> None:
    recording = RecordingManifest(
        fps=30.0,
        frame_count=12,
        dropped_frame_count=2,
        duplicated_frame_count=0,
        error_count=2,
        stop_reason="user_stop",
        last_error="ValueError: broken scene",
    )
    manifest = CaptureManifest(
        t=1.25,
        canvas_size=(800, 600),
        format="mp4",
        artifact_paths=(Path("output/capture.mp4"),),
        provenance=_capture_provenance(1.25),
        output_size=(1600, 1200),
        recording=recording,
    )

    payload = manifest.as_dict()

    assert payload["output"]["size"] == {"width": 1600, "height": 1200}
    assert payload["recording"] == {
        "fps": 30.0,
        "frame_count": 12,
        "dropped_frame_count": 2,
        "duplicated_frame_count": 0,
        "error_count": 2,
        "error_policy": "pause",
        "stop_reason": "user_stop",
        "abort_reason": None,
        "last_error": "ValueError: broken scene",
    }


def test_capture_manifest_rejects_missing_provenance() -> None:
    with pytest.raises(TypeError, match="provenance"):
        CaptureManifest(
            t=0.0,
            canvas_size=(100, 100),
            format="svg",
            artifact_paths=(Path("capture.svg"),),
            provenance=None,  # type: ignore[arg-type]
            output_size=(100, 100),
        )


def test_capture_manifest_serializes_multiple_layer_artifacts() -> None:
    artifacts = (
        Path("output/capture_layer001.gcode"),
        Path("output/capture_layer002.gcode"),
    )
    manifest = CaptureManifest(
        t=0.0,
        canvas_size=(210, 297),
        format="gcode",
        artifact_paths=artifacts,
        provenance=_capture_provenance(0.0),
        output_size=(210, 297),
    )
    payload = manifest.as_dict()

    assert payload["output"] == {
        "format": "gcode",
        "artifact_paths": [
            "output/capture_layer001.gcode",
            "output/capture_layer002.gcode",
        ],
        "canvas_size": {"width": 210, "height": 297},
        "size": {"width": 210, "height": 297},
    }


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"t": float("nan")}, "t"),
        ({"canvas_size": (0, 100)}, "canvas_size"),
        ({"format": "."}, "format"),
        ({"artifact_paths": ()}, "artifact_paths"),
        ({"output_size": (0, 100)}, "output_size"),
    ],
)
def test_capture_manifest_validates_fields(kwargs: dict[str, object], message: str) -> None:
    values: dict[str, object] = {
        "t": 0.0,
        "canvas_size": (100, 100),
        "format": "svg",
        "artifact_paths": (Path("capture.svg"),),
        "provenance": _capture_provenance(0.0),
        "output_size": (100, 100),
    }
    values.update(kwargs)

    with pytest.raises(ValueError, match=message):
        CaptureManifest(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"t": True},
        {"canvas_size": [100, 100]},
        {"format": 1},
        {"artifact_paths": ("capture.svg",)},
        {"output_size": (100.0, 100)},
    ],
)
def test_capture_manifest_rejects_implicit_field_conversion(
    kwargs: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "t": 0.0,
        "canvas_size": (100, 100),
        "format": "svg",
        "artifact_paths": (Path("capture.svg"),),
        "provenance": _capture_provenance(0.0),
        "output_size": (100, 100),
    }
    values.update(kwargs)

    with pytest.raises(TypeError):
        CaptureManifest(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"fps": "30"},
        {"frame_count": 1.0},
        {"error_count": True},
        {"error_policy": 1},
    ],
)
def test_recording_manifest_rejects_implicit_field_conversion(
    kwargs: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "fps": 30.0,
        "frame_count": 1,
    }
    values.update(kwargs)

    with pytest.raises(TypeError):
        RecordingManifest(**values)  # type: ignore[arg-type]
