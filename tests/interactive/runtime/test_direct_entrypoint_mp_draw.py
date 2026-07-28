"""direct Python entrypoint と spawn worker の operation identity を検証する。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DIRECT_ENTRYPOINT_SOURCE = """\
from __future__ import annotations

import json
import multiprocessing as mp
import time
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from grafix import G, primitive
from grafix.authoring_loader import default_session_authoring_definitions
from grafix.core.geometry import Geometry
from grafix.core.layer import LayerStyleDefaults
from grafix.core.parameters import ParamStore
from grafix.interactive.runtime.perf import PerfCollector
from grafix.interactive.runtime.scene_runner import SceneRunner
from grafix.runtime_config_loader import runtime_config


@dataclass(frozen=True, slots=True)
class Segment:
    start: tuple[float, float]
    end: tuple[float, float]


@lru_cache(maxsize=4)
def _segment() -> Segment:
    return Segment(start=(1.25, 2.5), end=(4.75, 8.0))


@primitive
def direct_entrypoint_spawn_shape() -> tuple[np.ndarray, np.ndarray]:
    segment = _segment()
    coords = np.asarray(
        [
            [segment.start[0], segment.start[1], 0.0],
            [segment.end[0], segment.end[1], 0.0],
        ],
        dtype=np.float32,
    )
    return coords, np.asarray([0, 2], dtype=np.int32)


def draw(_t: float) -> Geometry:
    return G.direct_entrypoint_spawn_shape()


def main() -> None:
    config = runtime_config()
    definitions = default_session_authoring_definitions()
    parent_geometry = draw(0.0)
    runner = SceneRunner(
        draw,
        perf=PerfCollector(enabled=False),
        n_worker=1,
        effective_config=config,
        definitions=definitions,
    )
    store = ParamStore()
    defaults = LayerStyleDefaults(
        color=(0.0, 0.0, 0.0),
        thickness=0.01,
    )
    realized_layers = []
    try:
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            realized_layers = runner.run(
                0.0,
                store=store,
                cc_snapshot=None,
                defaults=defaults,
                recording=False,
                transport_epoch=0,
                quality="draft",
            )
            if realized_layers:
                break
            time.sleep(0.01)
        if not realized_layers:
            raise TimeoutError("spawn worker result was not realized before the deadline")

        source_geometry = realized_layers[0].layer.geometry
        definitions.operations.resolve_ref(source_geometry.operation)
        if source_geometry.operation != parent_geometry.operation:
            raise AssertionError(
                "spawn worker geometry operation differs from the parent operation"
            )
        if source_geometry.id != parent_geometry.id:
            raise AssertionError("spawn worker geometry id differs from the parent geometry")

        coords = realized_layers[0].realized.coords
        np.testing.assert_allclose(
            coords,
            np.asarray(
                [[1.25, 2.5, 0.0], [4.75, 8.0, 0.0]],
                dtype=np.float32,
            ),
        )
        if runner.last_evaluation_succeeded is not True:
            raise AssertionError("SceneRunner did not adopt a successful worker result")
        payload = {
            "coords": coords.tolist(),
            "fingerprint": str(source_geometry.operation.fingerprint),
        }
    finally:
        runner.close()

    if mp.active_children():
        raise AssertionError("SceneRunner.close() left a spawn worker alive")
    print(json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    main()
"""


def test_direct_python_entrypoint_realizes_module_local_operation_in_spawn_worker(
    tmp_path: Path,
) -> None:
    """`python sketch.py` の parent catalog で spawn 結果を realize できる。"""

    sketch_path = tmp_path / "sketch.py"
    sketch_path.write_text(_DIRECT_ENTRYPOINT_SOURCE, encoding="utf-8")
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = str(_PROJECT_ROOT / "src")

    completed = subprocess.run(
        [sys.executable, str(sketch_path)],
        cwd=_PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=20.0,
    )

    assert completed.returncode == 0, (
        "direct sketch subprocess failed\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    output_lines = completed.stdout.strip().splitlines()
    assert output_lines, f"direct sketch produced no output; stderr:\n{completed.stderr}"
    payload = json.loads(output_lines[-1])

    assert payload["coords"] == [[1.25, 2.5, 0.0], [4.75, 8.0, 0.0]]
    assert len(payload["fingerprint"]) == 64
