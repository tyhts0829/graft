from __future__ import annotations

import builtins
import os
import pickle
import py_compile
import sys
from concurrent.futures import Future
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from grafix import G, P
from grafix._source_import_policy import SourceImportPolicyError
from grafix.api import preset
from grafix.authoring_loader import (
    capture_authoring_definitions_recipe,
    default_session_authoring_definitions,
    load_authoring_definitions_recipe,
    load_config_authoring_definitions,
)
from grafix.core.authoring_definitions import (
    AuthoringDefinitionsSnapshot,
    RegistrationTarget,
    registration_scope,
)
from grafix.core.authoring_recipe import (
    AuthoringDefinitionsRecipe,
    AuthoringModuleSource,
    AuthoringSourceRoot,
)
from grafix.core.geometry import Geometry
from grafix.core.operation_catalog import bind_operation_catalog
from grafix.core.preset_catalog import bind_preset_catalog
from grafix.core.runtime_config import RuntimeConfig
from grafix.interactive.runtime.source_reload import SourceReloadController
from grafix.runtime_config_loader import load_runtime_config


def _config(tmp_path: Path, name: str, source: str) -> RuntimeConfig:
    preset_dir = tmp_path / name
    preset_dir.mkdir()
    (preset_dir / "candidate.py").write_text(source, encoding="utf-8")
    config_path = tmp_path / f"{name}.yaml"
    config_path.write_text(
        "\n".join(
            [
                "version: 1",
                "paths:",
                '  output_dir: "output"',
                "  preset_module_dirs:",
                f'    - "{preset_dir.as_posix()}"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return load_runtime_config(config_path)


def _config_for_dirs(
    tmp_path: Path,
    name: str,
    directories: tuple[Path, ...],
) -> RuntimeConfig:
    config_path = tmp_path / f"{name}.yaml"
    config_path.write_text(
        "\n".join(
            [
                "version: 1",
                "paths:",
                '  output_dir: "output"',
                "  preset_module_dirs:",
                *(f'    - "{directory.as_posix()}"' for directory in directories),
                "",
            ]
        ),
        encoding="utf-8",
    )
    return load_runtime_config(config_path)


def _preset_source(value: int, *, name: str = "isolated") -> str:
    return (
        "from grafix.api import preset\n"
        "from grafix.core.geometry import Geometry\n"
        "@preset(meta={})\n"
        f"def {name}():\n"
        f"    return Geometry.create(op='concat', params={{'value': {value}}})\n"
    )


def _preset_value(snapshot: AuthoringDefinitionsSnapshot, name: str) -> int:
    with bind_preset_catalog(snapshot.presets):
        return int(dict(getattr(P, name)().args)["value"])


def test_config_catalogs_with_same_name_are_session_local(tmp_path: Path) -> None:
    first = load_config_authoring_definitions(
        _config(tmp_path, "a", _preset_source(1)),
        seed=default_session_authoring_definitions(),
    )
    second = load_config_authoring_definitions(
        _config(tmp_path, "b", _preset_source(2)),
        seed=default_session_authoring_definitions(),
    )

    assert _preset_value(first, "isolated") == 1
    assert _preset_value(second, "isolated") == 2
    assert first.presets["isolated"].func.__module__ != second.presets["isolated"].func.__module__
    assert not any(name.startswith("_grafix_config_authoring_") for name in sys.modules)


def test_failed_candidate_does_not_publish_partial_catalog(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        "broken",
        _preset_source(1, name="partial") + "raise RuntimeError('broken')\n",
    )
    seed = default_session_authoring_definitions()

    with pytest.raises(RuntimeError, match="broken"):
        load_config_authoring_definitions(config, seed=seed)

    assert "partial" not in seed.presets
    assert not any(name.startswith("_grafix_config_authoring_") for name in sys.modules)


def test_default_and_config_duplicate_only_rejects_candidate(tmp_path: Path) -> None:
    base = default_session_authoring_definitions()
    target = RegistrationTarget(
        operations=base.operations,
        presets=base.presets,
    )
    with registration_scope(target):

        @preset(meta={})
        def loader_default_contract() -> Geometry:
            return Geometry.create(op="concat")

    seed = target.snapshot()
    name = "loader_default_contract"
    declaration = seed.presets[name]
    config = _config(tmp_path, "duplicate", _preset_source(3, name=name))

    with pytest.raises(ValueError, match="既に登録"):
        load_config_authoring_definitions(config, seed=seed)

    assert seed.presets[name] is declaration
    assert name not in default_session_authoring_definitions().presets


def test_threaded_config_loads_do_not_mix_registration_targets(tmp_path: Path) -> None:
    configs = tuple(
        _config(tmp_path, f"thread-{index}", _preset_source(index)) for index in range(4)
    )
    seed = default_session_authoring_definitions()

    with ThreadPoolExecutor(max_workers=4) as executor:
        snapshots = tuple(
            executor.map(
                lambda config: load_config_authoring_definitions(config, seed=seed),
                configs,
            )
        )

    assert tuple(_preset_value(snapshot, "isolated") for snapshot in snapshots) == (
        0,
        1,
        2,
        3,
    )


def test_threaded_catalog_use_does_not_mix_same_name_presets(tmp_path: Path) -> None:
    configs = tuple(
        _config(tmp_path, f"bound-thread-{index}", _preset_source(index)) for index in range(4)
    )
    snapshots = tuple(load_config_authoring_definitions(config) for config in configs)

    def invoke(snapshot: AuthoringDefinitionsSnapshot) -> int:
        return _preset_value(snapshot, "isolated")

    with ThreadPoolExecutor(max_workers=4) as executor:
        pending: tuple[Future[int], ...] = tuple(
            executor.submit(invoke, snapshot) for snapshot in snapshots
        )
        values = tuple(future.result() for future in reversed(pending))

    assert values == (3, 2, 1, 0)


def test_deleted_source_is_absent_only_from_new_snapshot(tmp_path: Path) -> None:
    config = _config(tmp_path, "deleted", _preset_source(9))
    first = load_config_authoring_definitions(config)
    (tmp_path / "deleted" / "candidate.py").unlink()
    second = load_config_authoring_definitions(config)

    assert _preset_value(first, "isolated") == 9
    assert "isolated" not in second.presets


def test_candidate_executes_snapshotted_source_instead_of_stale_bytecode(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, "fresh-source", _preset_source(1))
    source_path = tmp_path / "fresh-source" / "candidate.py"
    original_stat = source_path.stat()
    py_compile.compile(str(source_path), doraise=True)
    source_path.write_text(_preset_source(2), encoding="utf-8")
    os.utime(
        source_path,
        ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
    )

    snapshot = load_config_authoring_definitions(config)

    assert _preset_value(snapshot, "isolated") == 2


def test_pickled_recipe_executes_captured_bytes_without_live_disk_read(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, "worker-recipe", _preset_source(3))
    source_path = tmp_path / "worker-recipe" / "candidate.py"
    recipe = capture_authoring_definitions_recipe(config)
    source_path.write_text(_preset_source(99), encoding="utf-8")

    restored_recipe = pickle.loads(pickle.dumps(recipe))
    snapshot = load_authoring_definitions_recipe(restored_recipe)

    assert _preset_value(snapshot, "isolated") == 3
    assert snapshot.recipe == restored_recipe


def test_same_sources_in_other_checkout_keep_operation_fingerprint(
    tmp_path: Path,
) -> None:
    source = (
        "from grafix.api import primitive\n"
        "@primitive(meta={})\n"
        "def loader_stable_primitive():\n"
        "    return ((), ())\n"
    )
    first = load_config_authoring_definitions(_config(tmp_path, "checkout-a", source))
    second = load_config_authoring_definitions(_config(tmp_path, "checkout-b", source))

    first_entry = first.operations.resolve("primitive", "loader_stable_primitive")
    second_entry = second.operations.resolve("primitive", "loader_stable_primitive")
    assert first_entry.evaluation_fingerprint == second_entry.evaluation_fingerprint
    assert first_entry.schema_fingerprint == second_entry.schema_fingerprint
    assert first_entry.evaluator.__module__ == second_entry.evaluator.__module__


def test_relative_helper_import_is_isolated_and_removed_from_sys_modules(
    tmp_path: Path,
) -> None:
    root = tmp_path / "relative"
    root.mkdir()
    (root / "helper.py").write_text(
        "def value():\n    return 17\n",
        encoding="utf-8",
    )
    (root / "candidate.py").write_text(
        "from .helper import value\n"
        "from grafix.api import preset\n"
        "from grafix.core.geometry import Geometry\n"
        "@preset(meta={})\n"
        "def relative_helper_preset():\n"
        "    return Geometry.create(op='concat', params={'value': value()})\n",
        encoding="utf-8",
    )

    snapshot = load_config_authoring_definitions(_config_for_dirs(tmp_path, "relative", (root,)))

    assert _preset_value(snapshot, "relative_helper_preset") == 17
    assert not any(name.startswith("_grafix_config_authoring_") for name in sys.modules)


@pytest.mark.parametrize(
    ("source", "scope"),
    [
        (
            "def load_later():\n"
            "    from .helper import value\n"
            "    return value\n",
            "function 'load_later'",
        ),
        (
            "async def load_later():\n"
            "    from .helper import value\n"
            "    return value\n",
            "async function 'load_later'",
        ),
        (
            "class Deferred:\n"
            "    from .helper import value\n",
            "class 'Deferred'",
        ),
    ],
)
def test_candidate_preflight_rejects_deferred_relative_import_with_location(
    tmp_path: Path,
    source: str,
    scope: str,
) -> None:
    root = tmp_path / "deferred"
    root.mkdir()
    candidate_path = root / "candidate.py"
    candidate_path.write_text(source, encoding="utf-8")
    config = _config_for_dirs(tmp_path, "deferred", (root,))

    with pytest.raises(SourceImportPolicyError) as caught:
        load_config_authoring_definitions(config)

    assert caught.value.filename == str(candidate_path)
    assert caught.value.lineno == 2
    assert scope in str(caught.value)
    assert "module lexical scope" in str(caught.value)
    assert not any(name.startswith("_grafix_config_authoring_") for name in sys.modules)


def test_candidate_preflight_finishes_before_any_candidate_executes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "all-source-preflight"
    root.mkdir()
    events: list[str] = []
    monkeypatch.setattr(
        builtins,
        "_grafix_test_authoring_preflight_events",
        events,
        raising=False,
    )
    (root / "a_side_effect.py").write_text(
        "import builtins\n"
        "builtins._grafix_test_authoring_preflight_events.append('executed')\n",
        encoding="utf-8",
    )
    invalid_path = root / "z_invalid.py"
    invalid_path.write_text(
        "def load_later():\n"
        "    from .missing_helper import value\n"
        "    return value\n",
        encoding="utf-8",
    )
    meta_path_before = tuple(sys.meta_path)

    with pytest.raises(SourceImportPolicyError, match="z_invalid.py:2"):
        load_config_authoring_definitions(
            _config_for_dirs(tmp_path, "all-source-preflight", (root,))
        )

    assert events == []
    assert tuple(sys.meta_path) == meta_path_before
    assert not any(name.startswith("_grafix_config_authoring_") for name in sys.modules)


def test_module_scope_relative_import_inside_control_flow_is_supported(
    tmp_path: Path,
) -> None:
    root = tmp_path / "module-control-flow"
    root.mkdir()
    (root / "helper.py").write_text("VALUE = 23\n", encoding="utf-8")
    (root / "candidate.py").write_text(
        "if True:\n"
        "    from .helper import VALUE\n"
        "from grafix.api import preset\n"
        "from grafix.core.geometry import Geometry\n"
        "@preset(meta={})\n"
        "def module_control_flow_preset():\n"
        "    return Geometry.create(op='concat', params={'value': VALUE})\n",
        encoding="utf-8",
    )

    snapshot = load_config_authoring_definitions(
        _config_for_dirs(tmp_path, "module-control-flow", (root,))
    )

    assert _preset_value(snapshot, "module_control_flow_preset") == 23


def test_filesystem_capture_rejects_root_initializer(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root-initializer"
    root.mkdir()
    initializer_path = root / "__init__.py"
    initializer_path.write_text("", encoding="utf-8")
    config = _config_for_dirs(tmp_path, "root-initializer", (root,))

    with pytest.raises(
        ValueError,
        match=r"synthetic namespace.*root __init__\.py",
    ) as caught:
        capture_authoring_definitions_recipe(config)

    assert str(initializer_path) in str(caught.value)


def test_pickled_recipe_cannot_bypass_root_initializer_rejection(
    tmp_path: Path,
) -> None:
    root = tmp_path / "restored-root-initializer"
    initializer_path = root / "__init__.py"
    recipe = AuthoringDefinitionsRecipe(
        roots=(
            AuthoringSourceRoot(
                path=root,
                modules=(
                    AuthoringModuleSource(
                        relative_path=Path("__init__.py"),
                        content=b"",
                    ),
                ),
            ),
        )
    )
    restored = pickle.loads(pickle.dumps(recipe))

    with pytest.raises(ValueError, match=r"root __init__\.py") as caught:
        load_authoring_definitions_recipe(restored)

    assert str(initializer_path) in str(caught.value)
    assert not any(name.startswith("_grafix_config_authoring_") for name in sys.modules)


def test_pickled_recipe_cannot_bypass_deferred_import_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "restored-deferred"
    events: list[str] = []
    monkeypatch.setattr(
        builtins,
        "_grafix_test_restored_preflight_events",
        events,
        raising=False,
    )
    recipe = AuthoringDefinitionsRecipe(
        roots=(
            AuthoringSourceRoot(
                path=root,
                modules=(
                    AuthoringModuleSource(
                        relative_path=Path("a_side_effect.py"),
                        content=(
                            b"import builtins\n"
                            b"builtins._grafix_test_restored_preflight_events"
                            b".append('executed')\n"
                        ),
                    ),
                    AuthoringModuleSource(
                        relative_path=Path("z_invalid.py"),
                        content=(
                            b"def load_later():\n"
                            b"    from .missing import VALUE\n"
                            b"    return VALUE\n"
                        ),
                    ),
                ),
            ),
        )
    )
    restored = pickle.loads(pickle.dumps(recipe))

    with pytest.raises(SourceImportPolicyError, match=r"z_invalid\.py:2"):
        load_authoring_definitions_recipe(restored)

    assert events == []
    assert not any(name.startswith("_grafix_config_authoring_") for name in sys.modules)


def test_nested_initializer_executes_once_and_can_publish_declarations(
    tmp_path: Path,
) -> None:
    root = tmp_path / "nested-initializer"
    package = root / "package"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        "from grafix.api import preset, primitive\n"
        "from grafix.core.geometry import Geometry\n"
        "OFFSET = 29\n"
        "@primitive(meta={})\n"
        "def nested_initializer_operation():\n"
        "    return ((), ())\n"
        "@preset(meta={})\n"
        "def nested_initializer_preset():\n"
        "    return Geometry.create(op='concat', params={'value': OFFSET})\n",
        encoding="utf-8",
    )
    (package / "child.py").write_text(
        "from . import OFFSET\n"
        "from grafix.api import preset\n"
        "from grafix.core.geometry import Geometry\n"
        "@preset(meta={})\n"
        "def nested_initializer_child_preset():\n"
        "    return Geometry.create(op='concat', params={'value': OFFSET + 1})\n",
        encoding="utf-8",
    )

    snapshot = load_config_authoring_definitions(
        _config_for_dirs(tmp_path, "nested-initializer", (root,))
    )

    assert snapshot.operations.resolve("primitive", "nested_initializer_operation")
    assert _preset_value(snapshot, "nested_initializer_preset") == 29
    assert _preset_value(snapshot, "nested_initializer_child_preset") == 30
    assert not any(name.startswith("_grafix_config_authoring_") for name in sys.modules)


def test_nested_package_and_namespace_relative_imports_share_one_snapshot(
    tmp_path: Path,
) -> None:
    root = tmp_path / "nested"
    (root / "package").mkdir(parents=True)
    (root / "namespace" / "deep").mkdir(parents=True)
    (root / "package" / "__init__.py").write_text(
        "OFFSET = 4\n",
        encoding="utf-8",
    )
    (root / "package" / "helper.py").write_text(
        "from . import OFFSET\n\ndef package_value():\n    return OFFSET + 5\n",
        encoding="utf-8",
    )
    (root / "namespace" / "deep" / "helper.py").write_text(
        "def namespace_value():\n    return 8\n",
        encoding="utf-8",
    )
    (root / "candidate.py").write_text(
        "from .package.helper import package_value\n"
        "from .namespace.deep.helper import namespace_value\n"
        "from grafix.api import preset\n"
        "from grafix.core.geometry import Geometry\n"
        "@preset(meta={})\n"
        "def nested_import_preset():\n"
        "    return Geometry.create(\n"
        "        op='concat',\n"
        "        params={'value': package_value() + namespace_value()},\n"
        "    )\n",
        encoding="utf-8",
    )

    snapshot = load_config_authoring_definitions(
        _config_for_dirs(tmp_path, "nested", (root,))
    )

    assert _preset_value(snapshot, "nested_import_preset") == 17
    assert not any(name.startswith("_grafix_config_authoring_") for name in sys.modules)


@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, SystemExit])
def test_process_control_during_candidate_import_restores_global_import_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exception_type: type[BaseException],
) -> None:
    process_control = exception_type("stop")
    monkeypatch.setattr(
        builtins,
        "_grafix_test_authoring_process_control",
        process_control,
        raising=False,
    )
    config = _config(
        tmp_path,
        f"process-control-{exception_type.__name__}",
        "import builtins\nraise builtins._grafix_test_authoring_process_control\n",
    )
    meta_path_before = tuple(sys.meta_path)

    with pytest.raises(exception_type, match="stop") as caught:
        load_config_authoring_definitions(config)

    assert caught.value is process_control
    assert tuple(sys.meta_path) == meta_path_before
    assert not any(name.startswith("_grafix_config_authoring_") for name in sys.modules)


def test_initial_authoring_and_source_reload_share_one_import_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_started = Event()
    release_config = Event()
    reload_started = Event()

    def block_config() -> None:
        config_started.set()
        if not release_config.wait(timeout=5.0):
            raise TimeoutError("test did not release config candidate")

    monkeypatch.setattr(
        builtins,
        "_grafix_test_block_config_authoring",
        block_config,
        raising=False,
    )
    monkeypatch.setattr(
        builtins,
        "_grafix_test_note_source_reload",
        reload_started.set,
        raising=False,
    )
    config = _config(
        tmp_path,
        "shared-lock",
        "import builtins\nbuiltins._grafix_test_block_config_authoring()\n",
    )
    source_path = tmp_path / "sketch.py"
    source_path.write_text(
        "import builtins\n"
        "builtins._grafix_test_note_source_reload()\n\n"
        "def draw(t):\n"
        "    return ()\n",
        encoding="utf-8",
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        config_future = executor.submit(load_config_authoring_definitions, config)
        assert config_started.wait(timeout=5.0)
        reload_future = executor.submit(SourceReloadController, source_path)
        try:
            assert not reload_started.wait(timeout=0.1)
        finally:
            release_config.set()
        config_future.result(timeout=5.0)
        controller = reload_future.result(timeout=5.0)
    try:
        assert reload_started.is_set()
    finally:
        controller.close()


def test_candidate_declarations_never_reach_default_authoring(
    tmp_path: Path,
) -> None:
    preset_name = "candidate_default_leak_contract"
    primitive_name = "candidate_operation_default_leak_contract"
    source = (
        "from grafix.api import preset, primitive\n"
        "from grafix.core.geometry import Geometry\n"
        "@preset(meta={})\n"
        f"def {preset_name}():\n"
        "    return Geometry.create(op='concat')\n"
        "@primitive(meta={})\n"
        f"def {primitive_name}():\n"
        "    return ((), ())\n"
    )
    before = default_session_authoring_definitions()

    candidate = load_config_authoring_definitions(_config(tmp_path, "leak", source))
    after = default_session_authoring_definitions()

    assert preset_name in candidate.presets
    assert candidate.operations.resolve("primitive", primitive_name)
    assert preset_name not in before.presets
    assert preset_name not in after.presets
    assert ("primitive", primitive_name) not in before.operations
    assert ("primitive", primitive_name) not in after.operations


def test_duplicate_candidate_discards_every_candidate_declaration(
    tmp_path: Path,
) -> None:
    root = tmp_path / "duplicate-candidate"
    root.mkdir()
    (root / "a.py").write_text(
        _preset_source(1, name="candidate_before_duplicate"),
        encoding="utf-8",
    )
    (root / "b.py").write_text(
        _preset_source(2, name="candidate_before_duplicate"),
        encoding="utf-8",
    )
    seed = default_session_authoring_definitions()

    with pytest.raises(ValueError, match="既に登録"):
        load_config_authoring_definitions(
            _config_for_dirs(tmp_path, "duplicate-candidate", (root,)),
            seed=seed,
        )

    assert "candidate_before_duplicate" not in seed.presets
    assert "candidate_before_duplicate" not in default_session_authoring_definitions().presets
    assert not any(name.startswith("_grafix_config_authoring_") for name in sys.modules)


def test_operation_fingerprint_is_stable_across_root_index_and_import_order(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first-root"
    second_root = tmp_path / "second-root"
    first_other_root = tmp_path / "first-other-root"
    second_other_root = tmp_path / "second-other-root"
    for root in (first_root, second_root, first_other_root, second_other_root):
        root.mkdir()
    helper_source = "def offset(value):\n    return value + 3\n"
    operation_source = (
        "from .helper import offset\n"
        "from grafix.api import primitive\n"
        "@primitive(meta={})\n"
        "def candidate_stable_operation():\n"
        "    return ((), offset(()))\n"
    )
    for root in (first_root, second_root):
        (root / "helper.py").write_text(helper_source, encoding="utf-8")
        (root / "operation.py").write_text(operation_source, encoding="utf-8")
    unrelated_source = _preset_source(41, name="candidate_unrelated_preset")
    for root in (first_other_root, second_other_root):
        (root / "unrelated.py").write_text(unrelated_source, encoding="utf-8")

    first = load_config_authoring_definitions(
        _config_for_dirs(tmp_path, "root-order-a", (first_other_root, first_root))
    )
    second = load_config_authoring_definitions(
        _config_for_dirs(tmp_path, "root-order-b", (second_root, second_other_root))
    )

    first_entry = first.operations.resolve("primitive", "candidate_stable_operation")
    second_entry = second.operations.resolve("primitive", "candidate_stable_operation")
    assert first_entry.evaluation_fingerprint == second_entry.evaluation_fingerprint
    assert first_entry.schema_fingerprint == second_entry.schema_fingerprint
    assert _preset_value(first, "candidate_unrelated_preset") == 41
    assert _preset_value(second, "candidate_unrelated_preset") == 41
    assert not any(name.startswith("_grafix_config_authoring_") for name in sys.modules)


def test_dynamic_operation_owner_is_stable_across_candidate_hash_and_root_index(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "dynamic-first"
    second_root = tmp_path / "dynamic-second"
    first_unrelated = tmp_path / "dynamic-unrelated-first"
    second_unrelated = tmp_path / "dynamic-unrelated-second"
    for root in (first_root, second_root, first_unrelated, second_unrelated):
        root.mkdir()
    operation_source = (
        "from grafix.api import primitive\n"
        "@primitive(meta={}, cache_policy='none', version='stable-v1')\n"
        "def candidate_dynamic_operation():\n"
        "    return ((), ())\n"
    )
    for root in (first_root, second_root):
        (root / "operation.py").write_text(operation_source, encoding="utf-8")
    (first_unrelated / "unrelated.py").write_text(
        _preset_source(1, name="dynamic_unrelated"),
        encoding="utf-8",
    )
    (second_unrelated / "unrelated.py").write_text(
        _preset_source(2, name="dynamic_unrelated"),
        encoding="utf-8",
    )

    first = load_config_authoring_definitions(
        _config_for_dirs(
            tmp_path,
            "dynamic-owner-a",
            (first_unrelated, first_root),
        )
    )
    second = load_config_authoring_definitions(
        _config_for_dirs(
            tmp_path,
            "dynamic-owner-b",
            (second_root, second_unrelated),
        )
    )

    first_entry = first.operations.resolve("primitive", "candidate_dynamic_operation")
    second_entry = second.operations.resolve("primitive", "candidate_dynamic_operation")
    assert first_entry.declaration.source_owner == "_grafix_config_authoring.operation"
    assert second_entry.declaration.source_owner == "_grafix_config_authoring.operation"
    assert first_entry.ref == second_entry.ref
    assert first_entry.schema_fingerprint == second_entry.schema_fingerprint
    with bind_operation_catalog(first.operations):
        first_geometry = G.candidate_dynamic_operation()
    with bind_operation_catalog(second.operations):
        second_geometry = G.candidate_dynamic_operation()
    assert first_geometry.id == second_geometry.id


def test_candidate_fingerprint_uses_executed_snapshot_bytes_after_disk_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_root = tmp_path / "digest-first"
    second_root = tmp_path / "digest-second"
    for root in (first_root, second_root):
        root.mkdir()
    mutator_source = (
        "import os\n"
        "from pathlib import Path\n"
        "Path(__file__).with_name('helper.py').write_text(\n"
        "    os.environ['GRAFIX_TEST_HELPER_REPLACEMENT'], encoding='utf-8'\n"
        ")\n"
    )
    helper_source = (
        "from grafix.api import primitive\n"
        "@primitive(meta={})\n"
        "def candidate_snapshot_digest_operation():\n"
        "    return ((), ())\n"
    )
    for root in (first_root, second_root):
        (root / "a_mutator.py").write_text(mutator_source, encoding="utf-8")
        (root / "helper.py").write_text(helper_source, encoding="utf-8")

    monkeypatch.setenv(
        "GRAFIX_TEST_HELPER_REPLACEMENT",
        "REPLACED_AFTER_SNAPSHOT = 1\n",
    )
    first = load_config_authoring_definitions(
        _config_for_dirs(tmp_path, "digest-a", (first_root,))
    )
    monkeypatch.setenv(
        "GRAFIX_TEST_HELPER_REPLACEMENT",
        "REPLACED_AFTER_SNAPSHOT = 2\n",
    )
    second = load_config_authoring_definitions(
        _config_for_dirs(tmp_path, "digest-b", (second_root,))
    )

    first_entry = first.operations.resolve(
        "primitive",
        "candidate_snapshot_digest_operation",
    )
    second_entry = second.operations.resolve(
        "primitive",
        "candidate_snapshot_digest_operation",
    )
    assert first_entry.ref == second_entry.ref
    assert first_entry.schema_fingerprint == second_entry.schema_fingerprint
    assert first_entry.declaration.source_owner == "_grafix_config_authoring.helper"
    assert second_entry.declaration.source_owner == "_grafix_config_authoring.helper"
