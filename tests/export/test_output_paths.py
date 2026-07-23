from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib
from pathlib import Path
from typing import Any, cast

import pytest

from grafix.export.output_paths import VersionedPathAllocator, gcode_layer_output_path
from grafix.runtime_config_loader import load_runtime_config

output_paths = importlib.import_module("grafix.export.output_paths")


def test_versioned_path_allocator_keeps_existing_and_reserved_paths(tmp_path: Path) -> None:
    base = tmp_path / "capture.png"
    numbered = tmp_path / "capture_001.png"
    base.write_bytes(b"previous-base")
    numbered.write_bytes(b"previous-001")

    allocator = VersionedPathAllocator()
    first = allocator.allocate(base)
    second = allocator.allocate(base)

    assert first == tmp_path / "capture_002.png"
    assert second == tmp_path / "capture_003.png"
    assert base.read_bytes() == b"previous-base"
    assert numbered.read_bytes() == b"previous-001"
    # 予約は export 完了前の衝突だけを防ぎ、偽の成果物は作らない。
    assert not first.exists()
    assert not second.exists()


def test_versioned_path_allocator_is_thread_safe_for_rapid_submit(tmp_path: Path) -> None:
    base = tmp_path / "capture.svg"
    allocator = VersionedPathAllocator()

    with ThreadPoolExecutor(max_workers=8) as executor:
        paths = list(executor.map(lambda _index: allocator.allocate(base), range(32)))

    assert len(set(paths)) == 32
    assert set(paths) == {
        base,
        *(tmp_path / f"capture_{index:03d}.svg" for index in range(1, 32)),
    }


def test_versioned_path_allocator_normalizes_relative_reservations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    allocator = VersionedPathAllocator()

    relative = allocator.allocate(Path("capture.gcode"))
    absolute = allocator.allocate(tmp_path / "capture.gcode")

    assert relative == Path("capture.gcode")
    assert absolute == tmp_path / "capture_001.gcode"


def test_versioned_path_allocator_validates_configuration() -> None:
    with pytest.raises(ValueError, match="minimum_digits"):
        VersionedPathAllocator(minimum_digits=0)
    for value in (True, 3.0, "3"):
        with pytest.raises(TypeError, match="minimum_digits"):
            VersionedPathAllocator(minimum_digits=cast(Any, value))

    allocator = VersionedPathAllocator()
    with pytest.raises(ValueError, match="ファイル名"):
        allocator.allocate(Path("."))
    with pytest.raises(TypeError, match="base_path"):
        allocator.allocate(cast(Any, object()))


def test_gcode_layer_output_path_without_name() -> None:
    base = Path("output/gcode/foo_800x600_v1.gcode")
    assert gcode_layer_output_path(base, layer_index=1, n_layers=12) == Path(
        "output/gcode/foo_800x600_v1_layer001.gcode"
    )
    assert gcode_layer_output_path(base, layer_index=12, n_layers=12) == Path(
        "output/gcode/foo_800x600_v1_layer012.gcode"
    )


def test_gcode_layer_output_path_with_name_sanitize_and_truncate() -> None:
    base = Path("output/gcode/foo.gcode")
    assert gcode_layer_output_path(
        base, layer_index=3, n_layers=3, layer_name="Layer A/B"
    ) == Path("output/gcode/foo_layer003_Layer_A_B.gcode")

    long_name = "a" * 100
    out = gcode_layer_output_path(base, layer_index=1, n_layers=1, layer_name=long_name)
    assert out.name == f"foo_layer001_{'a' * 32}.gcode"


def test_gcode_layer_output_path_name_can_be_omitted_after_sanitize() -> None:
    base = Path("output/gcode/foo.gcode")
    # ASCII 以外は `_` に潰れるため、最終的に空になり得る（その場合は name suffix を省略する）。
    assert gcode_layer_output_path(
        base, layer_index=1, n_layers=1, layer_name="日本語"
    ) == Path("output/gcode/foo_layer001.gcode")


def test_gcode_layer_output_path_index_validation() -> None:
    with pytest.raises(ValueError):
        gcode_layer_output_path(Path("x.gcode"), layer_index=0, n_layers=1)
    with pytest.raises(ValueError, match="n_layers"):
        gcode_layer_output_path(Path("x.gcode"), layer_index=1, n_layers=0)
    with pytest.raises(ValueError, match="n_layers"):
        gcode_layer_output_path(Path("x.gcode"), layer_index=2, n_layers=1)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    (
        ({"base_path": "x.gcode"}, "base_path"),
        ({"layer_index": True}, "layer_index"),
        ({"n_layers": "1"}, "n_layers"),
        ({"layer_name": 1}, "layer_name"),
        ({"max_layer_name_len": 1.0}, "max_layer_name_len"),
    ),
)
def test_gcode_layer_output_path_rejects_implicit_conversions(
    kwargs: dict[str, object],
    match: str,
) -> None:
    arguments: dict[str, object] = {
        "base_path": Path("x.gcode"),
        "layer_index": 1,
        "n_layers": 1,
    }
    arguments.update(kwargs)

    with pytest.raises(TypeError, match=match):
        gcode_layer_output_path(**cast(Any, arguments))


def test_gcode_layer_output_path_width_grows_for_large_layer_counts() -> None:
    base = Path("output/gcode/foo.gcode")
    assert gcode_layer_output_path(base, layer_index=1, n_layers=1000) == Path(
        "output/gcode/foo_layer0001.gcode"
    )


def test_draw_source_path_only_treats_unsupported_callable_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CallableWithoutSource:
        def __call__(self, t: float) -> None:
            return None

    monkeypatch.setattr(
        output_paths.inspect,
        "getsourcefile",
        lambda _draw: (_ for _ in ()).throw(TypeError("unsupported callable")),
    )
    monkeypatch.setattr(
        output_paths.inspect,
        "getfile",
        lambda _draw: (_ for _ in ()).throw(TypeError("unsupported callable")),
    )

    assert output_paths._draw_source_path(CallableWithoutSource()) is None


def test_draw_source_path_does_not_hide_unexpected_inspection_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CallableWithoutSource:
        def __call__(self, t: float) -> None:
            return None

    def fail(_draw: object) -> str:
        raise RuntimeError("inspection failed")

    monkeypatch.setattr(output_paths.inspect, "getsourcefile", fail)

    with pytest.raises(RuntimeError, match="inspection failed"):
        output_paths._draw_source_path(CallableWithoutSource())


def test_project_root_lookup_only_handles_shallow_ancestor_chain() -> None:
    assert (
        output_paths._project_root_dir_from_sketch_root(
            Path("/sketch"),
            Path("far/too/deep/sketch"),
        )
        is None
    )


def test_output_path_for_draw_keeps_explicit_configs_isolated(
    tmp_path: Path,
) -> None:
    def draw(_t: float) -> None:
        return None

    config_a_path = tmp_path / "a.yaml"
    config_b_path = tmp_path / "b.yaml"
    config_a_path.write_text("paths:\n  output_dir: output-a\n", encoding="utf-8")
    config_b_path.write_text("paths:\n  output_dir: output-b\n", encoding="utf-8")
    config_a = load_runtime_config(config_a_path)
    config_b = load_runtime_config(config_b_path)

    path_a = output_paths.output_path_for_draw(
        kind="svg",
        ext="svg",
        draw=draw,
        config=config_a,
    )
    path_b = output_paths.output_path_for_draw(
        kind="svg",
        ext="svg",
        draw=draw,
        config=config_b,
    )

    assert path_a.is_relative_to((tmp_path / "output-a").resolve())
    assert path_b.is_relative_to((tmp_path / "output-b").resolve())


def test_output_path_for_draw_requires_explicit_config() -> None:
    with pytest.raises(TypeError, match="config"):
        output_paths.output_path_for_draw(
            kind="svg",
            ext="svg",
            draw=lambda _t: None,
        )  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="config"):
        output_paths.default_param_store_path(
            lambda _t: None,
        )  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("kwargs", "error", "match"),
    (
        ({"kind": 1}, TypeError, "kind"),
        ({"kind": ""}, ValueError, "kind"),
        ({"ext": 1}, TypeError, "ext"),
        ({"ext": ""}, ValueError, "ext"),
        ({"draw": None}, TypeError, "draw"),
        ({"run_id": 1}, TypeError, "run_id"),
        ({"canvas_size": [800, 600]}, TypeError, "canvas_size"),
        ({"canvas_size": (True, 600)}, TypeError, "canvas_size"),
        ({"canvas_size": (float("nan"), 600)}, ValueError, "canvas_size"),
        ({"canvas_size": (0, 600)}, ValueError, "canvas_size"),
        ({"config": object()}, TypeError, "config"),
    ),
)
def test_output_path_for_draw_rejects_implicit_conversions(
    kwargs: dict[str, object],
    error: type[Exception],
    match: str,
) -> None:
    def draw(_t: float) -> None:
        return None

    arguments: dict[str, object] = {
        "kind": "svg",
        "ext": "svg",
        "draw": draw,
        "config": load_runtime_config(),
    }
    arguments.update(kwargs)

    with pytest.raises(error, match=match):
        output_paths.output_path_for_draw(**cast(Any, arguments))
