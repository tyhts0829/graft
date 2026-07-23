from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

import pytest

from grafix.core.export_format import ExportFormat
from grafix.core.export_result import ExportResult
from grafix.export.capture_publish import capture_manifest_path_for
import grafix.interactive.runtime.variation_thumbnail_capture as capture_module
from grafix.interactive.runtime.variation_thumbnail_capture import (
    make_variation_thumbnail_capture,
    variation_thumbnail_output_path,
    variation_thumbnail_size,
)


class _CaptureService:
    def __init__(self, *, published_paths: tuple[Path, ...]) -> None:
        self.published_paths = iter(published_paths)
        self.calls: list[tuple[object, Path, bool, tuple[int, int] | None]] = []

    def export(
        self,
        frame: object,
        path: str | Path,
        *,
        overwrite: bool,
        output_size: tuple[int, int] | None,
    ) -> ExportResult:
        self.calls.append((frame, Path(path), overwrite, output_size))
        published_path = next(self.published_paths)
        published_path.write_bytes(b"png")
        manifest_path = capture_manifest_path_for(published_path)
        manifest_path.write_text("{}\n", encoding="utf-8")
        return ExportResult(
            path=published_path,
            format=ExportFormat.PNG,
            manifest_path=manifest_path,
        )


def test_thumbnail_path_and_size_share_export_filename_policy(tmp_path: Path) -> None:
    base = tmp_path / "sketch_800x400.png"

    assert variation_thumbnail_output_path(base, "  A/B candidate  ") == (
        tmp_path / "sketch_800x400_A_B_candidate.png"
    )
    assert variation_thumbnail_output_path(base, " /// ") == (
        tmp_path / "sketch_800x400_variation.png"
    )
    assert variation_thumbnail_size((800, 400)) == (320, 160)


def test_thumbnail_capture_reads_live_frame_and_returns_published_path(
    tmp_path: Path,
) -> None:
    first_frame = object()
    second_frame = object()
    frames = iter((first_frame, second_frame))
    first_path = tmp_path / "piece_candidate_001.png"
    second_path = tmp_path / "piece_candidate_002.png"
    service = _CaptureService(published_paths=(first_path, second_path))
    capture = make_variation_thumbnail_capture(
        cast(Any, service),
        frame_provider=lambda: cast(Any, next(frames)),
        base_path=tmp_path / "piece.png",
        canvas_size=(300, 200),
    )

    first = capture("candidate")
    second = capture("candidate")

    assert first.path == first_path
    assert second.path == second_path
    assert service.calls == [
        (first_frame, tmp_path / "piece_candidate.png", False, (320, 213)),
        (second_frame, tmp_path / "piece_candidate.png", False, (320, 213)),
    ]
    assert first_path.exists()
    assert capture_manifest_path_for(first_path).exists()
    first.discard()
    assert not first_path.exists()
    assert not capture_manifest_path_for(first_path).exists()
    assert second_path.exists()
    assert capture_manifest_path_for(second_path).exists()


def test_thumbnail_capture_rejects_missing_live_frame(tmp_path: Path) -> None:
    service = _CaptureService(published_paths=(tmp_path / "unused.png",))
    capture = make_variation_thumbnail_capture(
        cast(Any, service),
        frame_provider=lambda: None,
        base_path=tmp_path / "piece.png",
        canvas_size=(100, 100),
    )

    with pytest.raises(RuntimeError, match="No rendered frame"):
        capture("candidate")

    assert service.calls == []


def test_discard_preserves_externally_replaced_same_name_file(
    tmp_path: Path,
) -> None:
    published_path = tmp_path / "piece_candidate.png"
    manifest_path = capture_manifest_path_for(published_path)
    service = _CaptureService(published_paths=(published_path,))
    capture = make_variation_thumbnail_capture(
        cast(Any, service),
        frame_provider=lambda: cast(Any, object()),
        base_path=tmp_path / "piece.png",
        canvas_size=(100, 100),
    )
    artifact = capture("candidate")

    replacement = tmp_path / "replacement.png"
    replacement.write_bytes(b"external")
    os.replace(replacement, published_path)
    artifact.discard()

    assert published_path.read_bytes() == b"external"
    assert not manifest_path.exists()


def test_discard_preserves_externally_replaced_manifest(
    tmp_path: Path,
) -> None:
    published_path = tmp_path / "piece_candidate.png"
    manifest_path = capture_manifest_path_for(published_path)
    service = _CaptureService(published_paths=(published_path,))
    artifact = make_variation_thumbnail_capture(
        cast(Any, service),
        frame_provider=lambda: cast(Any, object()),
        base_path=tmp_path / "piece.png",
        canvas_size=(100, 100),
    )("candidate")

    replacement = tmp_path / "replacement.capture.json"
    replacement.write_text("external\n", encoding="utf-8")
    os.replace(replacement, manifest_path)
    artifact.discard()

    assert not published_path.exists()
    assert manifest_path.read_text(encoding="utf-8") == "external\n"


def test_discard_attempts_manifest_after_png_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published_path = tmp_path / "piece_candidate.png"
    manifest_path = capture_manifest_path_for(published_path)
    service = _CaptureService(published_paths=(published_path,))
    artifact = make_variation_thumbnail_capture(
        cast(Any, service),
        frame_provider=lambda: cast(Any, object()),
        base_path=tmp_path / "piece.png",
        canvas_size=(100, 100),
    )("candidate")
    original = capture_module._unlink_if_identity

    def fail_png(path: Path, identity: tuple[int, int]) -> None:
        if path == published_path:
            raise OSError("png cleanup unavailable")
        original(path, identity)

    monkeypatch.setattr(capture_module, "_unlink_if_identity", fail_png)

    with pytest.raises(OSError, match="png cleanup unavailable"):
        artifact.discard()

    assert published_path.exists()
    assert not manifest_path.exists()


def test_discard_reports_secondary_manifest_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published_path = tmp_path / "piece_candidate.png"
    manifest_path = capture_manifest_path_for(published_path)
    service = _CaptureService(published_paths=(published_path,))
    artifact = make_variation_thumbnail_capture(
        cast(Any, service),
        frame_provider=lambda: cast(Any, object()),
        base_path=tmp_path / "piece.png",
        canvas_size=(100, 100),
    )("candidate")

    def fail_both(path: Path, _identity: tuple[int, int]) -> None:
        if path == published_path:
            raise OSError("png cleanup unavailable")
        raise PermissionError("manifest cleanup unavailable")

    monkeypatch.setattr(capture_module, "_unlink_if_identity", fail_both)

    with pytest.raises(OSError, match="png cleanup unavailable") as raised:
        artifact.discard()

    assert getattr(raised.value, "__notes__", ()) == [
        "Secondary cleanup failure (discard variation thumbnail manifest): "
        "PermissionError: manifest cleanup unavailable"
    ]
    assert published_path.exists()
    assert manifest_path.exists()


def test_same_thumbnail_and_manifest_path_is_rolled_back_once(
    tmp_path: Path,
) -> None:
    shared_path = tmp_path / "piece_candidate.png"
    shared_path.write_bytes(b"published")
    result = ExportResult(
        path=shared_path,
        format=ExportFormat.PNG,
        manifest_path=shared_path,
    )

    with pytest.raises(ValueError, match="must be distinct"):
        capture_module._owned_thumbnail(result)

    assert not shared_path.exists()


def test_same_path_identity_failure_preserves_primary_and_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_path = tmp_path / "piece_candidate.png"
    shared_path.write_bytes(b"published")
    result = ExportResult(
        path=shared_path,
        format=ExportFormat.PNG,
        manifest_path=shared_path,
    )

    def fail_identity(_path: Path) -> tuple[int, int]:
        raise OSError("identity unavailable")

    monkeypatch.setattr(
        capture_module,
        "_regular_file_identity",
        fail_identity,
    )

    with pytest.raises(ValueError, match="must be distinct") as raised:
        capture_module._owned_thumbnail(result)

    assert getattr(raised.value, "__notes__", ()) == [
        "Secondary cleanup failure (acquire same-path thumbnail identity): "
        "OSError: identity unavailable"
    ]
    assert shared_path.exists()


def test_same_path_cleanup_failure_is_secondary_to_contract_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_path = tmp_path / "piece_candidate.png"
    shared_path.write_bytes(b"published")
    result = ExportResult(
        path=shared_path,
        format=ExportFormat.PNG,
        manifest_path=shared_path,
    )

    def fail_cleanup(
        _path: Path,
        _identity: tuple[int, int],
    ) -> None:
        raise PermissionError("cleanup unavailable")

    monkeypatch.setattr(
        capture_module,
        "_unlink_if_identity",
        fail_cleanup,
    )

    with pytest.raises(ValueError, match="must be distinct") as raised:
        capture_module._owned_thumbnail(result)

    assert getattr(raised.value, "__notes__", ()) == [
        "Secondary cleanup failure (rollback same-path thumbnail artifact): "
        "PermissionError: cleanup unavailable"
    ]
    assert shared_path.exists()


def test_png_identity_failure_still_owns_and_rolls_back_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published_path = tmp_path / "piece_candidate.png"
    manifest_path = capture_manifest_path_for(published_path)
    service = _CaptureService(published_paths=(published_path,))
    original_identity = capture_module._regular_file_identity
    inspected: list[Path] = []

    def fail_png_identity(path: Path) -> tuple[int, int]:
        inspected.append(path)
        if path == published_path:
            raise OSError("PNG identity unavailable")
        return original_identity(path)

    monkeypatch.setattr(
        capture_module,
        "_regular_file_identity",
        fail_png_identity,
    )
    capture = make_variation_thumbnail_capture(
        cast(Any, service),
        frame_provider=lambda: cast(Any, object()),
        base_path=tmp_path / "piece.png",
        canvas_size=(100, 100),
    )

    with pytest.raises(OSError, match="PNG identity unavailable"):
        capture("candidate")

    assert inspected == [published_path, manifest_path]
    assert published_path.exists()
    assert not manifest_path.exists()


def test_manifest_identity_failure_rolls_back_owned_png(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published_path = tmp_path / "piece_candidate.png"
    manifest_path = capture_manifest_path_for(published_path)
    service = _CaptureService(published_paths=(published_path,))
    original_identity = capture_module._regular_file_identity

    def fail_manifest_identity(path: Path) -> tuple[int, int]:
        if path == manifest_path:
            raise OSError("manifest identity unavailable")
        return original_identity(path)

    monkeypatch.setattr(
        capture_module,
        "_regular_file_identity",
        fail_manifest_identity,
    )
    capture = make_variation_thumbnail_capture(
        cast(Any, service),
        frame_provider=lambda: cast(Any, object()),
        base_path=tmp_path / "piece.png",
        canvas_size=(100, 100),
    )

    with pytest.raises(OSError, match="manifest identity unavailable"):
        capture("candidate")

    assert not published_path.exists()
    assert manifest_path.exists()


def test_identity_failure_reports_secondary_owned_member_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published_path = tmp_path / "piece_candidate.png"
    manifest_path = capture_manifest_path_for(published_path)
    service = _CaptureService(published_paths=(published_path,))
    original_identity = capture_module._regular_file_identity

    def fail_png_identity(path: Path) -> tuple[int, int]:
        if path == published_path:
            raise OSError("PNG identity unavailable")
        return original_identity(path)

    def fail_manifest_cleanup(
        path: Path,
        _identity: tuple[int, int],
    ) -> None:
        assert path == manifest_path
        raise PermissionError("manifest cleanup unavailable")

    monkeypatch.setattr(
        capture_module,
        "_regular_file_identity",
        fail_png_identity,
    )
    monkeypatch.setattr(
        capture_module,
        "_unlink_if_identity",
        fail_manifest_cleanup,
    )
    capture = make_variation_thumbnail_capture(
        cast(Any, service),
        frame_provider=lambda: cast(Any, object()),
        base_path=tmp_path / "piece.png",
        canvas_size=(100, 100),
    )

    with pytest.raises(OSError, match="PNG identity unavailable") as raised:
        capture("candidate")

    assert getattr(raised.value, "__notes__", ()) == [
        "Secondary cleanup failure "
        "(rollback thumbnail manifest after identity acquisition failure): "
        "PermissionError: manifest cleanup unavailable"
    ]
    assert published_path.exists()
    assert manifest_path.exists()


def test_owner_construction_failure_rolls_back_png_and_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published_path = tmp_path / "piece_candidate.png"
    manifest_path = capture_manifest_path_for(published_path)
    service = _CaptureService(published_paths=(published_path,))

    def fail_owner(**_kwargs: object) -> None:
        raise RuntimeError("owner unavailable")

    monkeypatch.setattr(capture_module, "_new_owned_thumbnail", fail_owner)
    capture = make_variation_thumbnail_capture(
        cast(Any, service),
        frame_provider=lambda: cast(Any, object()),
        base_path=tmp_path / "piece.png",
        canvas_size=(100, 100),
    )

    with pytest.raises(RuntimeError, match="owner unavailable"):
        capture("candidate")

    assert not published_path.exists()
    assert not manifest_path.exists()


def test_owner_failure_keeps_primary_error_and_reports_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published_path = tmp_path / "piece_candidate.png"
    manifest_path = capture_manifest_path_for(published_path)
    service = _CaptureService(published_paths=(published_path,))
    original_unlink = capture_module._unlink_if_identity

    def fail_owner(**_kwargs: object) -> None:
        raise RuntimeError("owner unavailable")

    def fail_png(path: Path, identity: tuple[int, int]) -> None:
        if path == published_path:
            raise OSError("png cleanup unavailable")
        original_unlink(path, identity)

    monkeypatch.setattr(capture_module, "_new_owned_thumbnail", fail_owner)
    monkeypatch.setattr(capture_module, "_unlink_if_identity", fail_png)
    capture = make_variation_thumbnail_capture(
        cast(Any, service),
        frame_provider=lambda: cast(Any, object()),
        base_path=tmp_path / "piece.png",
        canvas_size=(100, 100),
    )

    with pytest.raises(RuntimeError, match="owner unavailable") as raised:
        capture("candidate")

    assert getattr(raised.value, "__notes__", ()) == [
        "Secondary cleanup failure "
        "(rollback thumbnail PNG after owner construction failure): "
        "OSError: png cleanup unavailable"
    ]
    assert published_path.exists()
    assert not manifest_path.exists()
