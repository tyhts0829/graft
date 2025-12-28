import json
from pathlib import Path

from grafix.core.parameters import ParamMeta, ParamStore, ParameterKey
from grafix.core.parameters.frame_params import FrameParamRecord
from grafix.core.parameters.invariants import assert_invariants
from grafix.core.parameters.merge_ops import merge_frame_params
from grafix.core.parameters.persistence import (
    default_param_store_path,
    load_param_store,
    save_param_store,
)
from grafix.core.parameters.snapshot_ops import store_snapshot


def test_default_param_store_path_uses_data_dir_and_script_stem():
    def draw(t: float) -> None:
        return None

    path = default_param_store_path(draw)
    assert path.parts[0] == "data"
    assert path.parts[1] == "output"
    assert path.parts[2] == "param_store"
    assert path.name == f"{Path(__file__).stem}.json"
    assert path.suffix == ".json"


def test_param_store_file_roundtrip(tmp_path: Path):
    store = ParamStore()
    key = ParameterKey(op="circle", site_id="site-1", arg="r")
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=0.5,
                meta=ParamMeta(kind="float", ui_min=0.0, ui_max=1.0),
                explicit=False,
            )
        ],
    )

    path = tmp_path / "dummy.json"
    save_param_store(store, path)
    loaded = load_param_store(path)

    snap = store_snapshot(loaded)
    meta, state, ordinal, _label = snap[key]
    assert meta.kind == "float"
    assert meta.ui_min == 0.0
    assert meta.ui_max == 1.0
    assert state.override is True
    assert state.ui_value == 0.5
    assert ordinal == 1
    assert_invariants(loaded)


def test_load_param_store_ignores_broken_json(tmp_path: Path):
    path = tmp_path / "broken.json"
    path.write_text("{broken-json", encoding="utf-8")

    loaded = load_param_store(path)
    assert store_snapshot(loaded) == {}
    assert_invariants(loaded)


def test_save_param_store_prunes_unknown_arg_for_known_primitive(tmp_path: Path):
    # 登録（meta 取得）に必要なので、対象モジュールを明示的に import する。
    from grafix.core.primitives import line as _primitive_line  # noqa: F401

    store = ParamStore()
    known = ParameterKey(op="line", site_id="site-1", arg="length")
    unknown = ParameterKey(op="line", site_id="site-1", arg="__unknown__")
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=known,
                base=1.0,
                meta=ParamMeta(kind="float", ui_min=0.0, ui_max=2.0),
                explicit=False,
            ),
            FrameParamRecord(
                key=unknown,
                base=0.1,
                meta=ParamMeta(kind="float", ui_min=0.0, ui_max=1.0),
                explicit=True,
            ),
        ],
    )

    path = tmp_path / "store.json"
    save_param_store(store, path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    for section in ["states", "meta", "explicit"]:
        assert not any(
            it.get("op") == "line" and it.get("arg") == "__unknown__"
            for it in payload.get(section, [])
        )

    assert any(it.get("op") == "line" and it.get("arg") == "length" for it in payload["states"])
