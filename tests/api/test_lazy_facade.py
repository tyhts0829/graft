from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _run_isolated(script: str) -> dict[str, object]:
    environment = dict(os.environ)
    source_root = Path(__file__).resolve().parents[2] / "src"
    environment["PYTHONPATH"] = str(source_root)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    return json.loads(completed.stdout)


def test_core_import_does_not_initialize_outer_capabilities() -> None:
    result = _run_isolated(
        r'''
import json
import sys

import grafix.core.geometry

watched = (
    "grafix.api",
    "grafix.export",
    "grafix.parameter_storage",
    "grafix.runtime_config_loader",
)
print(json.dumps({name: name in sys.modules for name in watched}))
'''
    )

    assert result == {
        "grafix.api": False,
        "grafix.export": False,
        "grafix.parameter_storage": False,
        "grafix.runtime_config_loader": False,
    }


def test_root_facade_has_stable_identity_and_defers_interactive_runtime() -> None:
    result = _run_isolated(
        r'''
import inspect
import json
import sys
from types import ModuleType

import grafix

api_before = "grafix.api" in sys.modules
first_g = grafix.G
import grafix.api as api
root_api_identity = first_g is api.G and grafix.G is first_g
root_run = grafix.run
runner_run = __import__("grafix.api.runner", fromlist=["run"]).run
star_namespace = {}
exec("from grafix import *", star_namespace)

print(json.dumps({
    "api_before": api_before,
    "root_type": type(grafix) is ModuleType,
    "api_type": type(api) is ModuleType,
    "root_api_identity": root_api_identity,
    "run_identity": root_run is runner_run,
    "run_signature": str(inspect.signature(root_run)),
    "pyglet_loaded": "pyglet" in sys.modules,
    "interactive_loaded": any(
        name == "grafix.interactive" or name.startswith("grafix.interactive.")
        for name in sys.modules
    ),
    "all_names_resolve": all(hasattr(grafix, name) for name in grafix.__all__),
    "star_surface_matches": all(
        star_namespace[name] is getattr(grafix, name)
        for name in grafix.__all__
    ),
}))
'''
    )

    assert result["api_before"] is False
    assert result["root_type"] is True
    assert result["api_type"] is True
    assert result["root_api_identity"] is True
    assert result["run_identity"] is True
    assert "*args" not in str(result["run_signature"])
    assert "**kwargs" not in str(result["run_signature"])
    assert result["pyglet_loaded"] is False
    assert result["interactive_loaded"] is False
    assert result["all_names_resolve"] is True
    assert result["star_surface_matches"] is True


def test_standard_module_import_matrix_and_root_callables_coexist() -> None:
    result = _run_isolated(
        r'''
import importlib
import json
from types import ModuleType

import grafix
import grafix.api as api

root_render = grafix.render
root_save = grafix.save
root_cc = grafix.cc

import grafix.export as export_package
import grafix.export.variation_batch as nested_export_module
import grafix.api.render as render_module
import grafix.api.export as save_module
import grafix.api.cc as cc_module

print(json.dumps({
    "export_package": (
        export_package is importlib.import_module("grafix.export")
        and type(export_package) is ModuleType
    ),
    "nested_export": (
        nested_export_module
        is importlib.import_module("grafix.export.variation_batch")
    ),
    "api_render_module": (
        render_module is importlib.import_module("grafix.api.render")
        and type(render_module) is ModuleType
    ),
    "api_export_module": (
        save_module is importlib.import_module("grafix.api.export")
        and type(save_module) is ModuleType
    ),
    "root_render_stable": grafix.render is root_render is render_module.render,
    "root_save_stable": grafix.save is root_save is save_module.save,
    "root_cc_stable": grafix.cc is root_cc is cc_module.cc,
    "api_application_names_absent": all(
        name not in api.__all__
        for name in ("render", "save", "run", "render_variation_batch")
    ),
}))
'''
    )

    assert result == {
        "export_package": True,
        "nested_export": True,
        "api_render_module": True,
        "api_export_module": True,
        "root_render_stable": True,
        "root_save_stable": True,
        "root_cc_stable": True,
        "api_application_names_absent": True,
    }


def test_root_callables_resolve_after_definition_modules_were_loaded_first() -> None:
    result = _run_isolated(
        r'''
import json

import grafix
import grafix.api.cc as cc_module
import grafix.api.export as save_module
import grafix.api.render as render_module
import grafix.api.runner as runner_module
import grafix.api.variation_batch as batch_module
import grafix.export

print(json.dumps({
    "render": grafix.render is render_module.render,
    "save": grafix.save is save_module.save,
    "run": grafix.run is runner_module.run,
    "cc": grafix.cc is cc_module.cc,
    "batch": grafix.render_variation_batch is batch_module.render_variation_batch,
    "export_is_package": grafix.export.__name__ == "grafix.export",
}))
'''
    )

    assert result == {
        "render": True,
        "save": True,
        "run": True,
        "cc": True,
        "batch": True,
        "export_is_package": True,
    }
