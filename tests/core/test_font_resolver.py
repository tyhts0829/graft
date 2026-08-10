from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import grafix.core.font_resolver as font_resolver_module
from grafix.core.evaluation_config import EvaluationConfig, bind_evaluation_config
from grafix.core.font_resolver import (
    DEFAULT_FONT_FILENAME,
    FontPathResolver,
    default_font_path,
    list_font_choices,
    resolve_font_path,
)
from grafix.runtime_config_loader import load_runtime_config


def _config_with_font_dirs(tmp_path: Path, *font_dirs: Path):
    cfg_path = tmp_path / f"config-{len(tuple(tmp_path.iterdir()))}.yaml"
    rows = ["version: 1", "paths:", '  output_dir: "data/output"', "  font_dirs:"]
    rows.extend(f'    - "{directory}"' for directory in font_dirs)
    cfg_path.write_text("\n".join((*rows, "")), encoding="utf-8")
    config = load_runtime_config(cfg_path)
    return EvaluationConfig(font_dirs=config.font_dirs)


def test_default_font_path_exists() -> None:
    path = default_font_path()
    assert isinstance(path, Path)
    assert path.is_file()
    assert path.name == DEFAULT_FONT_FILENAME


def test_list_font_choices_contains_default() -> None:
    choices = list_font_choices()
    assert any(value == DEFAULT_FONT_FILENAME for _stem, value, _is_ttc, _search_key in choices)


def test_resolve_font_path_respects_priority_explicit_path_over_config(tmp_path) -> None:
    bundled = default_font_path()

    # config font dir（同名のフォントを置く）
    font_dir = tmp_path / "fonts"
    font_dir.mkdir(parents=True, exist_ok=True)
    copied = font_dir / DEFAULT_FONT_FILENAME
    copied.write_bytes(bundled.read_bytes())

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "\n".join(
            [
                "version: 1",
                "paths:",
                '  output_dir: "data/output"',
                "  font_dirs:",
                f'    - "{font_dir}"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    runtime_config = load_runtime_config(cfg_path)
    with bind_evaluation_config(EvaluationConfig(font_dirs=runtime_config.font_dirs)):
        # 1) name 指定は config の font_dirs が優先される
        assert resolve_font_path(DEFAULT_FONT_FILENAME) == copied.resolve()

        # 2) 実在パスは config より優先される
        assert resolve_font_path(str(bundled)) == bundled.resolve()


def test_resolve_font_path_accepts_explicit_fixed_config(tmp_path: Path) -> None:
    bundled = default_font_path()
    font_a = tmp_path / "a"
    font_b = tmp_path / "b"
    font_a.mkdir()
    font_b.mkdir()
    path_a = font_a / "Shared.ttf"
    path_b = font_b / "Shared.ttf"
    path_a.write_bytes(bundled.read_bytes())
    path_b.write_bytes(bundled.read_bytes())
    config_a = _config_with_font_dirs(tmp_path, font_a)
    config_b = _config_with_font_dirs(tmp_path, font_b)

    assert resolve_font_path("Shared.ttf", config=config_a) == path_a.resolve()
    assert resolve_font_path("Shared.ttf", config=config_b) == path_b.resolve()


def test_partial_match_rechecks_search_dirs_on_every_lookup(tmp_path: Path) -> None:
    preferred = tmp_path / "preferred"
    fallback = tmp_path / "fallback"
    preferred.mkdir()
    fallback.mkdir()
    fallback_font = fallback / "DynamicFallback.ttf"
    preferred_font = preferred / "DynamicPreferred.ttf"
    fallback_font.write_bytes(b"fallback")
    config = _config_with_font_dirs(tmp_path, preferred, fallback)

    assert resolve_font_path("Dynamic", config=config) == fallback_font.resolve()

    preferred_font.write_bytes(b"preferred")
    assert resolve_font_path("Dynamic", config=config) == preferred_font.resolve()

    preferred_font.unlink()
    assert resolve_font_path("Dynamic", config=config) == fallback_font.resolve()


def test_list_font_choices_observes_files_appearing_after_first_call(
    tmp_path: Path,
) -> None:
    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    config = _config_with_font_dirs(tmp_path, font_dir)

    before = list_font_choices(config=config)
    added = font_dir / "AppearedLater.ttf"
    added.write_bytes(b"font")
    after = list_font_choices(config=config)

    assert all(value != added.name for _stem, value, _ttc, _key in before)
    assert any(value == added.name for _stem, value, _ttc, _key in after)


def test_nested_choice_uses_relative_value_and_resolves_without_tree_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    font_dir = tmp_path / "fonts"
    nested_dir = font_dir / "Supplemental"
    nested_dir.mkdir(parents=True)
    nested_font = nested_dir / "Kannada MN.ttc"
    nested_font.write_bytes(b"font")
    root_font = font_dir / "Root.ttf"
    root_font.write_bytes(b"root")
    config = _config_with_font_dirs(tmp_path, font_dir)

    choices = list_font_choices(config=config)
    by_value = {value: (stem, is_ttc, key) for stem, value, is_ttc, key in choices}

    assert by_value["Root.ttf"] == ("Root", False, "root.ttf root")
    assert by_value["Supplemental/Kannada MN.ttc"][:2] == ("Kannada MN", True)
    assert "supplemental" in by_value["Supplemental/Kannada MN.ttc"][2]

    def unexpected_scan(*, dirs):
        raise AssertionError(f"relative value must use direct lookup: {dirs}")

    monkeypatch.setattr(font_resolver_module, "_scan_font_tree", unexpected_scan)
    resolver = FontPathResolver()
    assert resolver.resolve("Supplemental/Kannada MN.ttc", config=config) == nested_font.resolve()


def test_choices_keep_distinct_relative_paths_and_shadow_same_relative_value(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    for root in (first_root, second_root):
        (root / "one").mkdir(parents=True)
    (first_root / "two").mkdir()
    (first_root / "one" / "Shared.ttf").write_bytes(b"first-one")
    (first_root / "two" / "Shared.ttf").write_bytes(b"first-two")
    (second_root / "one" / "Shared.ttf").write_bytes(b"shadowed")
    config = _config_with_font_dirs(tmp_path, first_root, second_root)

    choices = list_font_choices(config=config)
    shared = {
        value: search_key
        for _stem, value, _is_ttc, search_key in choices
        if value.endswith("Shared.ttf")
    }

    assert set(shared) == {"one/Shared.ttf", "two/Shared.ttf"}
    assert "one/shared.ttf" in shared["one/Shared.ttf"]
    assert "two/shared.ttf" in shared["two/Shared.ttf"]


def test_choice_provenance_uses_first_root_for_overlapping_and_symlinked_paths(
    tmp_path: Path,
) -> None:
    outer = tmp_path / "fonts"
    nested = outer / "nested"
    nested.mkdir(parents=True)
    original = nested / "Original.ttf"
    original.write_bytes(b"font")
    alias_dir = outer / "z-alias"
    alias_dir.mkdir()
    (alias_dir / "Alias.ttf").symlink_to(original)
    config = _config_with_font_dirs(tmp_path, outer, nested)

    values = {
        value
        for _stem, value, _is_ttc, _key in list_font_choices(config=config)
        if value.endswith(("Original.ttf", "Alias.ttf"))
    }

    assert values == {"nested/Original.ttf"}


def test_owner_resolver_warm_lookup_uses_directory_stat_without_reglob(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    font_dir = tmp_path / "fonts"
    nested_dir = font_dir / "empty" / "nested"
    nested_dir.mkdir(parents=True)
    font_path = nested_dir / "Warm.ttf"
    font_path.write_bytes(b"font")
    config = _config_with_font_dirs(tmp_path, font_dir)
    resolver = FontPathResolver()

    assert resolver.resolve("Warm.ttf", config=config) == font_path.resolve()

    def unexpected_glob(self, pattern):
        raise AssertionError(f"warm lookup must not glob: {self=}, {pattern=}")

    monkeypatch.setattr(Path, "glob", unexpected_glob)
    assert resolver.resolve("Warm.ttf", config=config) == font_path.resolve()


def test_owner_resolver_does_not_cache_unstable_tree_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    font_dir = tmp_path / "fonts"
    nested_dir = font_dir / "nested"
    nested_dir.mkdir(parents=True)
    font_path = nested_dir / "Unstable.ttf"
    font_path.write_bytes(b"font")
    config = _config_with_font_dirs(tmp_path, font_dir)
    original_scan = font_resolver_module._scan_font_tree
    scans = 0

    def first_scan_is_unstable(*, dirs):
        nonlocal scans
        scans += 1
        result = original_scan(dirs=dirs)
        if scans == 1:
            return font_resolver_module._FontTreeScan(
                snapshot=result.snapshot,
                stable=False,
            )
        return result

    monkeypatch.setattr(font_resolver_module, "_scan_font_tree", first_scan_is_unstable)
    resolver = FontPathResolver()

    assert resolver.resolve("Unstable.ttf", config=config) == font_path.resolve()
    assert resolver.resolve("Unstable.ttf", config=config) == font_path.resolve()
    assert resolver.resolve("Unstable.ttf", config=config) == font_path.resolve()
    assert scans == 2


def test_tree_scan_detects_membership_change_between_its_two_passes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    config = _config_with_font_dirs(tmp_path, font_dir)
    directories = font_resolver_module._search_dirs(config)
    original_enumerate = font_resolver_module._enumerate_tree
    enumerations = 0

    def changing_enumeration(*, dirs):
        nonlocal enumerations
        enumerations += 1
        result = original_enumerate(dirs=dirs)
        if enumerations == 1:
            (font_dir / "appeared-empty-directory").mkdir()
        return result

    monkeypatch.setattr(font_resolver_module, "_enumerate_tree", changing_enumeration)

    assert not font_resolver_module._scan_font_tree(dirs=directories).stable


def test_owner_resolver_supports_relative_config_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    font_dir = tmp_path / "relative-fonts"
    nested = font_dir / "nested"
    nested.mkdir(parents=True)
    font_path = nested / "Relative.ttf"
    font_path.write_bytes(b"font")
    monkeypatch.chdir(tmp_path)
    config = EvaluationConfig(font_dirs=(Path("relative-fonts"),))
    resolver = FontPathResolver()

    assert resolver.resolve("Relative.ttf", config=config) == font_path.resolve()
    assert resolver.resolve("Relative.ttf", config=config) == font_path.resolve()


def test_owner_resolver_serializes_concurrent_snapshot_builds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    font_dir = tmp_path / "fonts"
    nested = font_dir / "nested"
    nested.mkdir(parents=True)
    font_path = nested / "Concurrent.ttf"
    font_path.write_bytes(b"font")
    config = _config_with_font_dirs(tmp_path, font_dir)
    original_scan = font_resolver_module._scan_font_tree
    scans = 0

    def recording_scan(*, dirs):
        nonlocal scans
        scans += 1
        return original_scan(dirs=dirs)

    monkeypatch.setattr(font_resolver_module, "_scan_font_tree", recording_scan)
    resolver = FontPathResolver()
    with ThreadPoolExecutor(max_workers=8) as executor:
        paths = tuple(
            executor.map(
                lambda _index: resolver.resolve("Concurrent.ttf", config=config),
                range(60),
            )
        )

    assert paths == (font_path.resolve(),) * 60
    assert scans == 1


def test_owner_resolver_observes_external_symlink_target_disappearing(
    tmp_path: Path,
) -> None:
    preferred = tmp_path / "preferred"
    fallback = tmp_path / "fallback"
    external = tmp_path / "external"
    preferred.mkdir()
    fallback.mkdir()
    external.mkdir()
    target = external / "SwitchablePreferredTarget.ttf"
    target.write_bytes(b"preferred")
    link = preferred / "SwitchablePreferred.ttf"
    link.symlink_to(target)
    fallback_font = fallback / "SwitchableFallback.ttf"
    fallback_font.write_bytes(b"fallback")
    config = _config_with_font_dirs(tmp_path, preferred, fallback)
    resolver = FontPathResolver()

    assert resolver.resolve("Switchable", config=config) == target.resolve()

    target.unlink()
    assert resolver.resolve("Switchable", config=config) == fallback_font.resolve()


def test_owner_resolver_observes_dangling_symlink_target_appearing(
    tmp_path: Path,
) -> None:
    preferred = tmp_path / "preferred"
    fallback = tmp_path / "fallback"
    external = tmp_path / "external"
    preferred.mkdir()
    fallback.mkdir()
    external.mkdir()
    target = external / "SwitchableAppearedTarget.ttf"
    link = preferred / "SwitchablePreferred.ttf"
    link.symlink_to(target)
    fallback_font = fallback / "SwitchableFallback.ttf"
    fallback_font.write_bytes(b"fallback")
    config = _config_with_font_dirs(tmp_path, preferred, fallback)
    resolver = FontPathResolver()

    assert resolver.resolve("Switchable", config=config) == fallback_font.resolve()

    target.write_bytes(b"preferred")
    assert resolver.resolve("Switchable", config=config) == target.resolve()


def test_resolve_font_path_error_message_contains_hints() -> None:
    try:
        resolve_font_path("___no_such_font___")
    except FileNotFoundError as exc:
        msg = str(exc)
        assert "searched_dirs=" in msg
        assert "font_dirs:" in msg
    else:  # pragma: no cover
        raise AssertionError("resolve_font_path は FileNotFoundError を送出する必要がある")
