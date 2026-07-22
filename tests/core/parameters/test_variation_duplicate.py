from __future__ import annotations

from grafix.core.parameters.frame_params import FrameParamRecord
from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.merge_ops import merge_frame_params
from grafix.core.parameters.meta import ParamMeta
from grafix.core.parameters.store import ParamStore
from grafix.core.parameters.ui_ops import update_state_from_ui
from grafix.core.parameters.variations import (
    create_variation,
    duplicate_variation,
    list_variations,
    restore_variation,
)


def test_duplicate_variation_reuses_immutable_snapshot_under_new_name() -> None:
    store = ParamStore()
    key = ParameterKey("circle", "site", "radius")
    meta = ParamMeta(kind="float", ui_min=0.0, ui_max=10.0)
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=2.0,
                meta=meta,
                effective=2.0,
                source="code",
                explicit=False,
            )
        ],
    )
    original = create_variation(
        store,
        "original",
        note="promising",
        seed=17,
        t=1.25,
        thumbnail_path="thumb.png",
        created_at=100.0,
    )

    duplicate = duplicate_variation(
        store,
        "original",
        "copy",
        created_at=200.0,
    )

    assert duplicate.name == "copy"
    assert duplicate.created_at == 200.0
    assert duplicate.note == "promising"
    assert duplicate.seed == 17
    assert duplicate.t == 1.25
    assert duplicate.thumbnail_path == "thumb.png"
    assert duplicate.parameter_snapshot is original.parameter_snapshot
    assert [variation.name for variation in list_variations(store)] == [
        "original",
        "copy",
    ]

    ok, error = update_state_from_ui(store, key, 8.0, meta=meta, override=True)
    assert ok and error is None
    assert restore_variation(store, "copy") is True
    state = store.get_state(key)
    assert state is not None and state.ui_value == 2.0
