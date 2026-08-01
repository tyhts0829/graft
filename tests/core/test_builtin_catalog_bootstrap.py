"""builtin manifest bootstrap の import-order 非依存契約を検証する。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from grafix.core.builtins import builtin_operation_manifest


def _catalog_payload(order: str) -> dict[str, object]:
    script = "\n".join(
        [
            "import json",
            "from grafix.core.authoring_definitions import default_authoring_definitions",
            "if " + repr(order) + " == 'direct':",
            "    import grafix.core.primitives.circle",
            "    import grafix.core.effects.scale",
            "from grafix.core.builtins import builtin_operation_catalog",
            "catalog = builtin_operation_catalog()",
            "if " + repr(order) + " == 'bootstrap':",
            "    import grafix.core.primitives.circle",
            "    import grafix.core.effects.scale",
            "payload = {",
            "  'entries': [",
            "    [entry.kind, entry.name, str(entry.evaluation_fingerprint), str(entry.schema_fingerprint)]",
            "    for entry in catalog.entries()",
            "  ],",
            "  'default_operations': len(default_authoring_definitions.snapshot().operations),",
            "}",
            "print(json.dumps(payload, sort_keys=True))",
        ]
    )
    root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def test_builtin_manifest_has_one_unique_locator_per_operation() -> None:
    manifest = builtin_operation_manifest()
    keys = tuple((item.kind, item.name) for item in manifest)
    locators = tuple((item.module, item.attribute) for item in manifest)

    assert len(manifest) == 58
    assert len(set(keys)) == len(keys)
    assert len(set(locators)) == len(locators)
    assert all(item.evaluator_abi for item in manifest)


def test_direct_import_and_bootstrap_order_produce_the_same_catalog() -> None:
    direct = _catalog_payload("direct")
    bootstrap = _catalog_payload("bootstrap")

    assert direct == bootstrap
    assert direct["default_operations"] == 0


_CUSTOM_OPERATION_SOURCE = textwrap.dedent(
    """\
    from __future__ import annotations

    from pathlib import Path

    import numpy as np

    from grafix import effect, primitive

    _IMPORT_COUNT_PATH = Path(__file__).with_name("custom-import-count.txt")
    _IMPORT_COUNT_PATH.write_text(
        str(int(_IMPORT_COUNT_PATH.read_text()) + 1)
        if _IMPORT_COUNT_PATH.exists()
        else "1",
        encoding="utf-8",
    )


    @primitive(meta={"length": {"kind": "float", "description": "length"}})
    def stable_custom_line(*, length: float = 1.0):
        return (
            np.asarray(
                [[0.0, 0.0, 0.0], [length, 0.0, 0.0]],
                dtype=np.float32,
            ),
            np.asarray([0, 2], dtype=np.int32),
        )


    @effect(meta={"offset": {"kind": "float", "description": "offset"}})
    def stable_custom_shift(geometry, *, offset: float = 0.0):
        coords, offsets = geometry
        shifted = coords.copy()
        shifted[:, 0] += np.float32(offset)
        return shifted, offsets
    """
)


def _cross_checkout_payload(
    checkout: Path,
    *,
    import_order: str,
    hash_seed: int,
) -> dict[str, object]:
    """別 project path の fresh process で catalog と DAG identity を得る。"""

    script = """
import hashlib
import json
import pickle
import sys
from pathlib import Path

import grafix
from grafix.core.authoring_definitions import default_authoring_definitions

assert Path(grafix.__file__).is_relative_to(Path.cwd())
assert len(default_authoring_definitions.snapshot().operations) == 0
order = sys.argv[1]
if order == "builtin-first":
    from grafix.core.builtins import builtin_operation_catalog
    builtin_operation_catalog()
    import grafix.core.effects.scale
    import custom_ops
else:
    import custom_ops
    import grafix.core.primitives.circle
    from grafix.core.builtins import builtin_operation_catalog
    builtin_operation_catalog()
    import grafix.core.effects.scale

from grafix import E, G
from grafix.core.operation_catalog import current_operation_catalog
from grafix.core.realize import RealizeSession

catalog = current_operation_catalog()
keys = (
    ("primitive", "circle"),
    ("effect", "scale"),
    ("primitive", "stable_custom_line"),
    ("effect", "stable_custom_shift"),
)
entries = {
    f"{kind}:{name}": {
        "evaluation": str(catalog.resolve(kind, name).evaluation_fingerprint),
        "schema": str(catalog.resolve(kind, name).schema_fingerprint),
    }
    for kind, name in keys
}
custom = E.stable_custom_shift(offset=2.0)(
    G.stable_custom_line(length=3.0)
)
builtin = E.scale(scale=(2.0, 2.0, 1.0))(
    G.circle(radius=1.5, segments=12)
)
geometry = custom + builtin
serialized = pickle.dumps(geometry, protocol=5)
restored = pickle.loads(serialized)
with RealizeSession() as first_session:
    realized = first_session.realize(geometry)
with RealizeSession() as second_session:
    second_session.realize(geometry)
realized_bytes = realized.coords.tobytes() + realized.offsets.tobytes()
assert Path("custom-import-count.txt").read_text(encoding="utf-8") == "1"
print(
    json.dumps(
        {
            "entries": entries,
            "geometry_id": geometry.id,
            "operation_refs": [
                [ref.kind, ref.name, str(ref.fingerprint)]
                for ref in geometry.operation_refs
            ],
            "serialized_dag_sha256": hashlib.sha256(serialized).hexdigest(),
            "serialized_dag_size": len(serialized),
            "realized_geometry_sha256": hashlib.sha256(realized_bytes).hexdigest(),
            "roundtrip_geometry_id": restored.id,
        },
        sort_keys=True,
    )
)
"""
    env = os.environ.copy()
    env["PYTHONHASHSEED"] = str(hash_seed)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = os.pathsep.join((str(checkout / "src"), str(checkout)))
    completed = subprocess.run(
        [sys.executable, "-c", script, import_order],
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def test_fingerprints_and_serialized_dag_are_stable_across_clean_processes(
    tmp_path: Path,
) -> None:
    """checkout path、import 順、hash seed を identity へ混ぜない。"""

    first_checkout = tmp_path / "checkout-a"
    second_checkout = tmp_path / "elsewhere" / "checkout-b"
    first_checkout.mkdir()
    second_checkout.mkdir(parents=True)
    source_package = Path(__file__).resolve().parents[2] / "src" / "grafix"
    for checkout in (first_checkout, second_checkout):
        checkout_src = checkout / "src"
        checkout_src.mkdir()
        (checkout_src / "grafix").symlink_to(
            source_package,
            target_is_directory=True,
        )
        (checkout / "custom_ops.py").write_text(
            _CUSTOM_OPERATION_SOURCE,
            encoding="utf-8",
        )

    builtin_first = _cross_checkout_payload(
        first_checkout,
        import_order="builtin-first",
        hash_seed=1,
    )
    custom_first = _cross_checkout_payload(
        second_checkout,
        import_order="custom-first",
        hash_seed=927,
    )

    assert builtin_first == custom_first
    assert set(builtin_first["entries"]) == {
        "primitive:circle",
        "effect:scale",
        "primitive:stable_custom_line",
        "effect:stable_custom_shift",
    }
    assert builtin_first["roundtrip_geometry_id"] == builtin_first["geometry_id"]
