from pathlib import Path

from src.parameters import ParamMeta, ParamStore, ParameterKey
from src.parameters.persistence import default_param_store_path, load_param_store, save_param_store


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
    store.ensure_state(key, base_value=0.5).override = True
    store.set_meta(key, ParamMeta(kind="float", ui_min=0.0, ui_max=1.0))

    path = tmp_path / "dummy.json"
    save_param_store(store, path)
    loaded = load_param_store(path)

    snap = loaded.snapshot()
    meta, state, ordinal, _label = snap[key]
    assert meta.kind == "float"
    assert meta.ui_min == 0.0
    assert meta.ui_max == 1.0
    assert state.override is True
    assert state.ui_value == 0.5
    assert ordinal == 1


def test_load_param_store_ignores_broken_json(tmp_path: Path):
    path = tmp_path / "broken.json"
    path.write_text("{broken-json", encoding="utf-8")

    loaded = load_param_store(path)
    assert loaded.snapshot() == {}
