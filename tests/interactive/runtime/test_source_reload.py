from __future__ import annotations

import builtins
import os
import pickle
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from grafix._source_import_policy import SourceImportPolicyError
from grafix.core.authoring_definitions import default_authoring_definitions
from grafix.core.evaluation_context import EvaluationContext
from grafix.core.evaluation_config import EvaluationConfig
from grafix.core.geometry import Geometry
from grafix.core.parameters import ParamStore, parameter_context
from grafix.core.parameters.snapshot_ops import store_snapshot
from grafix.core.parameters.ui_ops import update_state_from_ui
from grafix.core.realize import realize
from grafix.runtime_config_loader import runtime_config
from grafix.interactive.runtime.mp_draw import DrawResult, MpDraw
from grafix.interactive.runtime.source_reload import (
    ReloadedDraw,
    SourceReloadController,
    SourceReloadResult,
)


def _write_source(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    stat_result = path.stat()
    os.utime(
        path,
        ns=(stat_result.st_atime_ns, stat_result.st_mtime_ns + 1_000_000),
    )


def _external_source_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """現在の作業ディレクトリ外にある source path を返す。"""

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    working_dir = tmp_path / "working"
    working_dir.mkdir()
    monkeypatch.chdir(working_dir)
    return source_dir / "sketch.py"


def _primitive_source(*, x: float, fail: bool = False) -> str:
    failure = "raise RuntimeError('candidate failed')" if fail else ""
    return f"""
import numpy as np
from grafix import G, primitive

@primitive
def watch_reload_shape():
    coords = np.asarray([[{x}, 0.0, 0.0], [{x + 1.0}, 0.0, 0.0]], dtype=np.float32)
    return coords, np.asarray([0, 2], dtype=np.int32)

{failure}

def draw(t):
    return G.watch_reload_shape()
"""


def _preset_source(
    *,
    name: str,
    generation: int,
    kind: str = "float",
    fail: bool = False,
) -> str:
    failure = "raise RuntimeError('candidate failed')" if fail else ""
    return f"""
from grafix import P, preset
from grafix.core.geometry import Geometry

@preset(meta={{"amount": {{"kind": "{kind}"}}}})
def {name}(amount={generation}):
    return Geometry.create(
        op="concat",
        params={{"generation": {generation}, "amount": amount}},
    )

{failure}

def draw(t):
    return P.{name}()
"""


def _helper_primitive_source(*, x: float, fail: bool = False) -> str:
    failure = "raise RuntimeError('helper candidate failed')" if fail else ""
    return f"""
import numpy as np
from grafix import primitive

@primitive
def watch_reload_helper_shape():
    coords = np.asarray([[{x}, 0.0, 0.0], [{x + 1.0}, 0.0, 0.0]], dtype=np.float32)
    return coords, np.asarray([0, 2], dtype=np.int32)

{failure}
"""


def _config_primitive_source(*, x: float) -> str:
    return f"""
import numpy as np
from grafix import primitive

@primitive
def watch_config_worker_shape():
    coords = np.asarray([[{x}, 0.0, 0.0], [{x + 1.0}, 0.0, 0.0]], dtype=np.float32)
    return coords, np.asarray([0, 2], dtype=np.int32)
"""


_RELATIVE_HELPER_MAIN = """
from . import helper as _helper
from grafix import G

def draw(t):
    return G.watch_reload_helper_shape()
"""


_PARAMETER_IDENTITY_SOURCE = """
from grafix import P, preset
from grafix.core.geometry import Geometry

@preset(meta={})
def watch_parameter_identity():
    return Geometry.create(op="concat", params={"marker": "enabled"})

def draw(t):
    return P(key="preview-toggle").watch_parameter_identity()
"""


def _draw_generation(draw: object) -> int:
    result = draw(0.0)  # type: ignore[operator]
    assert isinstance(result, Geometry)
    return int(dict(result.args)["generation"])


def _realize_for_controller(
    controller: SourceReloadController,
    geometry: Geometry,
):
    return realize(
        geometry,
        context=EvaluationContext(
            catalog=controller.operation_catalog,
            quality="final",
            config=EvaluationConfig(font_dirs=runtime_config().font_dirs),
        ),
    )


def _wait_for_worker_result(worker: MpDraw) -> DrawResult:
    deadline = time.monotonic() + 8.0
    result = None
    while result is None and time.monotonic() < deadline:
        result = worker.poll_latest()
        if result is None:
            time.sleep(0.01)
    assert result is not None
    return result


def test_reload_swaps_draw_and_catalog_only_after_candidate_success(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "sketch.py"
    _write_source(source_path, _primitive_source(x=1.0))

    with SourceReloadController(source_path) as controller:
        first_draw = controller.draw
        assert isinstance(first_draw, ReloadedDraw)
        assert first_draw.__grafix_source_path__ == source_path.resolve()
        assert first_draw.__grafix_source_bytes__ == source_path.read_bytes()
        np.testing.assert_allclose(
            _realize_for_controller(controller, first_draw(0.0)).coords[:, 0],
            [1.0, 2.0],
        )

        _write_source(source_path, _primitive_source(x=20.0, fail=True))
        failed = controller.poll(force=True)
        assert failed.status == "failed"
        assert failed.generation == 0
        assert failed.draw is first_draw
        assert failed.source is not None and str(source_path) in failed.source
        np.testing.assert_allclose(
            _realize_for_controller(controller, controller.draw(0.0)).coords[:, 0],
            [1.0, 2.0],
        )

        _write_source(source_path, _primitive_source(x=3.0))
        reloaded = controller.poll(force=True)
        assert reloaded.status == "reloaded"
        assert reloaded.generation == 1
        assert controller.draw is not first_draw
        np.testing.assert_allclose(
            _realize_for_controller(controller, controller.draw(0.0)).coords[:, 0],
            [3.0, 4.0],
        )


def test_unchanged_source_does_not_reload(tmp_path: Path) -> None:
    source_path = tmp_path / "sketch.py"
    _write_source(source_path, _primitive_source(x=1.0))

    with SourceReloadController(source_path) as controller:
        draw = controller.draw
        result = controller.poll()

        assert result.status == "unchanged"
        assert result.generation == 0
        assert result.draw is draw


def test_same_source_reload_preserves_operation_fingerprint_and_geometry_id(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "sketch.py"
    _write_source(source_path, _primitive_source(x=2.0))

    with SourceReloadController(source_path) as controller:
        first_entry = controller.operation_catalog.resolve(
            "primitive",
            "watch_reload_shape",
        )
        first_geometry = controller.draw(0.0)
        reloaded = controller.poll(force=True)
        second_entry = controller.operation_catalog.resolve(
            "primitive",
            "watch_reload_shape",
        )
        second_geometry = controller.draw(0.0)

    assert reloaded.status == "reloaded"
    assert first_entry.declaration.evaluator is not second_entry.declaration.evaluator
    assert first_entry.evaluation_fingerprint == second_entry.evaluation_fingerprint
    assert isinstance(first_geometry, Geometry)
    assert isinstance(second_geometry, Geometry)
    assert first_geometry.id == second_geometry.id


def test_dynamic_operation_owner_is_stable_across_source_generations(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "sketch.py"
    _write_source(
        source_path,
        "from grafix import G, primitive\n\n"
        "@primitive(cache_policy='none', version='stable-v1')\n"
        "def watch_dynamic_shape():\n"
        "    return ((), ())\n\n"
        "def draw(t):\n"
        "    return G.watch_dynamic_shape()\n",
    )

    with SourceReloadController(source_path) as controller:
        first_entry = controller.operation_catalog.resolve(
            "primitive",
            "watch_dynamic_shape",
        )
        first_geometry = controller.draw(0.0)
        reloaded = controller.poll(force=True)
        second_entry = controller.operation_catalog.resolve(
            "primitive",
            "watch_dynamic_shape",
        )
        second_geometry = controller.draw(0.0)

    assert reloaded.status == "reloaded"
    assert first_entry.declaration.source_owner == "_grafix_watch_source._entry"
    assert second_entry.declaration.source_owner == "_grafix_watch_source._entry"
    assert first_entry.ref == second_entry.ref
    assert isinstance(first_geometry, Geometry)
    assert isinstance(second_geometry, Geometry)
    assert first_geometry.id == second_geometry.id


def test_validated_source_snapshot_runs_in_spawn_worker(tmp_path: Path) -> None:
    source_path = tmp_path / "sketch.py"
    _write_source(source_path, _primitive_source(x=7.0))

    with SourceReloadController(source_path) as controller:
        parent_geometry = controller.draw(0.0)
        assert isinstance(parent_geometry, Geometry)
        worker = MpDraw(
            controller.draw,
            n_worker=1,
            effective_config=runtime_config(),
        )
        try:
            worker.submit(
                t=0.0,
                snapshot_revision=0,
                snapshot={},
                effect_order_snapshot={},
                epoch=0,
                quality="draft",
            )
            deadline = time.monotonic() + 5.0
            result = None
            while result is None and time.monotonic() < deadline:
                result = worker.poll_latest()
                if result is None:
                    time.sleep(0.01)
            assert result is not None
            assert result.error is None
            assert result.layers[0].geometry.op == "watch_reload_shape"
            assert result.layers[0].geometry.id == parent_geometry.id
            assert (
                result.layers[0].geometry.operation
                == parent_geometry.operation
            )
            np.testing.assert_allclose(
                _realize_for_controller(
                    controller,
                    result.layers[0].geometry,
                ).coords[:, 0],
                [7.0, 8.0],
            )
        finally:
            worker.close()


def test_source_parameter_site_id_matches_between_parent_and_spawn_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = _external_source_path(tmp_path, monkeypatch)
    _write_source(source_path, _PARAMETER_IDENTITY_SOURCE)
    store = ParamStore()

    with SourceReloadController(source_path) as controller:
        with parameter_context(store):
            controller.draw(0.0)
        parent_keys = tuple(store_snapshot(store))

        worker = MpDraw(
            controller.draw,
            n_worker=1,
            effective_config=runtime_config(),
        )
        try:
            worker.submit(
                t=0.0,
                snapshot_revision=store.revision,
                snapshot=store_snapshot(store),
                effect_order_snapshot={},
                epoch=0,
                quality="draft",
            )
            result = _wait_for_worker_result(worker)
        finally:
            worker.close()

    assert result.error is None
    assert tuple(record.key for record in result.records) == parent_keys


def test_parent_parameter_snapshot_controls_spawn_worker_preview(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = _external_source_path(tmp_path, monkeypatch)
    _write_source(source_path, _PARAMETER_IDENTITY_SOURCE)
    store = ParamStore()

    with SourceReloadController(source_path) as controller:
        with parameter_context(store):
            active_geometry = controller.draw(0.0)
        assert isinstance(active_geometry, Geometry)
        assert dict(active_geometry.args)["marker"] == "enabled"

        activate_key = next(key for key in store_snapshot(store) if key.arg == "activate")
        activate_meta = store.get_meta(activate_key)
        assert activate_meta is not None
        updated, error = update_state_from_ui(
            store,
            activate_key,
            False,
            meta=activate_meta,
            override=True,
        )
        assert updated and error is None

        worker = MpDraw(
            controller.draw,
            n_worker=1,
            effective_config=runtime_config(),
        )
        try:
            worker.submit(
                t=0.0,
                snapshot_revision=store.revision,
                snapshot=store_snapshot(store),
                effect_order_snapshot={},
                epoch=0,
                quality="draft",
            )
            result = _wait_for_worker_result(worker)
        finally:
            worker.close()

    assert result.error is None
    assert dict(result.layers[0].geometry.args) == {}
    activate_record = next(record for record in result.records if record.key.arg == "activate")
    assert activate_record.key == activate_key
    assert activate_record.effective is False
    assert activate_record.source == "ui"


def test_source_reload_keeps_one_parameter_site_in_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = _external_source_path(tmp_path, monkeypatch)
    _write_source(source_path, _PARAMETER_IDENTITY_SOURCE)
    store = ParamStore()

    with SourceReloadController(source_path) as controller:
        with parameter_context(store):
            controller.draw(0.0)
        first_site_ids = {key.site_id for key in store_snapshot(store)}
        assert len(first_site_ids) == 1

        reloaded = controller.poll(force=True)
        assert reloaded.status == "reloaded"
        with parameter_context(store):
            controller.draw(0.0)

    assert {key.site_id for key in store_snapshot(store)} == first_site_ids


def test_relative_helper_edit_creates_a_new_isolated_generation(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "sketch.py"
    helper_path = tmp_path / "helper.py"
    _write_source(source_path, _RELATIVE_HELPER_MAIN)
    _write_source(helper_path, _helper_primitive_source(x=2.0))

    with SourceReloadController(source_path) as controller:
        first_entry = controller.operation_catalog.resolve(
            "primitive",
            "watch_reload_helper_shape",
        )
        first_geometry = controller.draw(0.0)
        assert isinstance(first_geometry, Geometry)
        np.testing.assert_allclose(
            _realize_for_controller(controller, first_geometry).coords[:, 0],
            [2.0, 3.0],
        )

        _write_source(helper_path, _helper_primitive_source(x=9.0))
        reloaded = controller.poll()
        second_entry = controller.operation_catalog.resolve(
            "primitive",
            "watch_reload_helper_shape",
        )
        second_geometry = controller.draw(0.0)
        assert isinstance(second_geometry, Geometry)

        assert reloaded.status == "reloaded"
        assert second_entry.evaluation_fingerprint != first_entry.evaluation_fingerprint
        assert second_geometry.id != first_geometry.id
        np.testing.assert_allclose(
            _realize_for_controller(controller, second_geometry).coords[:, 0],
            [9.0, 10.0],
        )


def test_deferred_relative_import_is_rejected_before_reload_adoption(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "sketch.py"
    missing_helper_path = tmp_path / "missing_helper.py"
    _write_source(
        source_path,
        "from grafix import G\n\n"
        "def draw(t):\n"
        "    return G.line(length=1.0)\n",
    )

    with SourceReloadController(source_path) as controller:
        original_draw = controller.draw
        original_definitions = controller.definitions
        failed_package = f"_grafix_watch_{controller._namespace_token}_2"
        meta_path_before = tuple(sys.meta_path)
        _write_source(
            source_path,
            "from grafix import G\n\n"
            "def draw(t):\n"
            "    from .missing_helper import OFFSET\n"
            "    return G.line(length=OFFSET)\n",
        )

        failed = controller.poll()

        assert failed.status == "failed"
        assert failed.generation == 0
        assert failed.summary is not None
        assert "SourceImportPolicyError" in failed.summary
        assert f"{source_path}:4" in failed.summary
        assert "function 'draw'" in failed.summary
        assert failed.source == f"{source_path}:4"
        assert controller.draw is original_draw
        assert controller.definitions is original_definitions
        assert dict(controller.draw(0.0).args)["length"] == 1.0
        assert tuple(sys.meta_path) == meta_path_before
        assert not any(
            name == failed_package or name.startswith(f"{failed_package}.")
            for name in sys.modules
        )

        _write_source(missing_helper_path, "OFFSET = 8.0\n")
        unchanged = controller.poll()
        assert unchanged.status == "unchanged"
        assert controller.draw is original_draw

        _write_source(
            source_path,
            "from .missing_helper import OFFSET\n"
            "from grafix import G\n\n"
            "def draw(t):\n"
            "    return G.line(length=OFFSET)\n",
        )
        recovered = controller.poll()

        assert recovered.status == "reloaded"
        assert dict(controller.draw(0.0).args)["length"] == 8.0


def test_deferred_import_in_helper_does_not_watch_its_missing_dependency(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "sketch.py"
    helper_path = tmp_path / "helper.py"
    missing_path = tmp_path / "missing.py"
    _write_source(
        source_path,
        "from grafix import G\n\n"
        "def draw(t):\n"
        "    return G.line(length=1.0)\n",
    )

    with SourceReloadController(source_path) as controller:
        original_draw = controller.draw
        _write_source(
            helper_path,
            "def value():\n"
            "    from .missing import OFFSET\n"
            "    return OFFSET\n",
        )
        _write_source(
            source_path,
            "from .helper import value\n"
            "from grafix import G\n\n"
            "def draw(t):\n"
            "    return G.line(length=value())\n",
        )

        failed = controller.poll()

        assert failed.status == "failed"
        assert failed.summary is not None
        assert f"{helper_path}:2" in failed.summary
        assert failed.source == f"{helper_path}:2"
        assert controller.draw is original_draw

        _write_source(missing_path, "OFFSET = 13.0\n")
        unchanged = controller.poll()
        assert unchanged.status == "unchanged"
        assert controller.draw is original_draw

        _write_source(
            helper_path,
            "from .missing import OFFSET\n\n"
            "def value():\n"
            "    return OFFSET\n",
        )
        recovered = controller.poll()

        assert recovered.status == "reloaded"
        assert dict(controller.draw(0.0).args)["length"] == 13.0


def test_pickled_reloaded_draw_revalidates_captured_source_policy(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "sketch.py"
    _write_source(
        source_path,
        "from grafix import G\n\n"
        "def draw(t):\n"
        "    return G.line(length=1.0)\n",
    )

    with SourceReloadController(source_path) as controller:
        draw = controller.draw
        assert isinstance(draw, ReloadedDraw)
        source_package = draw._source_package
        assert source_package is not None
        invalid_content = (
            b"from grafix import G\n\n"
            b"def draw(t):\n"
            b"    from .helper import OFFSET\n"
            b"    return G.line(length=OFFSET)\n"
        )
        invalid_modules = tuple(
            replace(module, content=invalid_content)
            if module.relative_path == source_package.main_relative_path
            else module
            for module in source_package.modules
        )
        invalid_package = replace(source_package, modules=invalid_modules)
        worker_draw = ReloadedDraw(
            path=source_path,
            source_bytes=invalid_content,
            module_name="_source_policy_roundtrip",
            draw_attribute="draw",
            baseline=controller.baseline,
            source_package=invalid_package,
        )

        restored = pickle.loads(pickle.dumps(worker_draw))
        with pytest.raises(SourceImportPolicyError, match=r"sketch\.py:4"):
            restored(0.0)

    assert not any(
        name == "_source_policy_roundtrip_worker"
        or name.startswith("_source_policy_roundtrip_worker.")
        for name in sys.modules
    )


def test_unreferenced_python_file_is_not_snapshotted_or_polled(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "sketch.py"
    helper_path = tmp_path / "helper.py"
    unrelated_path = tmp_path / "unrelated.py"
    _write_source(source_path, _RELATIVE_HELPER_MAIN)
    _write_source(helper_path, _helper_primitive_source(x=2.0))
    _write_source(unrelated_path, "UNRELATED = 1\n")

    with SourceReloadController(source_path) as controller:
        draw = controller.draw
        assert isinstance(draw, ReloadedDraw)
        source_package = draw._source_package
        assert source_package is not None
        assert tuple(module.relative_path for module in source_package.modules) == (
            "helper.py",
            "sketch.py",
        )

        _write_source(unrelated_path, "UNRELATED = 2\n")
        unchanged = controller.poll()

        assert unchanged.status == "unchanged"
        assert unchanged.generation == 0
        assert controller.draw is draw


def test_new_relative_helper_syntax_failure_is_watched_until_fixed(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "sketch.py"
    helper_path = tmp_path / "helper.py"
    _write_source(
        source_path,
        "from grafix import G\n\ndef draw(t):\n    return G.line(length=1.0)\n",
    )

    with SourceReloadController(source_path) as controller:
        _write_source(helper_path, "OFFSET = (\n")
        _write_source(
            source_path,
            "from .helper import OFFSET\n"
            "from grafix import G\n\n"
            "def draw(t):\n"
            "    return G.line(length=OFFSET)\n",
        )
        failed = controller.poll()
        assert failed.status == "failed"
        assert dict(controller.draw(0.0).args)["length"] == 1.0

        _write_source(helper_path, "OFFSET = 8.0\n")
        recovered = controller.poll()

        assert recovered.status == "reloaded"
        assert dict(controller.draw(0.0).args)["length"] == 8.0


def test_new_missing_relative_helper_is_watched_until_created(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "sketch.py"
    helper_path = tmp_path / "missing_helper.py"
    _write_source(
        source_path,
        "from grafix import G\n\ndef draw(t):\n    return G.line(length=1.0)\n",
    )

    with SourceReloadController(source_path) as controller:
        _write_source(
            source_path,
            "from .missing_helper import OFFSET\n"
            "from grafix import G\n\n"
            "def draw(t):\n"
            "    return G.line(length=OFFSET)\n",
        )
        failed = controller.poll()
        assert failed.status == "failed"
        assert dict(controller.draw(0.0).args)["length"] == 1.0

        _write_source(helper_path, "OFFSET = 6.0\n")
        recovered = controller.poll()

        assert recovered.status == "reloaded"
        assert dict(controller.draw(0.0).args)["length"] == 6.0


def test_deleted_relative_helper_is_watched_until_restored(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "sketch.py"
    helper_path = tmp_path / "helper.py"
    _write_source(source_path, _RELATIVE_HELPER_MAIN)
    _write_source(helper_path, _helper_primitive_source(x=4.0))

    with SourceReloadController(source_path) as controller:
        original_draw = controller.draw
        helper_path.unlink()
        failed = controller.poll()

        assert failed.status == "failed"
        assert controller.draw is original_draw
        np.testing.assert_allclose(
            _realize_for_controller(controller, controller.draw(0.0)).coords[:, 0],
            [4.0, 5.0],
        )

        _write_source(helper_path, _helper_primitive_source(x=11.0))
        recovered = controller.poll()

        assert recovered.status == "reloaded"
        np.testing.assert_allclose(
            _realize_for_controller(controller, controller.draw(0.0)).coords[:, 0],
            [11.0, 12.0],
        )


def test_failed_relative_helper_generation_rolls_back_and_cleans_modules(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "sketch.py"
    helper_path = tmp_path / "helper.py"
    _write_source(source_path, _RELATIVE_HELPER_MAIN)
    _write_source(helper_path, _helper_primitive_source(x=4.0))

    with SourceReloadController(source_path) as controller:
        original_draw = controller.draw
        original_definitions = controller.definitions
        failed_package = f"_grafix_watch_{controller._namespace_token}_2"
        _write_source(helper_path, _helper_primitive_source(x=12.0, fail=True))

        failed = controller.poll()

        assert failed.status == "failed"
        assert controller.draw is original_draw
        assert controller.definitions is original_definitions
        np.testing.assert_allclose(
            _realize_for_controller(controller, controller.draw(0.0)).coords[:, 0],
            [4.0, 5.0],
        )
        assert not any(
            name == failed_package or name.startswith(f"{failed_package}.")
            for name in sys.modules
        )


@pytest.mark.parametrize(
    ("exception_name", "exception_type"),
    [
        ("KeyboardInterrupt", KeyboardInterrupt),
        ("SystemExit", SystemExit),
    ],
)
def test_initial_load_propagates_process_control_after_candidate_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exception_name: str,
    exception_type: type[BaseException],
) -> None:
    source_path = tmp_path / "sketch.py"
    process_control = exception_type("stop")
    monkeypatch.setattr(
        builtins,
        "_grafix_test_process_control",
        process_control,
        raising=False,
    )
    _write_source(
        source_path,
        "import builtins\n\n"
        "raise builtins._grafix_test_process_control\n\n"
        "def draw(t):\n"
        "    return ()\n",
    )
    source_modules_before = {
        name for name in sys.modules if name.startswith("_grafix_watch_")
    }
    meta_path_before = tuple(sys.meta_path)

    with pytest.raises(exception_type, match="stop") as caught:
        SourceReloadController(source_path)

    assert type(caught.value).__name__ == exception_name
    assert caught.value is process_control
    assert tuple(sys.meta_path) == meta_path_before
    assert {
        name for name in sys.modules if name.startswith("_grafix_watch_")
    } == source_modules_before


def test_initial_load_preserves_ordinary_error_as_runtime_error_cause(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "sketch.py"
    _write_source(
        source_path,
        "raise ValueError('broken source')\n\ndef draw(t):\n    return ()\n",
    )

    with pytest.raises(RuntimeError, match="ValueError: broken source") as caught:
        SourceReloadController(source_path)

    cause = caught.value.__cause__
    assert type(cause) is ValueError
    assert str(cause) == "broken source"


@pytest.mark.parametrize(
    ("exception_name", "exception_type"),
    [
        ("KeyboardInterrupt", KeyboardInterrupt),
        ("SystemExit", SystemExit),
    ],
)
def test_subsequent_reload_propagates_process_control_and_keeps_last_good_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exception_name: str,
    exception_type: type[BaseException],
) -> None:
    source_path = tmp_path / "sketch.py"
    _write_source(source_path, _primitive_source(x=4.0))

    with SourceReloadController(source_path) as controller:
        original_draw = controller.draw
        original_definitions = controller.definitions
        failed_package = f"_grafix_watch_{controller._namespace_token}_2"
        meta_path_before = tuple(sys.meta_path)
        process_control = exception_type("stop")
        monkeypatch.setattr(
            builtins,
            "_grafix_test_process_control",
            process_control,
            raising=False,
        )
        _write_source(
            source_path,
            "import builtins\n\n"
            "raise builtins._grafix_test_process_control\n\n"
            "def draw(t):\n"
            "    return ()\n",
        )

        with pytest.raises(exception_type, match="stop") as caught:
            controller.poll(force=True)

        assert type(caught.value).__name__ == exception_name
        assert caught.value is process_control
        assert tuple(sys.meta_path) == meta_path_before
        assert controller.generation == 0
        assert controller.draw is original_draw
        assert controller.definitions is original_definitions
        geometry = controller.draw(0.0)
        assert isinstance(geometry, Geometry)
        np.testing.assert_allclose(
            _realize_for_controller(controller, geometry).coords[:, 0],
            np.asarray([4.0, 5.0]),
        )
        assert not any(
            name == failed_package or name.startswith(f"{failed_package}.")
            for name in sys.modules
        )


def test_same_named_helpers_in_parallel_controllers_never_share_modules(
    tmp_path: Path,
) -> None:
    paths: list[Path] = []
    for directory_name, x in (("first", 3.0), ("second", 30.0)):
        directory = tmp_path / directory_name
        directory.mkdir()
        source_path = directory / "sketch.py"
        _write_source(source_path, _RELATIVE_HELPER_MAIN)
        _write_source(directory / "helper.py", _helper_primitive_source(x=x))
        paths.append(source_path)
    plain_helper_before = sys.modules.get("helper")

    with ThreadPoolExecutor(max_workers=2) as executor:
        controllers = tuple(executor.map(SourceReloadController, paths))
    try:
        realized = tuple(
            _realize_for_controller(controller, controller.draw(0.0))
            for controller in controllers
        )

        np.testing.assert_allclose(realized[0].coords[:, 0], [3.0, 4.0])
        np.testing.assert_allclose(realized[1].coords[:, 0], [30.0, 31.0])
        assert controllers[0]._module_name != controllers[1]._module_name
        assert sys.modules.get("helper") is plain_helper_before
    finally:
        for controller in controllers:
            controller.close()


def test_source_generation_close_removes_entry_and_helper_modules(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "sketch.py"
    _write_source(source_path, _RELATIVE_HELPER_MAIN)
    _write_source(tmp_path / "helper.py", _helper_primitive_source(x=5.0))
    controller = SourceReloadController(source_path)
    package_name = controller._module_name
    assert package_name is not None
    assert package_name in sys.modules
    assert f"{package_name}._entry" in sys.modules
    assert f"{package_name}.helper" in sys.modules

    controller.close()

    assert not any(
        name == package_name or name.startswith(f"{package_name}.")
        for name in sys.modules
    )


def test_spawn_worker_uses_captured_helper_bytes_not_later_filesystem_state(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "sketch.py"
    helper_path = tmp_path / "helper.py"
    _write_source(source_path, _RELATIVE_HELPER_MAIN)
    _write_source(helper_path, _helper_primitive_source(x=7.0))

    with SourceReloadController(source_path) as controller:
        parent_geometry = controller.draw(0.0)
        assert isinstance(parent_geometry, Geometry)
        _write_source(helper_path, _helper_primitive_source(x=70.0))
        worker = MpDraw(
            controller.draw,
            n_worker=1,
            effective_config=runtime_config(),
        )
        try:
            worker.submit(
                t=0.0,
                snapshot_revision=0,
                snapshot={},
                effect_order_snapshot={},
                epoch=0,
                quality="draft",
            )
            deadline = time.monotonic() + 5.0
            result = None
            while result is None and time.monotonic() < deadline:
                result = worker.poll_latest()
                if result is None:
                    time.sleep(0.01)

            assert result is not None
            assert result.error is None
            worker_geometry = result.layers[0].geometry
            assert worker_geometry.operation == parent_geometry.operation
            np.testing.assert_allclose(
                _realize_for_controller(controller, worker_geometry).coords[:, 0],
                [7.0, 8.0],
            )
        finally:
            worker.close()


def test_spawn_worker_uses_parent_config_recipe_after_helper_disk_edit(
    tmp_path: Path,
) -> None:
    config_root = tmp_path / "config-authoring"
    config_root.mkdir()
    config_helper = config_root / "helper.py"
    source_path = tmp_path / "sketch.py"
    _write_source(config_helper, _config_primitive_source(x=8.0))
    _write_source(
        source_path,
        "from grafix import G\n\n"
        "def draw(t):\n"
        "    return G.watch_config_worker_shape()\n",
    )
    config = replace(
        runtime_config(),
        preset_module_dirs=(config_root,),
    )

    with SourceReloadController(source_path, config=config) as controller:
        parent_geometry = controller.draw(0.0)
        assert isinstance(parent_geometry, Geometry)
        parent_entry = controller.operation_catalog.resolve(
            "primitive",
            "watch_config_worker_shape",
        )
        _write_source(config_helper, _config_primitive_source(x=80.0))
        worker = MpDraw(
            controller.draw,
            n_worker=1,
            effective_config=config,
            definitions=controller.definitions,
        )
        try:
            worker.submit(
                t=0.0,
                snapshot_revision=0,
                snapshot={},
                effect_order_snapshot={},
                epoch=0,
                quality="draft",
            )
            deadline = time.monotonic() + 5.0
            result = None
            while result is None and time.monotonic() < deadline:
                result = worker.poll_latest()
                if result is None:
                    time.sleep(0.01)

            assert result is not None
            assert result.error is None
            worker_geometry = result.layers[0].geometry
            assert worker_geometry.operation == parent_entry.ref
            assert worker_geometry.operation == parent_geometry.operation
            assert worker_geometry.id == parent_geometry.id
            np.testing.assert_allclose(
                _realize_for_controller(controller, worker_geometry).coords[:, 0],
                [8.0, 9.0],
            )
        finally:
            worker.close()


def test_reload_removes_presets_deleted_from_source(tmp_path: Path) -> None:
    source_path = tmp_path / "sketch.py"
    _write_source(
        source_path,
        """
from grafix import G, P, preset

@preset(meta={"length": {"kind": "float"}})
def watch_reload_preset(length=2.0):
    return G.line(length=length)

def draw(t):
    return P.watch_reload_preset()
""",
    )

    with SourceReloadController(source_path) as controller:
        assert "watch_reload_preset" in controller.preset_catalog

        _write_source(
            source_path,
            """
from grafix import G

def draw(t):
    return G.line(length=4.0)
""",
        )
        result = controller.poll(force=True)

        assert result.status == "reloaded"
        assert "watch_reload_preset" not in controller.preset_catalog


def test_preset_reload_is_atomic_and_rollback_restores_one_spec(
    tmp_path: Path,
) -> None:
    name = "watch_atomic_preset"
    source_path = tmp_path / "sketch.py"
    _write_source(
        source_path,
        _preset_source(name=name, generation=1),
    )

    controller = SourceReloadController(source_path)
    try:
        original_definitions = controller.definitions
        original_declaration = controller.preset_catalog[name]
        assert original_declaration.schema.meta["amount"].kind == "float"
        assert _draw_generation(controller.draw) == 1

        _write_source(
            source_path,
            _preset_source(
                name=name,
                generation=2,
                kind="int",
                fail=True,
            ),
        )
        failed = controller.poll(force=True)

        assert failed.status == "failed"
        assert controller.definitions is original_definitions
        assert controller.preset_catalog[name] is original_declaration
        assert _draw_generation(controller.draw) == 1

        _write_source(
            source_path,
            _preset_source(name=name, generation=2, kind="int"),
        )
        reloaded = controller.poll(force=True, retain_rollback=True)

        assert reloaded.status == "reloaded"
        replacement_definitions = controller.definitions
        replacement_declaration = controller.preset_catalog[name]
        assert replacement_definitions is not original_definitions
        assert replacement_declaration is not original_declaration
        assert replacement_declaration.schema.meta["amount"].kind == "int"
        assert _draw_generation(controller.draw) == 2

        restored_draw = controller.rollback_generation(reloaded.generation)

        assert controller.definitions is original_definitions
        assert controller.preset_catalog[name] is original_declaration
        assert restored_draw is controller.draw
        assert _draw_generation(restored_draw) == 1
    finally:
        controller.close()


def test_reload_keeps_source_preset_in_candidate_catalog_only(
    tmp_path: Path,
) -> None:
    name = "watch_registry_binding_preset"
    source_path = tmp_path / "sketch.py"
    _write_source(
        source_path,
        _preset_source(
            name=name,
            generation=3,
        ),
    )

    defaults_before = default_authoring_definitions.snapshot()
    with SourceReloadController(source_path) as controller:
        assert _draw_generation(controller.draw) == 3
        assert name in controller.preset_catalog
        assert name not in default_authoring_definitions.snapshot().presets

    defaults_after = default_authoring_definitions.snapshot()
    assert tuple(defaults_after.presets) == tuple(defaults_before.presets)


def test_transactional_reload_can_rollback_catalog_and_draw(tmp_path: Path) -> None:
    source_path = tmp_path / "sketch.py"
    _write_source(source_path, _primitive_source(x=1.0))

    with SourceReloadController(source_path) as controller:
        first_draw = controller.draw
        _write_source(source_path, _primitive_source(x=9.0))
        result = controller.poll(force=True, retain_rollback=True)

        assert result.status == "reloaded"
        np.testing.assert_allclose(
            _realize_for_controller(controller, controller.draw(0.0)).coords[:, 0],
            [9.0, 10.0],
        )

        restored = controller.rollback_generation(result.generation)

        assert restored is first_draw
        assert controller.generation == 0
        np.testing.assert_allclose(
            _realize_for_controller(controller, controller.draw(0.0)).coords[:, 0],
            [1.0, 2.0],
        )


def test_pending_transaction_must_be_explicitly_finished_before_next_poll(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "sketch.py"
    _write_source(source_path, _primitive_source(x=1.0))

    with SourceReloadController(source_path) as controller:
        first_draw = controller.draw
        _write_source(source_path, _primitive_source(x=9.0))
        result = controller.poll(force=True, retain_rollback=True)

        with pytest.raises(RuntimeError, match="未確定.*accept_generation.*rollback_generation"):
            controller.poll()
        with pytest.raises(RuntimeError, match="generation=1"):
            controller.poll(force=True)

        assert controller.generation == result.generation
        restored = controller.rollback_generation(result.generation)
        assert restored is first_draw
        assert controller.generation == 0


def test_explicit_accept_allows_the_next_poll(tmp_path: Path) -> None:
    source_path = tmp_path / "sketch.py"
    _write_source(source_path, _primitive_source(x=1.0))

    with SourceReloadController(source_path) as controller:
        _write_source(source_path, _primitive_source(x=2.0))
        result = controller.poll(force=True, retain_rollback=True)

        controller.accept_generation(result.generation)
        unchanged = controller.poll()

        assert unchanged.status == "unchanged"
        assert unchanged.generation == result.generation


def test_accept_and_rollback_are_one_shot_and_check_generation(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "sketch.py"
    _write_source(source_path, _primitive_source(x=1.0))

    with SourceReloadController(source_path) as controller:
        _write_source(source_path, _primitive_source(x=2.0))
        accepted = controller.poll(force=True, retain_rollback=True)

        with pytest.raises(ValueError, match="generation"):
            controller.accept_generation(accepted.generation - 1)
        with pytest.raises(ValueError, match="generation"):
            controller.rollback_generation(accepted.generation - 1)
        controller.accept_generation(accepted.generation)
        with pytest.raises(ValueError, match="accept可能"):
            controller.accept_generation(accepted.generation)
        with pytest.raises(ValueError, match="rollback可能"):
            controller.rollback_generation(accepted.generation)

        _write_source(source_path, _primitive_source(x=3.0))
        rolled_back = controller.poll(force=True, retain_rollback=True)
        controller.rollback_generation(rolled_back.generation)
        with pytest.raises(ValueError, match="accept可能"):
            controller.accept_generation(rolled_back.generation)
        with pytest.raises(ValueError, match="rollback可能"):
            controller.rollback_generation(rolled_back.generation)


def test_close_finishes_a_pending_transaction_as_terminal_cleanup(
    tmp_path: Path,
) -> None:
    defaults_before = default_authoring_definitions.snapshot()
    source_path = tmp_path / "sketch.py"
    _write_source(source_path, _primitive_source(x=1.0))
    controller = SourceReloadController(source_path)
    _write_source(source_path, _primitive_source(x=2.0))
    controller.poll(force=True, retain_rollback=True)

    controller.close()
    controller.close()

    defaults_after = default_authoring_definitions.snapshot()
    assert tuple(defaults_after.operations) == tuple(defaults_before.operations)


def test_runtime_error_then_source_fix_uses_new_generation(tmp_path: Path) -> None:
    source_path = tmp_path / "sketch.py"
    _write_source(source_path, _primitive_source(x=4.0))

    with SourceReloadController(source_path) as controller:
        last_good = _realize_for_controller(controller, controller.draw(0.0))
        _write_source(
            source_path,
            "from grafix import G\n\ndef draw(t):\n    raise RuntimeError('bad frame')\n",
        )
        bad = controller.poll(force=True)
        assert bad.status == "reloaded"
        with pytest.raises(RuntimeError, match="bad frame"):
            controller.draw(0.0)
        np.testing.assert_allclose(last_good.coords[:, 0], [4.0, 5.0])

        _write_source(source_path, _primitive_source(x=6.0))
        fixed = controller.poll(force=True)
        assert fixed.status == "reloaded"
        np.testing.assert_allclose(
            _realize_for_controller(controller, controller.draw(0.0)).coords[:, 0],
            [6.0, 7.0],
        )


@pytest.mark.parametrize(
    "source",
    [
        "value = 1\n",
        "def draw():\n    return None\n",
        "async def draw(t):\n    return None\n",
    ],
)
def test_initial_load_rejects_missing_or_invalid_draw_signature(
    tmp_path: Path,
    source: str,
) -> None:
    source_path = tmp_path / "invalid.py"
    _write_source(source_path, source)

    with pytest.raises(RuntimeError, match="draw|callable"):
        SourceReloadController(source_path)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("status", "legacy", ValueError),
        ("generation", True, TypeError),
        ("generation", -2, ValueError),
        ("draw", object(), TypeError),
        ("summary", object(), TypeError),
    ],
)
def test_source_reload_result_validates_direct_construction(
    field: str,
    value: object,
    error: type[Exception],
) -> None:
    values: dict[str, object] = {
        "status": "unchanged",
        "generation": 0,
        "draw": lambda _t: (),
    }
    values[field] = value
    with pytest.raises(error):
        SourceReloadResult(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("path", "sketch.py", TypeError),
        ("source_bytes", bytearray(b""), TypeError),
        ("module_name", 1, TypeError),
        ("module_name", "", ValueError),
        ("draw_attribute", 1, TypeError),
        ("draw_attribute", "", ValueError),
        ("loaded_draw", object(), TypeError),
    ],
)
def test_reloaded_draw_rejects_implicit_constructor_coercion(
    field: str,
    value: object,
    error: type[Exception],
) -> None:
    values: dict[str, object] = {
        "path": Path("sketch.py"),
        "source_bytes": b"",
        "module_name": "_sketch",
        "draw_attribute": "draw",
        "loaded_draw": lambda _t: (),
    }
    values[field] = value
    with pytest.raises(error):
        ReloadedDraw(**values)  # type: ignore[arg-type]


def test_reloaded_draw_rejects_implicit_time_coercion() -> None:
    draw = ReloadedDraw(
        path=Path("sketch.py"),
        source_bytes=b"",
        module_name="_sketch",
        draw_attribute="draw",
        loaded_draw=lambda _t: (),
    )
    with pytest.raises(TypeError, match="t"):
        draw("0")  # type: ignore[arg-type]


def test_source_reload_controller_rejects_noncanonical_controls(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "sketch.py"
    _write_source(source_path, _primitive_source(x=1.0))

    with pytest.raises(TypeError, match="path"):
        SourceReloadController(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="draw_attribute"):
        SourceReloadController(source_path, draw_attribute=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="空白"):
        SourceReloadController(source_path, draw_attribute=" draw ")

    with SourceReloadController(source_path) as controller:
        with pytest.raises(TypeError, match="force"):
            controller.poll(force=1)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="retain_rollback"):
            controller.poll(retain_rollback=0)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="generation"):
            controller.accept_generation(True)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="generation"):
            controller.rollback_generation("0")  # type: ignore[arg-type]
