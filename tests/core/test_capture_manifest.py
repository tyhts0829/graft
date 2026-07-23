from __future__ import annotations

import json
import importlib
import os
from pathlib import Path

import pytest

from grafix.core.capture_manifest import (
    CAPTURE_MANIFEST_SCHEMA_VERSION,
    CaptureManifest,
    RecordingManifest,
)
from grafix.export.capture_publish import (
    capture_manifest_path_for,
    publish_capture_generation,
    write_capture_manifest,
)
from grafix.core.capture_provenance import (
    CaptureProvenance,
    ConfigProvenance,
    FrameProvenance,
    GitProvenance,
    ParameterSnapshotProvenance,
    SessionProvenance,
    SourceProvenance,
)


def _capture_provenance(t: float) -> CaptureProvenance:
    return CaptureProvenance(
        session=SessionProvenance(
            grafix_version="test",
            source=SourceProvenance(
                module=None,
                qualname=None,
                path=None,
                sha256=None,
                hash_scope=None,
                unavailable_reason="test fixture has no source file",
            ),
            git=GitProvenance(
                available=False,
                unavailable_reason="test fixture is repository independent",
            ),
            config=ConfigProvenance(
                path=None,
                effective_json="{}",
                sha256="config-sha256",
            ),
            parameter_source="test",
            parameter_store_path=None,
            parameter_load_provenance="primary",
            seed=None,
        ),
        frame=FrameProvenance(
            t=t,
            frame_index=None,
            quality="final",
            origin="headless",
            parameters=ParameterSnapshotProvenance(
                revision=0,
                entry_count=0,
                sha256="parameters-sha256",
            ),
        ),
    )


def _manifest_for(
    artifact_paths: tuple[Path, ...],
    *,
    format: str = "svg",
) -> CaptureManifest:
    """publish test 用の最小 capture manifest を返す。"""

    return CaptureManifest(
        t=1.5,
        canvas_size=(100, 80),
        format=format,
        artifact_paths=artifact_paths,
        provenance=_capture_provenance(1.5),
        output_size=(100, 80),
    )


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


def test_capture_manifest_supports_multiple_layer_artifacts(tmp_path: Path) -> None:
    artifacts = (
        tmp_path / "capture_layer001.gcode",
        tmp_path / "capture_layer002.gcode",
    )
    manifest = CaptureManifest(
        t=0.0,
        canvas_size=(210, 297),
        format="gcode",
        artifact_paths=artifacts,
        provenance=_capture_provenance(0.0),
        output_size=(210, 297),
    )
    path = tmp_path / "capture.gcode.capture.json"

    assert write_capture_manifest(path, manifest) == path
    assert json.loads(path.read_text(encoding="utf-8")) == manifest.as_dict()
    assert path.read_bytes().endswith(b"\n")


def test_capture_manifest_write_never_replaces_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "capture.svg.capture.json"
    path.write_text("previous\n", encoding="utf-8")
    manifest = CaptureManifest(
        t=2.0,
        canvas_size=(100, 100),
        format="svg",
        artifact_paths=(tmp_path / "capture.svg",),
        provenance=_capture_provenance(2.0),
        output_size=(100, 100),
    )

    with pytest.raises(FileExistsError):
        write_capture_manifest(path, manifest)

    assert path.read_text(encoding="utf-8") == "previous\n"
    assert list(tmp_path.iterdir()) == [path]


def test_capture_manifest_path_keeps_artifact_extension() -> None:
    assert capture_manifest_path_for(Path("output/capture_002.png")) == Path(
        "output/capture_002.png.capture.json"
    )


def test_capture_generation_rolls_back_artifact_when_manifest_late_collides(
    tmp_path: Path,
) -> None:
    staged = tmp_path / ".staged.svg"
    staged.write_bytes(b"new artifact")
    artifact = tmp_path / "capture.svg"
    manifest_path = capture_manifest_path_for(artifact)
    manifest_path.write_bytes(b"external manifest")
    manifest = CaptureManifest(
        t=1.5,
        canvas_size=(100, 80),
        format="svg",
        artifact_paths=(artifact,),
        provenance=_capture_provenance(1.5),
        output_size=(100, 80),
    )

    with pytest.raises(FileExistsError):
        publish_capture_generation(
            staged_artifact_paths=(staged,),
            artifact_paths=(artifact,),
            manifest_path=manifest_path,
            manifest=manifest,
        )

    assert not artifact.exists()
    assert manifest_path.read_bytes() == b"external manifest"
    assert staged.read_bytes() == b"new artifact"


def test_capture_generation_rejects_duplicate_target_before_publish(
    tmp_path: Path,
) -> None:
    staged = tmp_path / ".staged.svg"
    staged.write_bytes(b"artifact")
    target = tmp_path / "capture.svg"

    with pytest.raises(ValueError, match="一意"):
        publish_capture_generation(
            staged_artifact_paths=(staged,),
            artifact_paths=(target,),
            manifest_path=target,
            manifest=_manifest_for((target,)),
        )

    assert not target.exists()
    assert list(tmp_path.glob(".grafix-capture-manifest-*")) == []


def test_private_manifest_cleanup_failure_does_not_mask_publish_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staged = tmp_path / ".staged.svg"
    staged.write_bytes(b"new artifact")
    artifact = tmp_path / "capture.svg"
    manifest_path = capture_manifest_path_for(artifact)
    manifest_path.write_bytes(b"external manifest")
    real_unlink = Path.unlink

    def fail_private_manifest(
        path: Path,
        missing_ok: bool = False,
    ) -> None:
        if path.name.startswith(".grafix-capture-manifest-"):
            raise OSError("private cleanup unavailable")
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_private_manifest)

    with pytest.raises(FileExistsError):
        publish_capture_generation(
            staged_artifact_paths=(staged,),
            artifact_paths=(artifact,),
            manifest_path=manifest_path,
            manifest=_manifest_for((artifact,)),
        )

    assert not artifact.exists()
    assert manifest_path.read_bytes() == b"external manifest"


def test_capture_generation_publishes_all_artifacts_and_manifest(tmp_path: Path) -> None:
    staged = (tmp_path / ".layer1", tmp_path / ".layer2")
    for index, path in enumerate(staged, start=1):
        path.write_bytes(f"layer {index}".encode())
    artifacts = (
        tmp_path / "capture_layer001.gcode",
        tmp_path / "capture_layer002.gcode",
    )
    manifest_path = tmp_path / "capture.gcode.capture.json"
    manifest = CaptureManifest(
        t=2.25,
        canvas_size=(210, 297),
        format="gcode",
        artifact_paths=artifacts,
        provenance=_capture_provenance(2.25),
        output_size=(210, 297),
    )

    published = publish_capture_generation(
        staged_artifact_paths=staged,
        artifact_paths=artifacts,
        manifest_path=manifest_path,
        manifest=manifest,
    )

    assert published.artifact_paths == artifacts
    assert published.manifest_path == manifest_path
    assert [path.read_bytes() for path in artifacts] == [b"layer 1", b"layer 2"]
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == manifest.as_dict()


def test_private_manifest_cleanup_failure_does_not_change_publish_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staged = tmp_path / ".staged.svg"
    staged.write_bytes(b"artifact")
    artifact = tmp_path / "capture.svg"
    manifest_path = capture_manifest_path_for(artifact)
    real_unlink = Path.unlink

    def fail_private_manifest(
        path: Path,
        missing_ok: bool = False,
    ) -> None:
        if path.name.startswith(".grafix-capture-manifest-"):
            raise OSError("private cleanup unavailable")
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_private_manifest)

    owned = publish_capture_generation(
        staged_artifact_paths=(staged,),
        artifact_paths=(artifact,),
        manifest_path=manifest_path,
        manifest=_manifest_for((artifact,)),
    )

    assert owned.path == artifact
    assert artifact.read_bytes() == b"artifact"
    assert manifest_path.is_file()


def test_owned_capture_generation_discards_all_artifacts_and_manifest(
    tmp_path: Path,
) -> None:
    staged = (tmp_path / ".layer1", tmp_path / ".layer2")
    for index, path in enumerate(staged, start=1):
        path.write_bytes(f"layer {index}".encode())
    artifacts = (
        tmp_path / "capture_layer001.gcode",
        tmp_path / "capture_layer002.gcode",
    )
    manifest_path = tmp_path / "capture.gcode.capture.json"
    owned = publish_capture_generation(
        staged_artifact_paths=staged,
        artifact_paths=artifacts,
        manifest_path=manifest_path,
        manifest=_manifest_for(artifacts, format="gcode"),
    )

    assert owned.path == artifacts[0]
    owned.discard()

    assert all(not path.exists() for path in artifacts)
    assert not manifest_path.exists()
    assert all(path.exists() for path in staged)


@pytest.mark.parametrize("replaced_member", ["artifact", "manifest"])
def test_owned_capture_generation_preserves_externally_replaced_member(
    tmp_path: Path,
    replaced_member: str,
) -> None:
    staged = tmp_path / ".staged.svg"
    staged.write_bytes(b"owned artifact")
    artifact = tmp_path / "capture.svg"
    manifest_path = capture_manifest_path_for(artifact)
    owned = publish_capture_generation(
        staged_artifact_paths=(staged,),
        artifact_paths=(artifact,),
        manifest_path=manifest_path,
        manifest=_manifest_for((artifact,)),
    )
    replaced_path = artifact if replaced_member == "artifact" else manifest_path
    replacement = tmp_path / f"external-{replaced_member}"
    replacement.write_bytes(b"external")
    os.replace(replacement, replaced_path)

    owned.discard()

    assert replaced_path.read_bytes() == b"external"
    other_path = manifest_path if replaced_path == artifact else artifact
    assert not other_path.exists()


def test_owned_capture_generation_attempts_remaining_cleanup_after_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staged = tmp_path / ".staged.svg"
    staged.write_bytes(b"artifact")
    artifact = tmp_path / "capture.svg"
    manifest_path = capture_manifest_path_for(artifact)
    owned = publish_capture_generation(
        staged_artifact_paths=(staged,),
        artifact_paths=(artifact,),
        manifest_path=manifest_path,
        manifest=_manifest_for((artifact,)),
    )
    real_unlink = Path.unlink
    calls: list[Path] = []

    def fail_artifact(path: Path, missing_ok: bool = False) -> None:
        calls.append(path)
        if path == artifact:
            raise OSError("artifact cleanup unavailable")
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_artifact)

    with pytest.raises(OSError, match="artifact cleanup unavailable"):
        owned.discard()

    assert calls == [artifact, manifest_path]
    assert artifact.exists()
    assert not manifest_path.exists()


def test_owned_capture_generation_reports_secondary_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staged = tmp_path / ".staged.svg"
    staged.write_bytes(b"artifact")
    artifact = tmp_path / "capture.svg"
    manifest_path = capture_manifest_path_for(artifact)
    owned = publish_capture_generation(
        staged_artifact_paths=(staged,),
        artifact_paths=(artifact,),
        manifest_path=manifest_path,
        manifest=_manifest_for((artifact,)),
    )
    calls: list[Path] = []

    def fail_all(path: Path, _missing_ok: bool = False) -> None:
        calls.append(path)
        if path == artifact:
            raise OSError("artifact cleanup unavailable")
        raise PermissionError("manifest cleanup unavailable")

    monkeypatch.setattr(Path, "unlink", fail_all)

    with pytest.raises(OSError, match="artifact cleanup unavailable") as raised:
        owned.discard()

    assert calls == [artifact, manifest_path]
    assert getattr(raised.value, "__notes__", ()) == [
        "Secondary cleanup failure "
        f"(discard capture generation member {manifest_path}): "
        "PermissionError: manifest cleanup unavailable"
    ]


def test_capture_generation_overwrite_replaces_complete_generation(tmp_path: Path) -> None:
    staged = tmp_path / ".staged.svg"
    staged.write_bytes(b"new artifact")
    artifact = tmp_path / "capture.svg"
    artifact.write_bytes(b"old artifact")
    manifest_path = capture_manifest_path_for(artifact)
    manifest_path.write_bytes(b"old manifest")
    manifest = CaptureManifest(
        t=1.5,
        canvas_size=(100, 80),
        format="svg",
        artifact_paths=(artifact,),
        provenance=_capture_provenance(1.5),
        output_size=(100, 80),
    )

    published = publish_capture_generation(
        staged_artifact_paths=(staged,),
        artifact_paths=(artifact,),
        manifest_path=manifest_path,
        manifest=manifest,
        overwrite=True,
    )

    assert published.artifact_paths == (artifact,)
    assert artifact.read_bytes() == b"new artifact"
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == manifest.as_dict()
    assert list(tmp_path.glob(".grafix-capture-backup-*")) == []


def test_capture_generation_overwrite_rolls_back_both_files_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture_module = importlib.import_module("grafix.export.capture_publish")

    staged = tmp_path / ".staged.svg"
    staged.write_bytes(b"new artifact")
    artifact = tmp_path / "capture.svg"
    artifact.write_bytes(b"old artifact")
    manifest_path = capture_manifest_path_for(artifact)
    manifest_path.write_bytes(b"old manifest")
    manifest = CaptureManifest(
        t=1.5,
        canvas_size=(100, 80),
        format="svg",
        artifact_paths=(artifact,),
        provenance=_capture_provenance(1.5),
        output_size=(100, 80),
    )
    real_link = capture_module.os.link
    link_calls = 0

    def fail_manifest_link(source, target, *, follow_symlinks=True):
        nonlocal link_calls
        link_calls += 1
        if link_calls == 2:
            raise OSError("manifest publish failed")
        return real_link(source, target, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(capture_module.os, "link", fail_manifest_link)

    with pytest.raises(OSError, match="manifest publish failed"):
        publish_capture_generation(
            staged_artifact_paths=(staged,),
            artifact_paths=(artifact,),
            manifest_path=manifest_path,
            manifest=manifest,
            overwrite=True,
        )

    assert artifact.read_bytes() == b"old artifact"
    assert manifest_path.read_bytes() == b"old manifest"
    assert list(tmp_path.glob(".grafix-capture-backup-*")) == []


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


def test_publish_capture_generation_rejects_non_bool_overwrite(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "capture.svg"
    manifest = CaptureManifest(
        t=0.0,
        canvas_size=(100, 100),
        format="svg",
        artifact_paths=(artifact,),
        provenance=_capture_provenance(0.0),
        output_size=(100, 100),
    )

    with pytest.raises(TypeError, match="overwrite"):
        publish_capture_generation(
            staged_artifact_paths=(tmp_path / "staged.svg",),
            artifact_paths=(artifact,),
            manifest_path=tmp_path / "capture.svg.capture.json",
            manifest=manifest,
            overwrite="false",  # type: ignore[arg-type]
        )
