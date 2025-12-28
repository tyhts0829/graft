from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.meta import ParamMeta
from grafix.core.parameters.state import ParamState
from grafix.core.parameters.store import ParamStore
from grafix.core.parameters.view import ParameterRow
from grafix.interactive.parameter_gui.store_bridge import _apply_updated_rows_to_store


def _row(
    *,
    site_id: str,
    arg: str,
    kind: str,
    ui_value,
    override: bool,
) -> ParameterRow:
    return ParameterRow(
        label="",
        op="text",
        site_id=site_id,
        arg=arg,
        kind=kind,
        ui_value=ui_value,
        ui_min=None,
        ui_max=None,
        choices=None,
        cc_key=None,
        override=override,
        ordinal=1,
    )


def test_text_font_select_ttc_resets_font_index_to_zero_with_override_true():
    store = ParamStore()
    site_id = "s"

    font_key = ParameterKey(op="text", site_id=site_id, arg="font")
    font_index_key = ParameterKey(op="text", site_id=site_id, arg="font_index")

    snapshot = {
        font_key: (ParamMeta(kind="font"), ParamState(ui_value="GoogleSans-Regular.ttf"), 1, None),
        font_index_key: (ParamMeta(kind="int"), ParamState(ui_value=3), 1, None),
    }

    rows_before = [
        _row(
            site_id=site_id,
            arg="font",
            kind="font",
            ui_value="GoogleSans-Regular.ttf",
            override=False,
        ),
        _row(site_id=site_id, arg="font_index", kind="int", ui_value=3, override=True),
    ]
    rows_after = [
        _row(
            site_id=site_id,
            arg="font",
            kind="font",
            ui_value="MyCollection.ttc",
            override=False,
        ),
        _row(site_id=site_id, arg="font_index", kind="int", ui_value=3, override=True),
    ]

    _apply_updated_rows_to_store(store, snapshot, rows_before, rows_after)

    state = store.get_state(font_index_key)
    assert state is not None
    assert state.ui_value == 0
    assert state.override is True
