from __future__ import annotations

import os
import importlib
from pathlib import Path

import pytest

from grafix.core.evaluation_config import EvaluationConfig
from grafix.core.font_resolver import default_font_path
from grafix.core.font_resources import (
    FontAssetFingerprint,
    FontResources,
    ResolvedFontLease,
    TextRenderer,
)
from grafix.runtime_config_loader import load_runtime_config


def _config_with_font_dirs(tmp_path: Path, *font_dirs: Path):
    cfg_path = tmp_path / f"config-{len(tuple(tmp_path.iterdir()))}.yaml"
    rows = ["version: 1", "paths:", '  output_dir: "data/output"', "  font_dirs:"]
    rows.extend(f'    - "{directory}"' for directory in font_dirs)
    cfg_path.write_text("\n".join((*rows, "")), encoding="utf-8")
    config = load_runtime_config(cfg_path)
    return EvaluationConfig(font_dirs=config.font_dirs)


def test_font_asset_fingerprint_contains_path_face_stat_and_content_digest(
    tmp_path: Path,
) -> None:
    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    font_path = font_dir / "asset.ttf"
    font_path.write_bytes(b"font bytes")
    config = _config_with_font_dirs(tmp_path, font_dir)

    with FontResources() as resources:
        face_zero = resources.resolve("asset.ttf", 0, config=config)
        face_one = resources.resolve("asset.ttf", 1, config=config)

    fingerprint = face_zero.fingerprint
    assert isinstance(fingerprint, FontAssetFingerprint)
    assert fingerprint.canonical_path == font_path.resolve().as_posix()
    assert fingerprint.face_index == 0
    assert fingerprint.stat.size == len(b"font bytes")
    assert len(fingerprint.content_digest) == 64
    assert fingerprint != face_one.fingerprint
    assert fingerprint.content_digest == face_one.fingerprint.content_digest
    assert fingerprint.canonical_value() != face_one.fingerprint.canonical_value()


def test_same_font_name_in_two_fixed_configs_has_distinct_identity(tmp_path: Path) -> None:
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    (dir_a / "Shared.ttf").write_bytes(b"font-a")
    (dir_b / "Shared.ttf").write_bytes(b"font-b")
    config_a = _config_with_font_dirs(tmp_path, dir_a)
    config_b = _config_with_font_dirs(tmp_path, dir_b)

    with FontResources() as resources:
        lease_a = resources.resolve("Shared.ttf", 0, config=config_a)
        lease_b = resources.resolve("Shared.ttf", 0, config=config_b)

    assert lease_a.fingerprint != lease_b.fingerprint
    assert lease_a.data == b"font-a"
    assert lease_b.data == b"font-b"


def test_unchanged_asset_is_warm_and_replacement_gets_new_lease(
    tmp_path: Path,
) -> None:
    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    font_path = font_dir / "Mutable.ttf"
    font_path.write_bytes(b"first")
    config = _config_with_font_dirs(tmp_path, font_dir)

    with FontResources() as resources:
        first = resources.resolve("Mutable.ttf", 0, config=config)
        warm = resources.resolve("Mutable.ttf", 0, config=config)
        replacement = font_dir / "replacement.tmp"
        replacement.write_bytes(b"other")
        os.replace(replacement, font_path)
        second = resources.resolve("Mutable.ttf", 0, config=config)

    assert warm is first
    assert second is not first
    assert second.fingerprint != first.fingerprint
    assert second.data == b"other"


def test_asset_lru_evicts_without_turning_capacity_into_user_error(
    tmp_path: Path,
) -> None:
    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    for name in ("a.ttf", "b.ttf", "c.ttf"):
        (font_dir / name).write_bytes(name.encode("ascii"))
    config = _config_with_font_dirs(tmp_path, font_dir)

    with FontResources(max_assets=2, max_asset_bytes=1024) as resources:
        first_a = resources.resolve("a.ttf", 0, config=config)
        resources.resolve("b.ttf", 0, config=config)
        assert resources.resolve("a.ttf", 0, config=config) is first_a
        resources.resolve("c.ttf", 0, config=config)
        second_b = resources.resolve("b.ttf", 0, config=config)
        stats = resources.stats()

    assert second_b.data == b"b.ttf"
    assert stats.assets <= 2
    assert stats.asset_bytes <= 1024


def test_text_renderer_closes_evicted_and_cleared_fonts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fontTools.ttLib import TTFont  # type: ignore[import-untyped]

    bundled = default_font_path()
    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    first_path = font_dir / "first.ttf"
    second_path = font_dir / "second.ttf"
    payload = bundled.read_bytes()
    first_path.write_bytes(payload)
    second_path.write_bytes(payload)
    config = _config_with_font_dirs(tmp_path, font_dir)
    closed: list[object] = []
    original_close = TTFont.close

    def recording_close(self) -> None:
        closed.append(self)
        original_close(self)

    monkeypatch.setattr(TTFont, "close", recording_close)
    with FontResources(max_fonts=1) as resources:
        first_lease = resources.resolve("first.ttf", 0, config=config)
        second_lease = resources.resolve("second.ttf", 0, config=config)
        first_font = resources.renderer.get_font(first_lease)
        second_font = resources.renderer.get_font(second_lease)
        assert first_font in closed
        assert second_font not in closed
        resources.clear()
        assert second_font in closed
        assert resources.stats().fonts == 0


def test_text_renderer_closes_input_stream_when_font_construction_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    (font_dir / "broken.ttf").write_bytes(b"not a font")
    config = _config_with_font_dirs(tmp_path, font_dir)
    captured_streams: list[object] = []

    def fail(stream, **_kwargs):
        captured_streams.append(stream)
        raise ValueError("broken")

    monkeypatch.setattr("fontTools.ttLib.TTFont", fail)
    with FontResources() as resources:
        lease = resources.resolve("broken.ttf", 0, config=config)
        with pytest.raises(ValueError, match="broken"):
            resources.renderer.get_font(lease)
        assert resources.stats().fonts == 0

    assert len(captured_streams) == 1
    assert captured_streams[0].closed


def test_close_failure_still_releases_remaining_font_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fontTools.ttLib import TTFont  # type: ignore[import-untyped]

    bundled = default_font_path()
    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    payload = bundled.read_bytes()
    (font_dir / "first.ttf").write_bytes(payload)
    (font_dir / "second.ttf").write_bytes(payload)
    config = _config_with_font_dirs(tmp_path, font_dir)
    original_close = TTFont.close
    attempts: list[object] = []

    def fail_first_close(self) -> None:
        attempts.append(self)
        if len(attempts) == 1:
            raise RuntimeError("close failed")
        original_close(self)

    monkeypatch.setattr(TTFont, "close", fail_first_close)
    resources = FontResources(max_fonts=2)
    renderer = resources.renderer
    renderer.get_font(resources.resolve("first.ttf", 0, config=config))
    renderer.get_font(resources.resolve("second.ttf", 0, config=config))

    with pytest.raises(RuntimeError, match="close failed"):
        resources.close()

    assert len(attempts) == 2
    assert resources.closed
    assert renderer.closed
    assert resources.stats().assets == 0
    assert resources.stats().fonts == 0


def test_clear_close_are_idempotent_and_closed_resources_reject_use(
    tmp_path: Path,
) -> None:
    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    (font_dir / "asset.ttf").write_bytes(b"font")
    config = _config_with_font_dirs(tmp_path, font_dir)
    resources = FontResources()
    lease = resources.resolve("asset.ttf", 0, config=config)

    assert isinstance(lease, ResolvedFontLease)
    resources.clear()
    resources.clear()
    assert resources.stats().assets == 0
    resources.close()
    resources.close()
    assert resources.closed
    with pytest.raises(RuntimeError, match="close"):
        resources.resolve("asset.ttf", 0, config=config)
    with pytest.raises(RuntimeError, match="close"):
        resources.renderer


def test_text_renderer_is_an_ordinary_instance_not_a_singleton() -> None:
    first = TextRenderer()
    second = TextRenderer()
    try:
        assert first is not second
    finally:
        first.close()
        second.close()


def test_font_modules_have_no_process_global_resource_cache() -> None:
    resolver_module = importlib.import_module("grafix.core.font_resolver")
    text_module = importlib.import_module("grafix.core.primitives.text")

    assert "TEXT_RENDERER" not in vars(text_module)
    assert "_instance" not in vars(TextRenderer)
    assert "_FONT_FILES_CACHE" not in vars(resolver_module)
    assert "_FONT_CHOICES_CACHE" not in vars(resolver_module)
    assert "_PACKAGED_FONT_DIRS" not in vars(resolver_module)
