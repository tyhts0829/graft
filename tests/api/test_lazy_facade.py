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


def test_root_facade_caches_public_identity_and_keeps_runner_deferred() -> None:
    result = _run_isolated(
        r'''
import json
import sys

import grafix

api_before = "grafix.api" in sys.modules
first_g = grafix.G
import grafix.api as api
root_api_identity = first_g is api.G and grafix.G is first_g
heavy_groups_after_g = {
    name: name in sys.modules
    for name in (
        "grafix.api.render",
        "grafix.api.export",
        "grafix.api.variation_batch",
    )
}
runner_before = "grafix.api.runner" in sys.modules
root_run = grafix.run
runner_after = "grafix.api.runner" in sys.modules
all_names_resolve = all(hasattr(grafix, name) for name in grafix.__all__)
star_namespace = {}
exec("from grafix import *", star_namespace)
star_surface_matches = all(
    star_namespace[name] is getattr(grafix, name) for name in grafix.__all__
)
print(json.dumps({
    "api_before": api_before,
    "root_api_identity": root_api_identity,
    "heavy_groups_after_g": heavy_groups_after_g,
    "run_identity": root_run is api.run,
    "runner_before": runner_before,
    "runner_after": runner_after,
    "all_names_resolve": all_names_resolve,
    "star_surface_matches": star_surface_matches,
}))
'''
    )

    assert result == {
        "api_before": False,
        "root_api_identity": True,
        "heavy_groups_after_g": {
            "grafix.api.render": False,
            "grafix.api.export": False,
            "grafix.api.variation_batch": False,
        },
        "run_identity": True,
        "runner_before": False,
        "runner_after": False,
        "all_names_resolve": True,
        "star_surface_matches": True,
    }


def test_root_callable_wins_when_same_named_submodule_was_loaded_first() -> None:
    result = _run_isolated(
        r'''
import importlib
import json

import grafix

importlib.import_module("grafix.export.capture")
from grafix import export
import grafix.api as api

cc_module = importlib.import_module("grafix.cc")
from grafix import cc

print(json.dumps({
    "export_callable": callable(export),
    "export_identity": export is api.export and grafix.export is api.export,
    "cc_identity": cc is cc_module.cc and grafix.cc is cc_module.cc,
}))
'''
    )

    assert result == {
        "export_callable": True,
        "export_identity": True,
        "cc_identity": True,
    }
