from grafix.core.parameters import FrameParamRecord, ParamMeta, ParamStore, ParameterKey
from grafix.core.parameters.history import ParamStoreHistory
from grafix.core.parameters.merge_ops import merge_frame_params
from grafix.core.parameters.ui_ops import update_state_from_ui
from grafix.core.parameters.view import ParameterRow
from grafix.interactive.parameter_gui.source_badge import source_badge_for_row


def _row(
    *,
    override: bool,
    kind: str = "float",
    cc_key: int | tuple[int | None, int | None, int | None] | None = None,
) -> ParameterRow:
    return ParameterRow(
        label="1:x",
        op="line",
        site_id="site",
        arg="x",
        kind=kind,
        ui_value=0.5,
        ui_min=0.0,
        ui_max=1.0,
        choices=None,
        cc_key=cc_key,
        override=override,
        ordinal=1,
    )


def test_source_badge_prefers_observed_effective_source() -> None:
    assert source_badge_for_row(_row(override=False), "code") == "CODE"
    assert source_badge_for_row(_row(override=True), "ui") == "UI"
    assert (
        source_badge_for_row(_row(override=False, cc_key=17), "midi_live")
        == "MIDI LIVE"
    )
    assert (
        source_badge_for_row(_row(override=False, cc_key=17), "midi_frozen")
        == "MIDI FROZEN"
    )
    # RGB MIDI is intentionally hidden until its dedicated resolver supports
    # tuple CC input; a stale saved mapping must not be reported as effective.
    assert (
        source_badge_for_row(
            _row(override=True, kind="rgb", cc_key=(10, None, 12)),
            "midi_live",
        )
        == "UI"
    )


def test_source_badge_ignores_an_observation_invalidated_by_history_restore() -> None:
    assert source_badge_for_row(_row(override=False), "ui") == "CODE"
    assert source_badge_for_row(_row(override=True), "code") == "UI"
    assert source_badge_for_row(_row(override=True), "midi_live") == "UI"


def test_undo_badge_matches_restored_effective_source_before_the_next_draw() -> None:
    store = ParamStore()
    key = ParameterKey(op="line", site_id="site", arg="x")
    meta = ParamMeta(kind="float", ui_min=0.0, ui_max=1.0)
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=0.0,
                meta=meta,
                effective=0.0,
                source="code",
                explicit=True,
            )
        ],
    )
    history = ParamStoreHistory(store)
    ok, error = update_state_from_ui(store, key, 0.75, meta=meta, override=True)
    assert ok and error is None
    assert history.record_change(source="slider") is True
    store._runtime_ref().last_source_by_key[key] = "ui"

    assert history.undo() is True
    restored = store.get_state(key)
    assert restored is not None
    assert restored.override is False
    # runtime 観測値は削除しないが、復元済み row と矛盾する
    # 1-frame-old の source は badge 決定に使わない。
    assert store._runtime_ref().last_source_by_key[key] == "ui"
    assert source_badge_for_row(_row(override=restored.override), "ui") == "CODE"


def test_source_badge_has_a_useful_pre_first_frame_fallback() -> None:
    assert source_badge_for_row(_row(override=False), None) == "CODE"
    assert source_badge_for_row(_row(override=True), None) == "UI"
    assert source_badge_for_row(_row(override=False, kind="bool"), None) == "CODE"


def test_merge_remembers_last_effective_source_without_persisting_it() -> None:
    store = ParamStore()
    key = ParameterKey(op="line", site_id="site", arg="x")
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=0.0,
                effective=0.75,
                source="midi_live",
                meta=ParamMeta(kind="float", ui_min=0.0, ui_max=1.0),
                explicit=True,
            )
        ],
    )

    runtime = store._runtime_ref()
    assert runtime.last_effective_by_key[key] == 0.75
    assert runtime.last_source_by_key[key] == "midi_live"
