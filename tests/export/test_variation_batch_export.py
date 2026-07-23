from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from grafix.export import variation_batch as variation_batch_module
from grafix import VariationBatchResult, VariationRenderResult
from grafix.export.variation_batch import (
    VariationBatchArtifacts,
    export_variation_batch,
    portable_filename_component,
)


def _artifact_callback(
    workspaces: list[Path],
    *,
    variation_name: str = 'A & "quoted"',
) -> Callable[[Path], VariationBatchArtifacts]:
    def render_items(workspace: Path) -> VariationBatchArtifacts:
        workspaces.append(workspace)
        thumbnail = workspace / "001_thumbnail.svg"
        thumbnail.write_text("<svg/>", encoding="utf-8")
        manifest = workspace / "001_thumbnail.svg.capture.json"
        manifest.write_text(
            json.dumps(
                {
                    "output": {
                        "artifact_paths": [str(thumbnail)],
                    }
                }
            ),
            encoding="utf-8",
        )
        return VariationBatchArtifacts(
            items=(
                VariationRenderResult(
                    variation_name=variation_name,
                    seed=7,
                    t=1.25,
                    status="success",
                    thumbnail_path=thumbnail,
                    manifest_path=manifest,
                ),
            )
        )

    return render_items


def _export(
    tmp_path: Path,
    render_items: Callable[[Path], VariationBatchArtifacts],
    *,
    overwrite: bool = False,
) -> VariationBatchResult:
    return export_variation_batch(
        tmp_path,
        batch_name="variations",
        overwrite=overwrite,
        thumbnail_size=(160, 120),
        columns=None,
        render_items=render_items,
    )


def test_export_transaction_retries_late_collision_and_relocates_codecs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspaces: list[Path] = []
    real_rename = variation_batch_module._atomic_rename_no_clobber
    collided_path: Path | None = None

    def collide_once(source: Path, destination: Path) -> None:
        nonlocal collided_path
        target = Path(destination)
        if collided_path is None:
            collided_path = target
            target.mkdir()
            (target / "external.txt").write_text("late collision", encoding="utf-8")
            real_rename(source, destination)
            return
        real_rename(source, destination)

    monkeypatch.setattr(
        variation_batch_module,
        "_atomic_rename_no_clobber",
        collide_once,
    )

    result = _export(tmp_path, _artifact_callback(workspaces))

    assert collided_path == tmp_path / "variations"
    assert (collided_path / "external.txt").read_text(encoding="utf-8") == (
        "late collision"
    )
    assert result.output_directory == tmp_path / "variations_001"
    assert result.items[0].thumbnail_path == (
        result.output_directory / "001_thumbnail.svg"
    )
    assert workspaces and not workspaces[0].exists()

    manifest_path = result.items[0].manifest_path
    assert manifest_path is not None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["output"]["artifact_paths"] == [
        str(result.items[0].thumbnail_path)
    ]
    assert ".variations.batch-" not in manifest_path.read_text(encoding="utf-8")

    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert summary["contact_sheet_path"] == "contact-sheet.svg"
    assert summary["summary_path"] == "summary.json"
    assert summary["items"][0]["thumbnail_path"] == "001_thumbnail.svg"
    assert summary["items"][0]["manifest_path"] == (
        "001_thumbnail.svg.capture.json"
    )
    sheet = result.contact_sheet_path.read_text(encoding="utf-8")
    assert "A &amp; &quot;quoted&quot;" in sheet


@pytest.mark.parametrize("collision_kind", ("empty_directory", "file", "symlink"))
def test_export_transaction_does_not_replace_late_collision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    collision_kind: str,
) -> None:
    workspaces: list[Path] = []
    real_rename = variation_batch_module._atomic_rename_no_clobber
    collided_path: Path | None = None

    def collide_once(source: Path, destination: Path) -> None:
        nonlocal collided_path
        if collided_path is None:
            collided_path = destination
            if collision_kind == "empty_directory":
                destination.mkdir()
            elif collision_kind == "file":
                destination.write_text("late collision", encoding="utf-8")
            else:
                destination.symlink_to(tmp_path / "missing-generation")
        real_rename(source, destination)

    monkeypatch.setattr(
        variation_batch_module,
        "_atomic_rename_no_clobber",
        collide_once,
    )

    result = _export(tmp_path, _artifact_callback(workspaces))

    assert collided_path == tmp_path / "variations"
    assert os.path.lexists(collided_path)
    if collision_kind == "empty_directory":
        assert collided_path.is_dir()
        assert list(collided_path.iterdir()) == []
    elif collision_kind == "file":
        assert collided_path.read_text(encoding="utf-8") == "late collision"
    else:
        assert collided_path.is_symlink()
        assert collided_path.readlink() == tmp_path / "missing-generation"
    assert result.output_directory == tmp_path / "variations_001"
    assert result.summary_path.exists()
    assert workspaces and not workspaces[0].exists()


def test_overwrite_publish_failure_restores_previous_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous = tmp_path / "variations"
    previous.mkdir()
    sentinel = previous / "keep.txt"
    sentinel.write_text("previous", encoding="utf-8")
    workspaces: list[Path] = []
    real_replace = os.replace

    def fail_staging_publish(source: str | Path, destination: str | Path) -> None:
        source_path = Path(source)
        if source_path.parent == tmp_path and source_path.name.startswith(
            ".variations.batch-"
        ):
            raise OSError("publish failed")
        real_replace(source, destination)

    monkeypatch.setattr(
        variation_batch_module.os,
        "replace",
        fail_staging_publish,
    )

    with pytest.raises(OSError, match="publish failed"):
        _export(
            tmp_path,
            _artifact_callback(workspaces),
            overwrite=True,
        )

    assert sentinel.read_text(encoding="utf-8") == "previous"
    assert workspaces and not workspaces[0].exists()
    assert list(tmp_path.glob(".variations.batch-*")) == []
    assert list(tmp_path.glob(".variations.backup-*")) == []


def test_successful_publish_ignores_private_backup_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous = tmp_path / "variations"
    previous.mkdir()
    (previous / "old.txt").write_text("old", encoding="utf-8")
    workspaces: list[Path] = []
    real_rmtree = shutil.rmtree

    def fail_backup_cleanup(path: str | Path, *args: object, **kwargs: object) -> None:
        if ".variations.backup-" in Path(path).name:
            raise OSError("cleanup failed")
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(
        variation_batch_module.shutil,
        "rmtree",
        fail_backup_cleanup,
    )

    result = _export(
        tmp_path,
        _artifact_callback(workspaces),
        overwrite=True,
    )

    assert result.output_directory == previous
    assert result.summary_path.exists()
    assert not (previous / "old.txt").exists()


def test_callback_failure_discards_private_staging(tmp_path: Path) -> None:
    workspaces: list[Path] = []

    def fail(workspace: Path) -> VariationBatchArtifacts:
        workspaces.append(workspace)
        (workspace / "partial.txt").write_text("partial", encoding="utf-8")
        raise RuntimeError("render failed")

    with pytest.raises(RuntimeError, match="render failed"):
        _export(tmp_path, fail)

    assert workspaces and not workspaces[0].exists()
    assert list(tmp_path.iterdir()) == []


def test_public_result_types_are_defined_by_export_layer() -> None:
    assert VariationBatchResult is variation_batch_module.VariationBatchResult
    assert VariationRenderResult is variation_batch_module.VariationRenderResult
    assert VariationBatchResult.__module__ == "grafix.export.variation_batch"
    assert VariationRenderResult.__module__ == "grafix.export.variation_batch"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("A / B", "A_B"),
        ("日本語 variation", "日本語_variation"),
        (" /... ", "variation"),
        ("a" * 80, "a" * 64),
    ],
)
def test_portable_filename_component_has_one_stable_policy(
    name: str,
    expected: str,
) -> None:
    assert portable_filename_component(name) == expected
