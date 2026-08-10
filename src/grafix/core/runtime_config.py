"""実行時設定の immutable value、binding、pure mapping validation。"""

from __future__ import annotations

import difflib
import math
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from numbers import Real
from pathlib import Path
from typing import Any

from grafix.core.gcode_params import GCodeParams

_PARAMETER_GUI_FONT_SIZE_BASE_PX_DEFAULT = 14.0
_PARAMETER_GUI_SHORTCUT_ACTIONS = (
    "play_pause",
    "reset_time",
    "step_backward",
    "step_forward",
    "slower",
    "faster",
    "range_shift",
    "range_min",
    "range_max",
    "cancel",
    "undo",
    "redo",
)
_MIDI_MODES = ("7bit", "14bit")


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """grafix の実行時設定。

    `runtime_config_from_mapping()` が検証済み mapping から構築する不変オブジェクト。
    YAML の読み込みと探索は application layer の
    `grafix.runtime_config_loader` が担当する。
    実行時に参照される「ファイルパス」「UI の配置」「書き出し設定」などを集約する。

    Attributes
    ----------
    config_path:
        実際に採用されたユーザー設定ファイルのパス。
        loader への明示指定または探索で見つかった 1 ファイルのどちらか。
        ユーザー設定が無い場合は None（同梱デフォルトのみで動作）。
    output_dir:
        生成物（PNG 等）の出力先ディレクトリ。
    sketch_dir:
        スケッチ検索用のベースディレクトリ（任意）。
    preset_module_dirs:
        preset モジュール探索用のディレクトリ列。
    font_dirs:
        フォント探索用のディレクトリ列。
    window_pos_draw:
        描画ウィンドウの左上座標 (x, y)。
    window_pos_parameter_gui:
        パラメータ GUI ウィンドウの左上座標 (x, y)。
    parameter_gui_window_size:
        パラメータ GUI のウィンドウサイズ (w, h)。
    parameter_gui_fallback_font_japanese:
        日本語表示時のフォールバックフォント名（任意）。
    parameter_gui_font_size_base_px:
        パラメータ GUI の基準フォントサイズ（px）。
    png_scale:
        `python -m grafix export` における PNG の拡大率。
    gcode:
        `python -m grafix export` における G-code 出力設定。
    midi_inputs:
        MIDI 入力の設定。各要素は (port_name, mode)。
    """

    config_path: Path | None
    output_dir: Path
    sketch_dir: Path | None
    preset_module_dirs: tuple[Path, ...]
    font_dirs: tuple[Path, ...]
    window_pos_draw: tuple[int, int]
    window_pos_parameter_gui: tuple[int, int]
    parameter_gui_window_size: tuple[int, int]
    parameter_gui_fallback_font_japanese: str | None
    parameter_gui_font_size_base_px: float
    parameter_gui_shortcuts: tuple[tuple[str, str], ...]
    png_scale: float
    gcode: GCodeParams
    midi_inputs: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class RuntimeConfigValue:
    """1 つの effective config leaf と出典・path 解決結果。"""

    key: str
    source: str
    effective_value: object
    is_path: bool
    resolved_path: Path | tuple[Path, ...] | None


@dataclass(frozen=True, slots=True)
class RuntimeConfigReport:
    """strict validation 後の config と leaf ごとの provenance。"""

    config: RuntimeConfig
    active_source: str
    values: tuple[RuntimeConfigValue, ...]


@dataclass(frozen=True, slots=True)
class RuntimeConfigFallback:
    """user config失敗後にpackaged defaultへ退避した事実。"""

    summary: str
    details: str
    source: Path | None


@dataclass(frozen=True, slots=True)
class _PathsSection:
    output_dir: Path
    sketch_dir: Path | None
    preset_module_dirs: tuple[Path, ...]
    font_dirs: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class _UiSection:
    window_pos_draw: tuple[int, int]
    window_pos_parameter_gui: tuple[int, int]
    parameter_gui_window_size: tuple[int, int]
    parameter_gui_fallback_font_japanese: str | None
    parameter_gui_font_size_base_px: float
    parameter_gui_shortcuts: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class _ExportSection:
    png_scale: float
    gcode: GCodeParams


_CURRENT_RUNTIME_CONFIG: ContextVar[RuntimeConfig | None] = ContextVar(
    "grafix_current_runtime_config",
    default=None,
)


@contextmanager
def bind_runtime_config(config: RuntimeConfig) -> Iterator[None]:
    """authoring scope に確定済みの実行時設定を束縛する。"""

    if type(config) is not RuntimeConfig:
        raise TypeError("config は exact RuntimeConfig である必要があります")
    token = _CURRENT_RUNTIME_CONFIG.set(config)
    try:
        yield
    finally:
        _CURRENT_RUNTIME_CONFIG.reset(token)


def current_runtime_config() -> RuntimeConfig:
    """現在の authoring scope に束縛された設定を返す。"""

    config = _CURRENT_RUNTIME_CONFIG.get()
    if config is None:
        raise RuntimeError("RuntimeConfig が現在の authoring scope に束縛されていません")
    return config


@contextmanager
def without_runtime_config() -> Iterator[None]:
    """内側の scope から outer authoring config を観測できなくする。"""

    token = _CURRENT_RUNTIME_CONFIG.set(None)
    try:
        yield
    finally:
        _CURRENT_RUNTIME_CONFIG.reset(token)


def _as_optional_path(value: Any) -> Path | None:
    """None または path 文字列を Path へ変換する。"""

    if value is None:
        return None
    if type(value) is not str:
        raise RuntimeError(f"path は文字列または None である必要があります: got={value!r}")
    s = value.strip()
    if not s:
        return None
    return Path(s)


def _as_optional_str(value: Any) -> str | None:
    """None または文字列を、空なら None として返す。"""

    if value is None:
        return None
    if type(value) is not str:
        raise RuntimeError(f"値は文字列または None である必要があります: got={value!r}")
    s = value.strip()
    return None if not s else s


def _as_path_list(value: Any) -> list[Path]:
    """path 文字列だけを含む list を Path 列へ変換する。"""

    if not isinstance(value, list):
        raise RuntimeError(f"path list は文字列の配列である必要があります: got={value!r}")
    out: list[Path] = []
    for index, item in enumerate(value):
        p = _as_optional_path(item)
        if p is None:
            raise RuntimeError(f"path list[{index}] は空でない文字列である必要があります")
        out.append(p)
    return out


def _as_mapping(value: Any, *, key: str) -> dict[str, Any]:
    """任意値を mapping として解釈し、dict に正規化して返す。"""

    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    raise RuntimeError(f"{key} は mapping である必要があります: got={value!r}")


def _as_int_pair(value: Any, *, key: str) -> tuple[int, int] | None:
    """任意値を (x, y) の整数ペアとして解釈して返す。"""

    if value is None:
        return None
    if not isinstance(value, list):
        raise RuntimeError(f"{key} は [x, y] の配列である必要があります: got={value!r}")
    seq = value
    if len(seq) != 2:
        raise RuntimeError(f"{key} は [x, y] の配列である必要があります: got={value!r}")
    x = _as_int(seq[0], key=f"{key}[0]")
    y = _as_int(seq[1], key=f"{key}[1]")
    if x is None or y is None:
        raise RuntimeError(f"{key} は [x, y] の整数配列である必要があります: got={value!r}")
    return (x, y)


def _as_float_pair(value: Any, *, key: str) -> tuple[float, float] | None:
    """任意値を (x, y) の float ペアとして解釈して返す。"""

    if value is None:
        return None
    if not isinstance(value, list):
        raise RuntimeError(f"{key} は [x, y] の配列である必要があります: got={value!r}")
    seq = value
    if len(seq) != 2:
        raise RuntimeError(f"{key} は [x, y] の配列である必要があります: got={value!r}")
    x = _as_float(seq[0], key=f"{key}[0]")
    y = _as_float(seq[1], key=f"{key}[1]")
    if x is None or y is None:
        raise RuntimeError(f"{key} は [x, y] の数値配列である必要があります: got={value!r}")
    return (x, y)


def _as_int(value: Any, *, key: str) -> int | None:
    """bool や他型を変換せず int を返す。"""

    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError(f"{key} は整数である必要があります: got={value!r}")
    return value


def _as_bool(value: Any, *, key: str) -> bool | None:
    """暗黙 truthiness 変換を行わず bool を返す。"""

    if value is None:
        return None
    if type(value) is not bool:
        raise RuntimeError(f"{key} は bool である必要があります: got={value!r}")
    return value


def _as_float(value: Any, *, key: str) -> float | None:
    """bool/文字列を変換せず有限実数を float で返す。"""

    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, Real):
        raise RuntimeError(f"{key} は数値である必要があります: got={value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{key} は finite な数値である必要があります: got={value!r}")
    return number


def _as_midi_inputs(value: Any) -> list[tuple[str, str]]:
    """midi.inputs を (port_name, mode) の list として解釈して返す。

    期待する形:
    - `[{port_name: "...", mode: "..."}, ...]`

    不正な要素は無視せず、index を含むエラーとして報告する。
    """

    if value is None:
        return []
    if not isinstance(value, list):
        raise RuntimeError(f"midi.inputs は mapping の配列である必要があります: got={value!r}")

    out: list[tuple[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise RuntimeError(f"midi.inputs[{index}] は mapping である必要があります")
        port_name = item.get("port_name")
        mode = item.get("mode")
        if port_name is None or mode is None:
            raise RuntimeError(f"midi.inputs[{index}] に port_name と mode が必要です")
        if not isinstance(port_name, str) or not isinstance(mode, str):
            raise RuntimeError(f"midi.inputs[{index}].port_name/mode は文字列である必要があります")
        port_s = port_name.strip()
        mode_s = mode.strip()
        if not port_s or not mode_s:
            raise RuntimeError(f"midi.inputs[{index}].port_name/mode に空文字は使えません")
        if mode_s not in _MIDI_MODES:
            raise ValueError(
                f"midi.inputs[{index}].mode は {_MIDI_MODES} のいずれかである必要があります"
                f": got={mode_s!r}"
            )
        out.append((port_s, mode_s))
    return out


def _unknown_key_message(
    *,
    key: object,
    parent: tuple[str, ...],
    known_keys: tuple[str, ...],
    source: str,
) -> str:
    """unknown key と同じ階層の近似候補を含むエラー文を作る。"""

    key_s = str(key)
    full_key = ".".join((*parent, key_s))
    matches = difflib.get_close_matches(key_s, known_keys, n=1, cutoff=0.55)
    suggestion = ""
    if matches:
        suggested_key = ".".join((*parent, matches[0]))
        suggestion = f"; 候補: {suggested_key!r}"
    return f"未定義の config key: {full_key!r} (source={source}){suggestion}"


def _validate_midi_item_keys(value: Any, *, source: str) -> None:
    """``midi.inputs`` の item key を merge 前に検証する。"""

    if not isinstance(value, list):
        return
    known_keys = ("port_name", "mode")
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            continue
        for key in item:
            if key not in known_keys:
                raise RuntimeError(
                    _unknown_key_message(
                        key=key,
                        parent=("midi", "inputs", str(index)),
                        known_keys=known_keys,
                        source=source,
                    )
                )


def _validate_known_key_tree(
    payload: dict[str, Any],
    *,
    schema: dict[str, Any],
    source: str,
    parent: tuple[str, ...] = (),
) -> None:
    """override の key が packaged default の既知 tree 内にあるか検証する。"""

    known_keys = tuple(str(key) for key in schema)
    for key, value in payload.items():
        if key not in schema:
            raise RuntimeError(
                _unknown_key_message(
                    key=key,
                    parent=parent,
                    known_keys=known_keys,
                    source=source,
                )
            )

        key_s = str(key)
        path = (*parent, key_s)
        schema_value = schema[key]
        if isinstance(value, dict) and isinstance(schema_value, dict):
            _validate_known_key_tree(
                value,
                schema=schema_value,
                source=source,
                parent=path,
            )
        elif path == ("midi", "inputs"):
            _validate_midi_item_keys(value, source=source)


def _validate_version(payload: dict[str, Any]) -> None:
    """設定 schema version を検証する。"""

    version = payload.get("version")
    if version is None:
        raise RuntimeError(
            "config.yaml の version が未設定です（同梱 default_config.yaml を確認してください）"
        )
    version_i = _as_int(version, key="config.yaml.version")
    assert version_i is not None
    if version_i != 1:
        raise RuntimeError(f"未対応の config.yaml version です: got={version_i}")


def _parse_paths_section(payload: dict[str, Any]) -> _PathsSection:
    """``paths`` section を検証して返す。"""

    paths = _as_mapping(payload.get("paths"), key="paths")
    output_dir = _as_optional_path(paths.get("output_dir"))
    if output_dir is None:
        raise RuntimeError(
            "paths.output_dir が未設定です（同梱 default_config.yaml を確認してください）"
        )
    return _PathsSection(
        output_dir=output_dir,
        sketch_dir=_as_optional_path(paths.get("sketch_dir")),
        preset_module_dirs=tuple(_as_path_list(paths.get("preset_module_dirs"))),
        font_dirs=tuple(_as_path_list(paths.get("font_dirs"))),
    )


def _parse_ui_section(payload: dict[str, Any]) -> _UiSection:
    """``ui`` section を検証して返す。"""

    ui = _as_mapping(payload.get("ui"), key="ui")
    window_positions = _as_mapping(ui.get("window_positions"), key="ui.window_positions")

    window_pos_draw = _as_int_pair(
        window_positions.get("draw"),
        key="ui.window_positions.draw",
    )
    if window_pos_draw is None:
        raise RuntimeError(
            "ui.window_positions.draw が未設定です（同梱 default_config.yaml を確認してください）"
        )

    window_pos_parameter_gui = _as_int_pair(
        window_positions.get("parameter_gui"),
        key="ui.window_positions.parameter_gui",
    )
    if window_pos_parameter_gui is None:
        raise RuntimeError(
            "ui.window_positions.parameter_gui が未設定です"
            "（同梱 default_config.yaml を確認してください）"
        )

    parameter_gui = _as_mapping(ui.get("parameter_gui"), key="ui.parameter_gui")
    window_size = _as_int_pair(
        parameter_gui.get("window_size"),
        key="ui.parameter_gui.window_size",
    )
    if window_size is None:
        raise RuntimeError(
            "ui.parameter_gui.window_size が未設定です"
            "（同梱 default_config.yaml を確認してください）"
        )
    if window_size[0] <= 0 or window_size[1] <= 0:
        raise ValueError(
            f"ui.parameter_gui.window_size は正の整数ペアである必要があります: got={window_size}"
        )

    font_size = _as_float(
        parameter_gui.get("font_size_base_px"),
        key="ui.parameter_gui.font_size_base_px",
    )
    if font_size is None:
        font_size = float(_PARAMETER_GUI_FONT_SIZE_BASE_PX_DEFAULT)
    if font_size <= 0.0:
        raise ValueError(
            f"ui.parameter_gui.font_size_base_px は正の値である必要があります: got={font_size}"
        )

    shortcut_values = _as_mapping(
        parameter_gui.get("shortcuts"),
        key="ui.parameter_gui.shortcuts",
    )
    shortcuts: list[tuple[str, str]] = []
    for action in _PARAMETER_GUI_SHORTCUT_ACTIONS:
        raw_key_name = shortcut_values.get(action)
        if type(raw_key_name) is not str:
            raise ValueError(
                f"ui.parameter_gui.shortcuts.{action} はpyglet key名である必要があります"
            )
        key_name = raw_key_name.strip().upper()
        if not key_name or not key_name.replace("_", "").isalnum():
            raise ValueError(
                f"ui.parameter_gui.shortcuts.{action} はpyglet key名である必要があります"
            )
        shortcuts.append((action, key_name))

    return _UiSection(
        window_pos_draw=window_pos_draw,
        window_pos_parameter_gui=window_pos_parameter_gui,
        parameter_gui_window_size=window_size,
        parameter_gui_fallback_font_japanese=_as_optional_str(
            parameter_gui.get("fallback_font_japanese")
        ),
        parameter_gui_font_size_base_px=float(font_size),
        parameter_gui_shortcuts=tuple(shortcuts),
    )


def _parse_gcode_section(gcode: dict[str, Any]) -> GCodeParams:
    """``export.gcode`` section を検証して返す。"""

    for removed_key in ("origin", "canvas_height_mm"):
        if removed_key in gcode:
            raise RuntimeError(
                f"export.gcode.{removed_key} は廃止されました"
                "（paper_bottom_right_mm へ移行してください）"
            )

    required_keys = (
        "travel_feed",
        "draw_feed",
        "z_up",
        "z_down",
        "y_down",
        "paper_bottom_right_mm",
        "decimals",
        "paper_margin_mm",
        "bed_x_range",
        "bed_y_range",
        "bridge_draw_distance",
        "optimize_travel",
        "allow_reverse",
    )
    missing_keys = [key for key in required_keys if key not in gcode]
    if missing_keys:
        raise RuntimeError(
            "export.gcode が未設定、または必須キーが不足しています"
            "（再帰 merge 後の config.yaml を確認してください）"
            f": missing={missing_keys}"
        )

    travel_feed = _as_float(gcode.get("travel_feed"), key="export.gcode.travel_feed")
    if travel_feed is None:
        raise RuntimeError(
            "export.gcode.travel_feed が未設定です（同梱 default_config.yaml を確認してください）"
        )
    draw_feed = _as_float(gcode.get("draw_feed"), key="export.gcode.draw_feed")
    if draw_feed is None:
        raise RuntimeError(
            "export.gcode.draw_feed が未設定です（同梱 default_config.yaml を確認してください）"
        )
    if travel_feed <= 0.0:
        raise ValueError(
            f"export.gcode.travel_feed は正の値である必要があります: got={travel_feed}"
        )
    if draw_feed <= 0.0:
        raise ValueError(f"export.gcode.draw_feed は正の値である必要があります: got={draw_feed}")
    z_up = _as_float(gcode.get("z_up"), key="export.gcode.z_up")
    if z_up is None:
        raise RuntimeError(
            "export.gcode.z_up が未設定です（同梱 default_config.yaml を確認してください）"
        )
    z_down = _as_float(gcode.get("z_down"), key="export.gcode.z_down")
    if z_down is None:
        raise RuntimeError(
            "export.gcode.z_down が未設定です（同梱 default_config.yaml を確認してください）"
        )
    y_down = _as_bool(gcode.get("y_down"), key="export.gcode.y_down")
    if y_down is None:
        raise RuntimeError(
            "export.gcode.y_down が未設定です（同梱 default_config.yaml を確認してください）"
        )
    paper_bottom_right_mm = _as_float_pair(
        gcode.get("paper_bottom_right_mm"),
        key="export.gcode.paper_bottom_right_mm",
    )
    if paper_bottom_right_mm is None:
        raise RuntimeError(
            "export.gcode.paper_bottom_right_mm が未設定です"
            "（同梱 default_config.yaml を確認してください）"
        )
    decimals = _as_int(gcode.get("decimals"), key="export.gcode.decimals")
    if decimals is None:
        raise RuntimeError(
            "export.gcode.decimals が未設定です（同梱 default_config.yaml を確認してください）"
        )
    if decimals < 0:
        raise ValueError(f"export.gcode.decimals は 0 以上である必要があります: got={decimals}")

    paper_margin_mm = _as_float(
        gcode.get("paper_margin_mm"),
        key="export.gcode.paper_margin_mm",
    )
    if paper_margin_mm is None:
        raise RuntimeError(
            "export.gcode.paper_margin_mm が未設定です"
            "（同梱 default_config.yaml を確認してください）"
        )
    if paper_margin_mm < 0.0:
        raise ValueError(
            f"export.gcode.paper_margin_mm は 0 以上である必要があります: got={paper_margin_mm}"
        )

    bridge_draw_distance = _as_float(
        gcode.get("bridge_draw_distance"),
        key="export.gcode.bridge_draw_distance",
    )
    if bridge_draw_distance is not None and bridge_draw_distance < 0.0:
        raise ValueError(
            "export.gcode.bridge_draw_distance は 0 以上である必要があります"
            f": got={bridge_draw_distance}"
        )

    bed_x_range = _as_float_pair(
        gcode.get("bed_x_range"),
        key="export.gcode.bed_x_range",
    )
    if bed_x_range is not None and bed_x_range[0] >= bed_x_range[1]:
        raise ValueError(
            f"export.gcode.bed_x_range は [min, max] の昇順である必要があります: got={bed_x_range}"
        )
    bed_y_range = _as_float_pair(
        gcode.get("bed_y_range"),
        key="export.gcode.bed_y_range",
    )
    if bed_y_range is not None and bed_y_range[0] >= bed_y_range[1]:
        raise ValueError(
            f"export.gcode.bed_y_range は [min, max] の昇順である必要があります: got={bed_y_range}"
        )

    optimize_travel = _as_bool(
        gcode.get("optimize_travel"),
        key="export.gcode.optimize_travel",
    )
    if optimize_travel is None:
        raise RuntimeError(
            "export.gcode.optimize_travel が未設定です"
            "（同梱 default_config.yaml を確認してください）"
        )
    allow_reverse = _as_bool(
        gcode.get("allow_reverse"),
        key="export.gcode.allow_reverse",
    )
    if allow_reverse is None:
        raise RuntimeError(
            "export.gcode.allow_reverse が未設定です（同梱 default_config.yaml を確認してください）"
        )

    return GCodeParams(
        travel_feed=travel_feed,
        draw_feed=draw_feed,
        z_up=z_up,
        z_down=z_down,
        y_down=y_down,
        paper_bottom_right_mm=paper_bottom_right_mm,
        decimals=decimals,
        paper_margin_mm=paper_margin_mm,
        bed_x_range=bed_x_range,
        bed_y_range=bed_y_range,
        bridge_draw_distance=bridge_draw_distance,
        optimize_travel=optimize_travel,
        allow_reverse=allow_reverse,
    )


def _parse_export_section(payload: dict[str, Any]) -> _ExportSection:
    """``export`` section を検証して返す。"""

    export = _as_mapping(payload.get("export"), key="export")
    png = _as_mapping(export.get("png"), key="export.png")
    png_scale = _as_float(png.get("scale"), key="export.png.scale")
    if png_scale is None:
        raise RuntimeError(
            "export.png.scale が未設定です（同梱 default_config.yaml を確認してください）"
        )
    if png_scale <= 0.0:
        raise ValueError(f"export.png.scale は正の値である必要があります: got={png_scale}")

    gcode = _as_mapping(export.get("gcode"), key="export.gcode")
    return _ExportSection(
        png_scale=float(png_scale),
        gcode=_parse_gcode_section(gcode),
    )


def _parse_midi_section(payload: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """``midi`` section を検証して入力設定を返す。"""

    midi = _as_mapping(payload.get("midi"), key="midi")
    return tuple(_as_midi_inputs(midi.get("inputs")))


def validate_runtime_config_override(
    payload: Mapping[str, Any],
    *,
    schema: Mapping[str, Any],
    source: str,
) -> None:
    """override mapping の key tree を I/O 無しで検証する。"""

    if not isinstance(payload, Mapping):
        raise TypeError("payload は Mapping である必要があります")
    if not isinstance(schema, Mapping):
        raise TypeError("schema は Mapping である必要があります")
    if type(source) is not str or not source:
        raise TypeError("source は空でない str である必要があります")
    _validate_known_key_tree(dict(payload), schema=dict(schema), source=source)


def runtime_config_from_mapping(
    payload: Mapping[str, Any],
    *,
    config_path: Path | None = None,
) -> RuntimeConfig:
    """解決済み mapping を I/O 無しで厳密検証し、設定値へ変換する。"""

    if not isinstance(payload, Mapping):
        raise TypeError("payload は Mapping である必要があります")
    if config_path is not None and not isinstance(config_path, Path):
        raise TypeError("config_path は Path または None です")
    normalized = dict(payload)
    _validate_version(normalized)
    paths = _parse_paths_section(normalized)
    ui = _parse_ui_section(normalized)
    export = _parse_export_section(normalized)
    midi_inputs = _parse_midi_section(normalized)
    return RuntimeConfig(
        config_path=config_path,
        output_dir=paths.output_dir,
        sketch_dir=paths.sketch_dir,
        preset_module_dirs=paths.preset_module_dirs,
        font_dirs=paths.font_dirs,
        window_pos_draw=ui.window_pos_draw,
        window_pos_parameter_gui=ui.window_pos_parameter_gui,
        parameter_gui_window_size=ui.parameter_gui_window_size,
        parameter_gui_fallback_font_japanese=(ui.parameter_gui_fallback_font_japanese),
        parameter_gui_font_size_base_px=ui.parameter_gui_font_size_base_px,
        parameter_gui_shortcuts=ui.parameter_gui_shortcuts,
        png_scale=export.png_scale,
        gcode=export.gcode,
        midi_inputs=midi_inputs,
    )


__all__ = [
    "RuntimeConfig",
    "RuntimeConfigFallback",
    "RuntimeConfigReport",
    "RuntimeConfigValue",
    "bind_runtime_config",
    "current_runtime_config",
    "runtime_config_from_mapping",
    "validate_runtime_config_override",
    "without_runtime_config",
]
