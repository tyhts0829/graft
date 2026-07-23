"""Parameter GUI が immutable catalog projection だけを読む契約を検証する。"""

from __future__ import annotations

import ast
from pathlib import Path

from grafix.api import preset
from grafix.core.authoring_definitions import RegistrationTarget, registration_scope
from grafix.core.operation_authoring import effect
from grafix.core.geometry import Geometry
from grafix.core.operation_schema import ParameterOpSchema
from grafix.core.operation_selector import PRIMITIVE_SELECTOR_OP, effect_selector_op
from grafix.core.operation_authoring import primitive
from grafix.interactive.parameter_gui.catalog import ParameterGuiCatalog


def _catalog() -> ParameterGuiCatalog:
    target = RegistrationTarget()
    with registration_scope(target):

        @primitive(meta={"amount": {"kind": "float"}})
        def gui_catalog_shape(*, amount: float = 1.0):
            _ = amount
            return [], []

        @effect(meta={"gain": {"kind": "float"}}, n_inputs=2)
        def gui_catalog_effect(first, second, *, gain: float = 1.0):
            _ = second, gain
            return first

        @preset(meta={"count": {"kind": "int"}})
        def gui_catalog_preset(*, count: int = 1) -> Geometry:
            _ = count
            return Geometry.create(op="concat")

    snapshot = target.snapshot()
    return ParameterGuiCatalog.capture(snapshot.operations, snapshot.presets)


def test_catalog_projects_schema_without_exposing_evaluators() -> None:
    catalog = _catalog()

    primitive_entry = catalog.resolve_operation("primitive", "gui_catalog_shape")
    effect_entry = catalog.resolve_operation("effect", "gui_catalog_effect")
    preset_entry = catalog.resolve("preset.gui_catalog_preset")

    assert primitive_entry.schema.defaults["amount"] == 1.0
    assert effect_entry.n_inputs == 2
    assert preset_entry is not None
    assert preset_entry.call_name == "gui_catalog_preset"
    assert type(preset_entry.schema) is ParameterOpSchema
    assert preset_entry.schema.defaults == {"activate": True, "count": 1}
    assert not hasattr(primitive_entry, "evaluator")
    assert not hasattr(effect_entry, "evaluator")


def test_catalog_contains_selector_schema_but_no_selector_evaluator() -> None:
    catalog = _catalog()

    primitive_selector = catalog.resolve(PRIMITIVE_SELECTOR_OP)
    effect_selector = catalog.resolve(effect_selector_op(2))

    assert primitive_selector is not None
    assert primitive_selector.kind == "selector"
    assert primitive_selector.schema.meta["target"].choices == ("gui_catalog_shape",)
    assert effect_selector is not None
    assert effect_selector.schema.meta["target"].choices == ("gui_catalog_effect",)
    assert not hasattr(primitive_selector, "evaluator")


def test_gui_catalog_consumers_do_not_import_legacy_live_registries() -> None:
    root = Path(__file__).parents[3]
    targets = (
        root / "src/grafix/interactive/parameter_gui/table_view.py",
        root / "src/grafix/interactive/parameter_gui/table_commit.py",
        root / "src/grafix/interactive/parameter_gui/visibility.py",
        root / "src/grafix/interactive/parameter_gui/snippet.py",
        root / "src/grafix/interactive/parameter_gui/table.py",
        root / "src/grafix/interactive/parameter_gui/grouping.py",
        root / "src/grafix/devtools/describe_op.py",
        root / "src/grafix/devtools/list_builtins.py",
        root / "src/grafix/devtools/generate_stub.py",
    )
    forbidden = {
        "grafix.core.effect_registry",
        "grafix.core.primitive_registry",
        "grafix.core.preset_registry",
    }

    for path in targets:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        assert not imports & forbidden, path
