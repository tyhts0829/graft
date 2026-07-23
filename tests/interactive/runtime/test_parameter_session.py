from __future__ import annotations

from pathlib import Path

from grafix.core.geometry import Geometry
from grafix.core.operation_catalog import OperationCatalog
from grafix.core.operation_schema import ParameterOpSchema
from grafix.core.parameters.meta import ParamMeta
from grafix.core.preset_catalog import PresetCatalog, PresetDeclaration
from grafix.interactive.runtime.parameter_session import (
    ParameterSession,
    known_operation_schema_snapshot,
)


def _empty_preset(*_args: object, **_kwargs: object) -> Geometry:
    return Geometry.create(op="concat")


def test_known_operation_snapshot_includes_canonical_preset_operation() -> None:
    declaration = PresetDeclaration(
        name="sample",
        func=_empty_preset,
        invoker=_empty_preset,
        schema=ParameterOpSchema(
            meta={
                "activate": ParamMeta(kind="bool"),
                "amount": ParamMeta(kind="float"),
            },
            defaults={"activate": True, "amount": 1.0},
            param_order=("activate", "amount"),
            ui_visible={},
        ),
    )

    snapshot = known_operation_schema_snapshot(
        OperationCatalog({}),
        PresetCatalog({"sample": declaration}),
    )

    assert snapshot.args_for("preset.sample") == frozenset({"activate", "amount"})
    assert snapshot.args_for("sample") is None


def test_parameter_session_source_distinguishes_code_and_saved(tmp_path: Path) -> None:
    empty = known_operation_schema_snapshot(
        OperationCatalog({}),
        PresetCatalog({}),
    )
    code = ParameterSession(
        primary_path=None,
        gui_enabled=False,
        known_operations=empty,
    )
    saved = ParameterSession(
        primary_path=tmp_path / "params.json",
        gui_enabled=False,
        known_operations=empty,
    )

    assert code.capture_state().source == "code"
    assert saved.capture_state().source == "saved"


def test_parameter_session_replaces_finalize_schema_only_after_generation_acceptance() -> None:
    initial = known_operation_schema_snapshot(
        OperationCatalog({}),
        PresetCatalog({}),
    )
    replacement_declaration = PresetDeclaration(
        name="reloaded",
        func=_empty_preset,
        invoker=_empty_preset,
        schema=ParameterOpSchema(
            meta={"amount": ParamMeta(kind="float")},
            defaults={"amount": 1.0},
            param_order=("amount",),
            ui_visible={},
        ),
    )
    replacement = known_operation_schema_snapshot(
        OperationCatalog({}),
        PresetCatalog({"reloaded": replacement_declaration}),
    )
    session = ParameterSession(
        primary_path=None,
        gui_enabled=False,
        known_operations=initial,
    )

    session.replace_known_operations(replacement)

    assert session.known_operations is replacement
    assert session.known_operations.args_for("preset.reloaded") == frozenset({"amount"})
