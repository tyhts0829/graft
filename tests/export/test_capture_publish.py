from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import pytest

from grafix.core.capture_manifest import CaptureManifest
from grafix.export.capture_publish import (
    _publish_capture_generation,
    capture_manifest_path_for,
)
from tests.capture_manifest_test_support import _capture_provenance


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
        _publish_capture_generation(
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
        _publish_capture_generation(
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
        _publish_capture_generation(
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

    published = _publish_capture_generation(
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

    owned = _publish_capture_generation(
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
    owned = _publish_capture_generation(
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
def test_owned_capture_generation_preserves_member_replaced_before_discard_check(
    tmp_path: Path,
    replaced_member: str,
) -> None:
    staged = tmp_path / ".staged.svg"
    staged.write_bytes(b"owned artifact")
    artifact = tmp_path / "capture.svg"
    manifest_path = capture_manifest_path_for(artifact)
    owned = _publish_capture_generation(
        staged_artifact_paths=(staged,),
        artifact_paths=(artifact,),
        manifest_path=manifest_path,
        manifest=_manifest_for((artifact,)),
    )
    replaced_path = artifact if replaced_member == "artifact" else manifest_path
    replacement = tmp_path / f"external-{replaced_member}"
    replacement.write_bytes(b"external")

    # external replacement は discard の identity check が始まる前に完了している。
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
    owned = _publish_capture_generation(
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
    owned = _publish_capture_generation(
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

    published = _publish_capture_generation(
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
        _publish_capture_generation(
            staged_artifact_paths=(staged,),
            artifact_paths=(artifact,),
            manifest_path=manifest_path,
            manifest=manifest,
            overwrite=True,
        )

    assert artifact.read_bytes() == b"old artifact"
    assert manifest_path.read_bytes() == b"old manifest"
    assert list(tmp_path.glob(".grafix-capture-backup-*")) == []


def test_capture_generation_rejects_non_bool_overwrite(
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
        _publish_capture_generation(
            staged_artifact_paths=(tmp_path / "staged.svg",),
            artifact_paths=(artifact,),
            manifest_path=tmp_path / "capture.svg.capture.json",
            manifest=manifest,
            overwrite="false",  # type: ignore[arg-type]
        )
