from __future__ import annotations

import os
import pickle
import py_compile
import sys
from concurrent.futures import Future
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from grafix import G, P
from grafix.api import preset
from grafix.core.authoring_definitions import (
    AuthoringDefinitionsSnapshot,
    RegistrationTarget,
    registration_scope,
)
from grafix.core.authoring_loader import (
    capture_authoring_definitions_recipe,
    default_session_authoring_definitions,
    load_authoring_definitions_recipe,
    load_config_authoring_definitions,
)
from grafix.core.preset_catalog import bind_preset_catalog
from grafix.core.geometry import Geometry
from grafix.core.operation_catalog import bind_operation_catalog
from grafix.core.runtime_config import RuntimeConfig
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
