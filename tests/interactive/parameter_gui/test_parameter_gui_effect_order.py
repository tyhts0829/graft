from __future__ import annotations

import pytest

from grafix import E
from grafix.core.geometry import Geometry
from grafix.core.parameters.context import parameter_context
from grafix.core.parameters.effect_order_ops import merge_frame_effect_chains
from grafix.core.parameters.effects import EffectStepTopology
from grafix.core.parameters.frame_params import FrameEffectChainRecord
from grafix.core.parameters.history import ParamStoreHistory
from grafix.core.parameters.store import ParamStore
from grafix.interactive.parameter_gui import store_bridge
from grafix.interactive.parameter_gui.session_state import WidgetSessionState
from grafix.interactive.parameter_gui.store_bridge import apply_effect_order_command
from grafix.interactive.parameter_gui.snippet import snippet_for_block
from grafix.interactive.parameter_gui.grouping import GroupType
from grafix.interactive.parameter_gui.table import (
    EFFECT_STEP_DRAG_PAYLOAD_TYPE,
    EffectOrderCommand,
    TableEdits,
    _decode_effect_step_drag_payload,
    _effect_step_drop_placement,
    _encode_effect_step_drag_payload,
    _render_effect_step_heading,
)
from grafix.interactive.parameter_gui.table_model import (
    EFFECT_ORDER_DUPLICATE_REASON,
    EFFECT_ORDER_FILTERED_REASON,
    EFFECT_ORDER_INCOMPLETE_REASON,
    EffectChainTableState,
    effect_chain_table_states,
)


def _topology(
    op: str,
    site_id: str,
    *,
    index: int,
    n_inputs: int = 1,
) -> EffectStepTopology:
    return EffectStepTopology(
        op=op,
        site_id=site_id,
        n_inputs=n_inputs,
        code_index=index,
    )


def test_effect_step_drag_payload_round_trip_and_rejects_foreign_data() -> None:
    payload = _encode_effect_step_drag_payload(
        "chain:日本語",
        ("select.effect.1", "site:1"),
    )

    assert _decode_effect_step_drag_payload(payload) == (
        "chain:日本語",
        ("select.effect.1", "site:1"),
    )
    assert _decode_effect_step_drag_payload(b"not-json") is None
    assert _decode_effect_step_drag_payload(b'["chain","op"]') is None
    assert _decode_effect_step_drag_payload("not-bytes") is None


def test_effect_step_drop_placement_uses_target_midpoint() -> None:
    assert (
        _effect_step_drop_placement(
            mouse_y=11.9,
            item_top=10.0,
            item_bottom=14.0,
        )
        == "before"
    )
    assert (
        _effect_step_drop_placement(
            mouse_y=12.0,
            item_top=10.0,
            item_bottom=14.0,
        )
        == "after"
    )


def test_effect_chain_table_state_pins_multi_input_and_omits_no_ops() -> None:
    state = EffectChainTableState(
        chain_id="chain",
        steps=(("merge", "m"), ("scale", "s"), ("rotate", "r")),
        n_inputs=(2, 1, 1),
        order_overridden=False,
    )

    assert state.is_pinned(("merge", "m")) is True
    assert state.can_move(("merge", "m"), ("scale", "s"), "after") is False
    assert state.can_move(("rotate", "r"), ("merge", "m"), "before") is False
    assert state.can_move(("rotate", "r"), ("merge", "m"), "after") is True
    assert state.can_move(("scale", "s"), ("rotate", "r"), "before") is False
    assert state.neighbor_move(("scale", "s"), direction=-1) is None
    assert state.neighbor_move(("scale", "s"), direction=1) == (
        ("rotate", "r"),
        "after",
    )


def test_effect_chain_table_states_follow_effective_order_and_disable_ambiguity() -> None:
    topologies = {
        "complete": (
            _topology("scale", "s", index=0),
            _topology("rotate", "r", index=1),
        ),
        "hidden": (
            _topology("scale", "h1", index=0),
            _topology("rotate", "h2", index=1),
        ),
        "duplicate": (
            _topology("scale", "dup", index=0),
            _topology("scale", "dup", index=1),
        ),
    }
    states = effect_chain_table_states(
        topologies=topologies,
        step_info_by_site={
            ("scale", "s"): ("complete", 1),
            ("rotate", "r"): ("complete", 0),
            ("scale", "h1"): ("hidden", 0),
            ("rotate", "h2"): ("hidden", 1),
            ("scale", "dup"): ("duplicate", 1),
        },
        order_overrides={
            "complete": (("rotate", "r"), ("scale", "s")),
        },
        gui_steps_by_chain={
            "complete": {("scale", "s"), ("rotate", "r")},
            "hidden": {("scale", "h1")},
            "duplicate": {("scale", "dup")},
        },
    )

    assert states["complete"].steps == (("rotate", "r"), ("scale", "s"))
    assert states["complete"].order_overridden is True
    assert states["complete"].disabled_reason is None
    assert states["hidden"].disabled_reason == EFFECT_ORDER_INCOMPLETE_REASON
    assert states["duplicate"].disabled_reason == EFFECT_ORDER_DUPLICATE_REASON

    filtered = states["complete"].for_visible_steps({("rotate", "r")})
    assert filtered.disabled_reason == EFFECT_ORDER_FILTERED_REASON


class _DrawList:
    def __init__(self) -> None:
        self.lines: list[tuple[object, ...]] = []
        self.clip_rect = ((0.0, 0.0), (500.0, 500.0))

    def add_line(self, *args: object) -> None:
        self.lines.append(args)

    def get_clip_rect_min(self) -> tuple[float, float]:
        return self.clip_rect[0]

    def get_clip_rect_max(self) -> tuple[float, float]:
        return self.clip_rect[1]

    def push_clip_rect(
        self,
        x_min: float,
        y_min: float,
        x_max: float,
        y_max: float,
        _intersect: bool,
    ) -> None:
        self.clip_rect = ((x_min, y_min), (x_max, y_max))

    def pop_clip_rect(self) -> None:
        return None


class _ClosedPopup:
    opened = False

    def __enter__(self) -> _ClosedPopup:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _DragDropSource:
    def __init__(self, owner: _DragDropImgui) -> None:
        self._owner = owner
        self.dragging = owner.source_dragging

    def __enter__(self) -> _DragDropSource:
        return self

    def __exit__(self, *_args: object) -> None:
        if self.dragging:
            self._owner.end_source_calls += 1


class _DragDropTarget:
    def __init__(self, owner: _DragDropImgui) -> None:
        self._owner = owner
        self.hovered = owner.target_hovered

    def __enter__(self) -> _DragDropTarget:
        return self

    def __exit__(self, *_args: object) -> None:
        if self.hovered:
            self._owner.end_target_calls += 1


class _DragDropImgui:
    SELECTABLE_SPAN_ALL_COLUMNS = 2
    DRAG_DROP_ACCEPT_PEEK_ONLY = 3072
    DRAG_DROP_ACCEPT_NO_DRAW_DEFAULT_RECT = 2048
    COLOR_TEXT = 0

    def __init__(
        self,
        *,
        payload: bytes,
        mouse_y: float,
        source_dragging: bool = False,
        target_hovered: bool = True,
        item_hovered: bool = False,
    ) -> None:
        self.payload = payload
        self.mouse_y = mouse_y
        self.source_dragging = source_dragging
        self.target_hovered = target_hovered
        self.item_hovered = item_hovered
        self.payloads_set: list[tuple[str, bytes]] = []
        self.small_button_labels: list[str] = []
        self.disabled_texts: list[str] = []
        self.tooltips: list[str] = []
        self.draw_list = _DrawList()
        self.end_source_calls = 0
        self.end_target_calls = 0

    def table_next_row(self) -> None:
        pass

    def table_set_column_index(self, _index: int) -> None:
        pass

    def push_id(self, _value: str) -> None:
        pass

    def pop_id(self) -> None:
        pass

    def begin_group(self) -> None:
        pass

    def end_group(self) -> None:
        pass

    def small_button(self, label: str) -> bool:
        self.small_button_labels.append(label)
        return False

    def begin_popup_context_item(self, _label: str) -> _ClosedPopup:
        return _ClosedPopup()

    def same_line(self, *_args: object) -> None:
        pass

    def selectable(self, *_args: object) -> tuple[bool, bool]:
        return False, False

    def push_style_color(self, *_args: object) -> None:
        pass

    def pop_style_color(self, _count: int = 1) -> None:
        pass

    def is_item_hovered(self) -> bool:
        return self.item_hovered

    def is_item_focused(self) -> bool:
        return False

    def calc_text_size(self, _text: str) -> tuple[float, float]:
        return 10.0, 14.0

    def text(self, _text: str) -> None:
        pass

    def text_disabled(self, text: str) -> None:
        self.disabled_texts.append(text)

    def set_tooltip(self, text: str) -> None:
        self.tooltips.append(text)

    def begin_drag_drop_source(self) -> _DragDropSource:
        return _DragDropSource(self)

    def set_drag_drop_payload(self, payload_type: str, payload: bytes) -> None:
        self.payloads_set.append((payload_type, payload))

    def get_item_rect_min(self) -> tuple[float, float]:
        return 10.0, 20.0

    def get_item_rect_max(self) -> tuple[float, float]:
        return 110.0, 40.0

    def begin_drag_drop_target(self) -> _DragDropTarget:
        return _DragDropTarget(self)

    def accept_drag_drop_payload(
        self,
        payload_type: str,
        _flags: int,
    ) -> bytes | None:
        assert payload_type == EFFECT_STEP_DRAG_PAYLOAD_TYPE
        return self.payload

    def get_mouse_position(self) -> tuple[float, float]:
        return 0.0, self.mouse_y

    def get_window_draw_list(self) -> _DrawList:
        return self.draw_list

    def get_color_u32_rgba(self, *_rgba: float) -> int:
        return 1

    def get_window_position(self) -> tuple[float, float]:
        return 0.0, 0.0

    def get_window_content_region_min(self) -> tuple[float, float]:
        return 0.0, 0.0

    def get_window_content_region_max(self) -> tuple[float, float]:
        return 120.0, 100.0


class _RealImguiRecorder:
    """実pyimguiへ委譲しつつ、drag関連itemと挿入線を記録する。"""

    def __init__(self, imgui) -> None:
        self._imgui = imgui
        self.handle_rects: list[
            tuple[tuple[float, float], tuple[float, float]]
        ] = []
        self.handle_clip_rects: list[
            tuple[tuple[float, float], tuple[float, float]]
        ] = []
        self.label_rects: list[
            tuple[tuple[float, float], tuple[float, float]]
        ] = []
        self.label_clip_rects: list[
            tuple[tuple[float, float], tuple[float, float]]
        ] = []
        self.group_rects: list[
            tuple[tuple[float, float], tuple[float, float]]
        ] = []
        self.group_clip_rects: list[
            tuple[tuple[float, float], tuple[float, float]]
        ] = []
        self.insertion_lines: list[
            tuple[
                tuple[object, ...],
                tuple[tuple[float, float], tuple[float, float]],
            ]
        ] = []

    def __getattr__(self, name: str):
        return getattr(self._imgui, name)

    def _item_rect(
        self,
    ) -> tuple[tuple[float, float], tuple[float, float]]:
        return (
            tuple(self._imgui.get_item_rect_min()),
            tuple(self._imgui.get_item_rect_max()),
        )

    def _clip_rect(
        self,
    ) -> tuple[tuple[float, float], tuple[float, float]]:
        draw_list = self._imgui.get_window_draw_list()
        return (
            tuple(draw_list.get_clip_rect_min()),
            tuple(draw_list.get_clip_rect_max()),
        )

    def small_button(self, label: str) -> bool:
        clicked = bool(self._imgui.small_button(label))
        self.handle_rects.append(self._item_rect())
        self.handle_clip_rects.append(self._clip_rect())
        return clicked

    def selectable(self, *args: object):
        selected = self._imgui.selectable(*args)
        self.label_rects.append(self._item_rect())
        self.label_clip_rects.append(self._clip_rect())
        return selected

    def end_group(self) -> None:
        self._imgui.end_group()
        self.group_rects.append(self._item_rect())
        self.group_clip_rects.append(self._clip_rect())

    def get_window_draw_list(self):
        return _RealDrawListRecorder(
            self._imgui.get_window_draw_list(),
            self.insertion_lines,
        )

    def reset_rects(self) -> None:
        self.handle_rects.clear()
        self.handle_clip_rects.clear()
        self.label_rects.clear()
        self.label_clip_rects.clear()
        self.group_rects.clear()
        self.group_clip_rects.clear()
        self.insertion_lines.clear()


class _RealDrawListRecorder:
    """実draw listへ委譲し、add_line時点のclip矩形を記録する。"""

    def __init__(self, draw_list, lines: list[tuple[object, ...]]) -> None:
        self._draw_list = draw_list
        self._lines = lines

    def __getattr__(self, name: str):
        return getattr(self._draw_list, name)

    def add_line(self, *args: object) -> None:
        clip_rect = (
            tuple(self._draw_list.get_clip_rect_min()),
            tuple(self._draw_list.get_clip_rect_max()),
        )
        self._lines.append((tuple(args), clip_rect))
        self._draw_list.add_line(*args)


def test_effect_step_heading_previews_and_commits_same_chain_drop() -> None:
    state = EffectChainTableState(
        chain_id="chain",
        steps=(("scale", "s"), ("rotate", "r")),
        n_inputs=(1, 1),
        order_overridden=False,
    )
    imgui = _DragDropImgui(
        payload=_encode_effect_step_drag_payload("chain", ("rotate", "r")),
        mouse_y=21.0,
    )

    command = _render_effect_step_heading(
        imgui,
        "Scale",
        step=("scale", "s"),
        state=state,
    )

    assert command == EffectOrderCommand.move(
        chain_id="chain",
        source=("rotate", "r"),
        target=("scale", "s"),
        placement="before",
    )
    assert len(imgui.draw_list.lines) == 1
    assert imgui.end_target_calls == 1


def test_effect_step_heading_rejects_cross_chain_drop() -> None:
    state = EffectChainTableState(
        chain_id="chain:a",
        steps=(("scale", "s"), ("rotate", "r")),
        n_inputs=(1, 1),
        order_overridden=False,
    )
    imgui = _DragDropImgui(
        payload=_encode_effect_step_drag_payload("chain:b", ("rotate", "r")),
        mouse_y=21.0,
    )

    command = _render_effect_step_heading(
        imgui,
        "Scale",
        step=("scale", "s"),
        state=state,
    )

    assert command is None
    assert imgui.draw_list.lines == []


def test_effect_step_heading_handle_sets_stable_source_payload() -> None:
    state = EffectChainTableState(
        chain_id="chain",
        steps=(("scale", "s"), ("rotate", "r")),
        n_inputs=(1, 1),
        order_overridden=False,
    )
    imgui = _DragDropImgui(
        payload=b"",
        mouse_y=21.0,
        source_dragging=True,
        target_hovered=False,
    )

    assert (
        _render_effect_step_heading(
            imgui,
            "Scale",
            step=("scale", "s"),
            state=state,
        )
        is None
    )
    assert imgui.payloads_set == [
        (
            EFFECT_STEP_DRAG_PAYLOAD_TYPE,
            _encode_effect_step_drag_payload("chain", ("scale", "s")),
        )
    ]
    assert imgui.end_source_calls == 1


def test_effect_step_heading_disables_filtered_chain_and_explains_reason() -> None:
    state = EffectChainTableState(
        chain_id="chain",
        steps=(("scale", "s"), ("rotate", "r")),
        n_inputs=(1, 1),
        order_overridden=False,
        disabled_reason=EFFECT_ORDER_FILTERED_REASON,
    )
    imgui = _DragDropImgui(
        payload=_encode_effect_step_drag_payload("chain", ("rotate", "r")),
        mouse_y=21.0,
        source_dragging=True,
        item_hovered=True,
    )

    assert (
        _render_effect_step_heading(
            imgui,
            "Scale",
            step=("scale", "s"),
            state=state,
        )
        is None
    )
    assert imgui.small_button_labels == []
    assert imgui.payloads_set == []
    assert "::" in imgui.disabled_texts
    assert EFFECT_ORDER_FILTERED_REASON in imgui.tooltips


def test_effect_step_heading_pins_multi_input_step_and_explains_reason() -> None:
    state = EffectChainTableState(
        chain_id="chain",
        steps=(("boolean", "binary"), ("rotate", "r")),
        n_inputs=(2, 1),
        order_overridden=False,
    )
    imgui = _DragDropImgui(
        payload=b"",
        mouse_y=21.0,
        source_dragging=True,
        item_hovered=True,
    )

    assert (
        _render_effect_step_heading(
            imgui,
            "Boolean",
            step=("boolean", "binary"),
            state=state,
        )
        is None
    )
    assert imgui.small_button_labels == []
    assert imgui.payloads_set == []
    assert "::" in imgui.disabled_texts
    assert "This multi-input effect is fixed at the start." in imgui.tooltips


@pytest.mark.parametrize(
    ("source_index", "target_index", "target_ratio", "expected"),
    [
        pytest.param(
            0,
            1,
            0.8,
            EffectOrderCommand.move(
                chain_id="chain",
                source=("scale", "s"),
                target=("rotate", "r"),
                placement="after",
            ),
            id="down-after",
        ),
        pytest.param(
            1,
            0,
            0.2,
            EffectOrderCommand.move(
                chain_id="chain",
                source=("rotate", "r"),
                target=("scale", "s"),
                placement="before",
            ),
            id="up-before",
        ),
    ],
)
@pytest.mark.parametrize("dpi_scale", [1.0, 2.0])
def test_real_pyimgui_drag_items_and_insertion_line_are_not_clipped(
    dpi_scale: float,
    source_index: int,
    target_index: int,
    target_ratio: float,
    expected: EffectOrderCommand,
) -> None:
    imgui = pytest.importorskip("imgui")
    context = imgui.create_context()
    try:
        io = imgui.get_io()
        io.display_size = (800.0 * dpi_scale, 600.0 * dpi_scale)
        io.delta_time = 1.0 / 60.0
        io.font_global_scale = dpi_scale
        io.fonts.get_tex_data_as_rgba32()
        recorder = _RealImguiRecorder(imgui)
        state = EffectChainTableState(
            chain_id="chain",
            steps=(("scale", "s"), ("rotate", "r")),
            n_inputs=(1, 1),
            order_overridden=False,
        )

        def render_frame(
            mouse_pos: tuple[float, float],
            *,
            mouse_down: bool,
        ) -> list[EffectOrderCommand]:
            io.mouse_pos = mouse_pos
            io.mouse_down[0] = bool(mouse_down)
            recorder.reset_rects()
            imgui.new_frame()
            imgui.set_next_window_position(
                10.0 * dpi_scale,
                10.0 * dpi_scale,
            )
            imgui.set_next_window_size(
                500.0 * dpi_scale,
                400.0 * dpi_scale,
            )
            imgui.begin("effect-order vertical drag")
            table = imgui.begin_table("##effect_order_vertical_drag", 4)
            assert table.opened
            commands = [
                command
                for command in (
                    _render_effect_step_heading(
                        recorder,
                        "Scale",
                        step=("scale", "s"),
                        state=state,
                    ),
                    _render_effect_step_heading(
                        recorder,
                        "Rotate",
                        step=("rotate", "r"),
                        state=state,
                    ),
                )
                if command is not None
            ]
            imgui.end_table()
            imgui.end()
            imgui.render()
            return commands

        assert render_frame((-100.0, -100.0), mouse_down=False) == []
        assert len(recorder.handle_rects) == 2
        assert len(recorder.label_rects) == 2
        for rects, clips in (
            (recorder.handle_rects, recorder.handle_clip_rects),
            (recorder.label_rects, recorder.label_clip_rects),
            (recorder.group_rects, recorder.group_clip_rects),
        ):
            for (item_min, item_max), (clip_min, clip_max) in zip(
                rects,
                clips,
                strict=True,
            ):
                assert item_min[0] >= clip_min[0]
                assert item_min[1] >= clip_min[1]
                assert item_max[0] <= clip_max[0]
                assert item_max[1] <= clip_max[1]
                assert item_max[0] > item_min[0]
                assert item_max[1] > item_min[1]
        label_min, label_max = recorder.label_rects[0]
        label_width, _label_height = imgui.calc_text_size("Scale")
        assert label_max[0] - label_min[0] > label_width

        source_min, source_max = recorder.handle_rects[source_index]
        target_min, target_max = recorder.group_rects[target_index]
        source_pos = (
            (source_min[0] + source_max[0]) * 0.5,
            (source_min[1] + source_max[1]) * 0.5,
        )
        target_pos = (
            source_pos[0],
            target_min[1]
            + (target_max[1] - target_min[1]) * target_ratio,
        )
        activation_pos = (
            source_pos[0] + 8.0 * dpi_scale,
            source_pos[1],
        )
        assert target_min[0] <= target_pos[0] < target_max[0]

        assert render_frame(source_pos, mouse_down=False) == []
        assert render_frame(source_pos, mouse_down=True) == []
        assert render_frame(activation_pos, mouse_down=True) == []
        assert render_frame(target_pos, mouse_down=True) == []
        assert len(recorder.insertion_lines) == 1
        line, (clip_min, clip_max) = recorder.insertion_lines[0]
        x_min, y_start, x_max, y_end, _color, thickness = line
        assert float(x_min) >= clip_min[0]
        assert float(x_max) <= clip_max[0]
        assert float(y_start) == pytest.approx(float(y_end))
        assert clip_min[1] <= float(y_start) <= clip_max[1]
        assert float(x_max) - float(x_min) > 400.0 * dpi_scale
        assert float(thickness) >= 2.0
        assert render_frame(target_pos, mouse_down=False) == [expected]
    finally:
        imgui.destroy_context(context)


def test_apply_effect_order_command_is_one_full_history_operation() -> None:
    store = ParamStore()
    merge_frame_effect_chains(
        store,
        [
            FrameEffectChainRecord(
                chain_id="chain",
                steps=(
                    _topology("scale", "s", index=0),
                    _topology("rotate", "r", index=1),
                ),
            )
        ],
        observation_complete=False,
    )
    history = ParamStoreHistory(store)
    command = EffectOrderCommand.move(
        chain_id="chain",
        source=("rotate", "r"),
        target=("scale", "s"),
        placement="before",
    )

    with history.transaction(source=("effect_order", "chain"), patch=False):
        assert apply_effect_order_command(store, command) is True

    assert store.effect_order_overrides()["chain"] == (
        ("rotate", "r"),
        ("scale", "s"),
    )
    assert history.undo_depth == 1
    assert history.undo() is True
    assert store.effect_order_overrides() == {}
    assert history.redo() is True
    assert store.effect_order_overrides()["chain"] == (
        ("rotate", "r"),
        ("scale", "s"),
    )

    reset = EffectOrderCommand.reset(chain_id="chain")
    with history.transaction(source=("effect_order", "chain"), patch=False):
        assert apply_effect_order_command(store, reset) is True
    assert store.effect_order_overrides() == {}


def test_store_bridge_commits_effect_command_as_one_history_unit(
    monkeypatch,
) -> None:
    store = ParamStore()
    merge_frame_effect_chains(
        store,
        [
            FrameEffectChainRecord(
                chain_id="chain",
                steps=(
                    _topology("scale", "s", index=0),
                    _topology("rotate", "r", index=1),
                ),
            )
        ],
        observation_complete=False,
    )
    move = EffectOrderCommand.move(
        chain_id="chain",
        source=("rotate", "r"),
        target=("scale", "s"),
        placement="before",
    )
    assert apply_effect_order_command(store, move) is True
    revision = store.revision
    history = ParamStoreHistory(store)

    def fake_render(render_input, **_kwargs):
        rows = [
            render_input.model_rows[item.row_index]
            for block in render_input.group_layout
            for item in block.items
        ]
        return TableEdits(
            rows=tuple(rows),
            collapsed_headers=render_input.collapsed_headers,
            midi_learn_state=render_input.midi_learn_state,
            effect_order_commands=(
                EffectOrderCommand.reset(chain_id="chain"),
            ),
        )

    monkeypatch.setattr(store_bridge, "render_parameter_table", fake_render)

    view = store_bridge.parameter_table_view_for_store(
        store,
        show_inactive_params=True,
    )
    result = store_bridge.render_store_parameter_table(
        store,
        table_view=view,
        widget_state=WidgetSessionState(),
        history=history,
    )
    assert result.changed is True
    assert store.revision == revision + 1
    assert store.effect_order_overrides() == {}
    assert history.undo_depth == 1
    assert history.undo() is True
    assert "chain" in store.effect_order_overrides()


def test_effective_order_drives_table_rows_and_copy_code() -> None:
    store = ParamStore()
    builder = E.scale(key="gui-order-scale").rotate(key="gui-order-rotate")
    with parameter_context(store):
        builder(Geometry.create(op="gui-order-source"))

    model = store_bridge._parameter_table_model_for_store(store)
    state = model.effect_chain_state_by_id[builder.chain_id]
    assert [row.op for row in model.rows if row.op in {"scale", "rotate"}][0] == (
        "scale"
    )
    assert apply_effect_order_command(
        store,
        EffectOrderCommand.move(
            chain_id=builder.chain_id,
            source=state.steps[1],
            target=state.steps[0],
            placement="before",
        ),
    )

    reordered = store_bridge._parameter_table_model_for_store(store)
    assert [
        row.op for row in reordered.rows if row.op in {"scale", "rotate"}
    ][0] == "rotate"
    block = next(
        block
        for block in reordered.group_layout
        if block.group_id == (GroupType.EFFECT_CHAIN, builder.chain_id)
    )
    snippet = snippet_for_block(
        block,
        reordered.rows,
        step_info_by_site=reordered.step_info_by_site,
    )
    assert snippet.index("E.rotate") < snippet.index(".scale")
