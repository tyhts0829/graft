"""P namespace と immutable PresetCatalog の選択規則を検証する。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from grafix import P
from grafix.api import preset
from grafix.core.authoring_definitions import RegistrationTarget, registration_scope
from grafix.core.geometry import Geometry
from grafix.core.parameters import ParamStore
from grafix.core.parameters.context import parameter_context
from grafix.core.parameters.snapshot_ops import store_snapshot
from grafix.core.preset_catalog import bind_preset_catalog, preset_declaration
from grafix.core.runtime_config import bind_runtime_config
from grafix.runtime_config_loader import load_runtime_config


def _write_config(*, path: Path, preset_module_dir: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "version: 1",
                "paths:",
                '  output_dir: "data/output"',
                "  preset_module_dirs:",
                f'    - "{preset_module_dir.as_posix()}"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_preset_namespace_does_not_implicitly_autoload_config_modules(
    tmp_path: Path,
) -> None:
    preset_dir = tmp_path / "presets"
    preset_dir.mkdir(parents=True)
    (preset_dir / "implicit.py").write_text(
        "from grafix.api import preset\n"
        "from grafix.core.geometry import Geometry\n"
        "@preset(meta={})\n"
        "def implicit_catalog_preset():\n"
        "    return Geometry.create(op='concat')\n",
        encoding="utf-8",
    )
    cfg_path = tmp_path / "config.yaml"
    _write_config(path=cfg_path, preset_module_dir=preset_dir)

    with bind_runtime_config(load_runtime_config(cfg_path)):
        with pytest.raises(AttributeError, match="implicit_catalog_preset"):
            _ = P.implicit_catalog_preset


def test_explicit_module_load_registers_only_into_the_scoped_candidate(
    tmp_path: Path,
) -> None:
    module_path = tmp_path / "explicit.py"
    module_path.write_text(
        "from grafix.api import preset\n"
        "from grafix.core.geometry import Geometry\n"
        "@preset(meta={'x': {'kind': 'float'}})\n"
        "def explicit_catalog_preset(*, x: float = 1.0):\n"
        "    return Geometry.create(op='concat', params={'x': x})\n",
        encoding="utf-8",
    )
    spec = importlib.util.spec_from_file_location("explicit_catalog_preset_module", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    target = RegistrationTarget()
    with registration_scope(target):
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop(spec.name, None)

    with pytest.raises(AttributeError, match="explicit_catalog_preset"):
        _ = P.explicit_catalog_preset
    with bind_preset_catalog(target.snapshot().presets):
        out = P.explicit_catalog_preset(x=2.0)
    assert dict(out.args)["x"] == 2.0


def test_preset_namespace_supports_p_call_name_without_signature() -> None:
    @preset(meta={"x": {"kind": "float"}})
    def b_only_sample(*, x: float = 1.0) -> Geometry:
        return Geometry.create(op="concat", params={"x": float(x)})

    store = ParamStore()
    with parameter_context(store=store, cc_snapshot=None):
        out = P(name="Custom", key=1).b_only_sample(x=3.0)

    assert isinstance(out, Geometry)
    assert dict(out.args)["x"] == 3.0
    snap = store_snapshot(store)
    labels = {
        label
        for key, (_meta, _state, _ordinal, label) in snap.items()
        if key.op == "preset.b_only_sample"
    }
    assert labels == {"Custom"}
    site_ids = {key.site_id for key in snap if key.op == "preset.b_only_sample"}
    assert any(site_id.endswith("|int:1") for site_id in site_ids)


def test_preset_decorator_builds_one_complete_immutable_schema() -> None:
    target = RegistrationTarget()
    visible = {"mode": lambda values: values["count"] > 0}
    with registration_scope(target):

        @preset(
            meta={
                "mode": {"kind": "choice", "choices": ("line", "fill")},
                "count": {"kind": "int"},
            },
            ui_visible=visible,
        )
        def complete_preset_schema(
            *, count: int = 2, mode: str = "line"
        ) -> Geometry:
            _ = count, mode
            return Geometry.create(op="concat")

    declaration = preset_declaration(complete_preset_schema)
    assert target.snapshot().presets["complete_preset_schema"] is declaration
    assert tuple(declaration.schema.meta) == ("activate", "mode", "count")
    assert declaration.schema.defaults == {
        "activate": True,
        "mode": "line",
        "count": 2,
    }
    assert declaration.schema.param_order == ("activate", "count", "mode")
    assert declaration.schema.ui_visible["mode"](
        {"activate": True, "count": 1, "mode": "line"}
    )
    with pytest.raises(TypeError):
        declaration.schema.defaults["count"] = 3  # type: ignore[index]


def test_preset_decorator_validates_public_function_defaults() -> None:
    target = RegistrationTarget()
    with registration_scope(target):
        with pytest.raises(TypeError, match="int"):

            @preset(meta={"count": {"kind": "int"}})
            def invalid_preset_default(*, count: int = True) -> Geometry:
                _ = count
                return Geometry.create(op="concat")

    assert "invalid_preset_default" not in target.snapshot().presets


def test_preset_namespace_rejects_positional_identity() -> None:
    with pytest.raises(TypeError, match="positional"):
        P("Custom")  # type: ignore[misc]


@pytest.mark.parametrize("reserved_name", ["name", "key", "instance_key", "shared"])
def test_preset_namespace_rejects_identity_as_direct_preset_kwargs(
    reserved_name: str,
) -> None:
    target = RegistrationTarget()
    with registration_scope(target):

        @preset(meta={"x": {"kind": "float"}})
        def b_only_direct_rejection(*, x: float = 1.0) -> Geometry:
            return Geometry.create(op="concat", params={"x": float(x)})

    with bind_preset_catalog(target.snapshot().presets):
        with pytest.raises(
            TypeError,
            match=f"unexpected keyword argument '{reserved_name}'",
        ):
            P.b_only_direct_rejection(x=2.0, **{reserved_name: "reserved"})


@pytest.mark.parametrize("reserved_name", ["name", "key", "instance_key", "shared", "activate"])
def test_preset_rejects_every_reserved_name_in_original_signature(
    reserved_name: str,
) -> None:
    namespace: dict[str, object] = {}
    exec(
        "def invalid_reserved(*, " + reserved_name + "=None):\n"
        "    return Geometry.create(op='concat')\n",
        {"Geometry": Geometry},
        namespace,
    )
    with pytest.raises(ValueError, match=reserved_name):
        preset(meta={})(namespace["invalid_reserved"])  # type: ignore[arg-type]


def test_preset_duplicate_is_atomic_inside_one_candidate() -> None:
    target = RegistrationTarget()
    with registration_scope(target):

        @preset(meta={"amount": {"kind": "float"}})
        def duplicate_contract(amount: float = 1.0) -> Geometry:
            return Geometry.create(op="concat", params={"amount": amount})

    before = target.snapshot()

    def duplicate() -> Geometry:
        return Geometry.create(op="concat")

    duplicate.__name__ = "duplicate_contract"
    with registration_scope(target):
        with pytest.raises(ValueError, match=r"duplicate_contract.*既に登録"):
            preset(meta={})(duplicate)

    after = target.snapshot()
    assert after.presets["duplicate_contract"] is before.presets["duplicate_contract"]


def test_two_catalogs_can_own_the_same_preset_name() -> None:
    first = RegistrationTarget()
    second = RegistrationTarget()

    def scene_a() -> Geometry:
        return Geometry.create(op="concat", params={"value": 1})

    def scene_b() -> Geometry:
        return Geometry.create(op="concat", params={"value": 2})

    scene_a.__name__ = scene_b.__name__ = "same_catalog_preset"
    with registration_scope(first):
        preset(meta={})(scene_a)
    with registration_scope(second):
        preset(meta={})(scene_b)

    with bind_preset_catalog(first.snapshot().presets):
        first_value = dict(P.same_catalog_preset().args)["value"]
    with bind_preset_catalog(second.snapshot().presets):
        second_value = dict(P.same_catalog_preset().args)["value"]
    assert (first_value, second_value) == (1, 2)


def test_preset_namespace_unknown_error_is_stable() -> None:
    with pytest.raises(
        AttributeError,
        match=r"^未登録の preset: 'missing_contract'$",
    ):
        _ = P.missing_contract


def test_preset_decorator_rejects_empty_callable_name() -> None:
    def unnamed() -> Geometry:
        return Geometry.create(op="concat")

    unnamed.__name__ = ""
    with pytest.raises(ValueError, match="空でない文字列"):
        preset(meta={})(unnamed)
