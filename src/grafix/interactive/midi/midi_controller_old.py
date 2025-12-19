from __future__ import annotations

"""
MIDI コントローラ入出力（IO モジュール）

本モジュールは、外部 MIDI デバイスからの入力を扱い、CC 値を統一的に管理・永続化する
ための軽量ユーティリティを提供する。7bit/14bit のコントロールチェンジ（CC）を抽象化し、
アプリ側では数値（0.0–1.0）として参照できるように正規化する。

主な責務:
- MIDI 入力ポートの検証とオープン（存在しない場合は `InvalidPortError`）。
- CC 値の受信・更新（7bit/14bit を判別し 0–127 にスケール → 0.0–1.0 へ正規化）。
- CC 値のスナップショットを JSON で保存/読み込み（スクリプト名・ポート名で分離）。
- 一部デバイス（例: Intech Grid）向けのノブ初期同期メッセージ送出。
- デバッグ出力のオン/オフ切り替え。

設計メモ:
- 14bit CC は MSB/LSB の 2 メッセージから 0–16383 を組み立て、0–127 に線形スケール後
  0.0–1.0 に正規化する。本モジュールでは整数演算を尊重しつつ最終的に float で保持する。
- CC 値は `DualKeyDict` を用いて「数値 CC 番号」「論理名」の両方から参照できる。
- 永続化ファイルは `data/cc/` 配下に保存し（プロジェクトルート相対）、壊れている場合は
  安全側で初期化。保存先は設定ファイルの `io.cc_dir` で上書き可能。

使用例:
    from engine.io.controller import MidiController
    ctrl = MidiController(port_name, cc_map, mode="14bit")
    for msg in ctrl.iter_pending():
        ctrl.update_cc(msg)
        # cc 値は ctrl.cc[<cc_number or name>] で取得

注意:
- 本モジュールは I/O レイヤのユーティリティであり、レンダリングや幾何処理には依存しない。
- ログ/例外メッセージは実行時の利便性を優先し、最小限の英語出力を残す場合がある。
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from util.utils import load_config

from .helpers import DualKeyDict

# mido はローカル import 方針（使用箇所で import）


_SNAPSHOT_KEY = "by_name"

# from midi.ui.controllers.tx6 import TX6Dict


class InvalidPortError(Exception):
    """要求された MIDI ポート名が存在しない場合に送出される例外。"""


class MidiController:
    MSB_THRESHOLD = 32  # 14ビットのコントロールチェンジメッセージのMSBの値は32以下
    SCALED_14BIT_MIN = 0  # 14ビットのMIDI値をスケール変換したときの最小値
    SCALED_14BIT_MAX = 127  # 14ビットのMIDI値をスケール変換したときの最大値
    MAX_14BIT_VAL = 16383  # 14ビットのMIDI値の最大値

    # 既定の保存ディレクトリ（シンプル方針）。
    # 優先度:
    # 1) 設定 `io.cc_dir`（絶対/相対は実行CWD基準）
    # 2) フォールバック `CWD/data/cc`
    @staticmethod
    def _default_save_dir() -> Path:
        cfg = load_config() or {}
        cc_dir = cfg.get("io", {}).get("cc_dir") if isinstance(cfg, dict) else None
        if cc_dir:
            return Path(str(cc_dir))
        # フォールバック: 実行ディレクトリ配下
        return Path.cwd() / "data" / "cc"

    SAVE_DIR = _default_save_dir.__func__()  # type: ignore[attr-defined]

    def __init__(self, port_name, cc_map, mode):
        self.port_name = port_name
        self.cc_map = cc_map
        self.inport = self.validate_and_open_port(port_name)
        self.mode = mode
        self.msb_values = {}
        self.cc = self.init_cc()

        self.sync_grid_knob_values()
        self.debug_enabled = False
        self._logger = logging.getLogger(__name__)

    def __repr__(self):
        return f"MidiController(port_name={self.port_name}, mode={self.mode})"

    def init_cc(self):
        try:
            cc = self.load_cc()
            cc.reset_activation()
        except (
            FileNotFoundError,
            json.JSONDecodeError,
            ValueError,
            AttributeError,
            TypeError,
        ):
            # 既存の保存がない/壊れている場合は新規初期化
            cc = DualKeyDict()
            cc.init_map(self.cc_map)
        return cc

    def load_cc(self):
        """JSON から CC 値を読み込む。"""
        script_name = os.path.basename(sys.argv[0])[:-3]  # .py を除いたスクリプト名
        file_name = f"{script_name}_{self.port_name}.json"
        MidiController.SAVE_DIR.mkdir(parents=True, exist_ok=True)
        path = MidiController.SAVE_DIR / file_name
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        cc = DualKeyDict()
        cc.init_map(self.cc_map)

        try:
            values_by_name = data[_SNAPSHOT_KEY]
        except KeyError as exc:
            raise ValueError("missing snapshot key") from exc
        if not isinstance(values_by_name, dict):
            raise ValueError("snapshot must be a dict keyed by name")

        for name, value in values_by_name.items():
            if isinstance(name, str):
                try:
                    cc[name] = float(value)
                except (KeyError, ValueError, TypeError):
                    # 無効なキー/値は無視（安全側）
                    pass
        return cc

    def save_cc(self):
        """CC 値を JSON に保存する。"""
        script_name = os.path.basename(sys.argv[0])[:-3]  # .py を除いたスクリプト名
        save_name = f"{script_name}_{self.port_name}.json"
        MidiController.SAVE_DIR.mkdir(parents=True, exist_ok=True)

        # DualKeyDict から名前ベースでスナップショットを作成
        values_by_name = {}
        if hasattr(self.cc, "_str_to_value"):
            for name, value in self.cc._str_to_value.items():
                try:
                    values_by_name[name] = float(value)
                except (ValueError, TypeError):
                    continue

        data = {_SNAPSHOT_KEY: values_by_name}
        with open(MidiController.SAVE_DIR / save_name, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def update_cc(self, msg):
        """
        MIDIメッセージを受け取り、self.ccの値を更新する。
        port_nameに応じてccの範囲を変更する。
        """
        result = self.process_midi_message(msg)
        if result and "type" in result and result["type"] in ["CC(14bit)", "CC(7bit)"]:
            result["value"] /= 127
            cc_number = result["CC number"]
            self.cc[cc_number] = result["value"]
            if self.debug_enabled:
                self._logger.debug("CC updated: %s", dict(self.cc))

    @staticmethod
    def validate_and_open_port(port_name):
        import mido  # type: ignore

        if port_name in mido.get_input_names():  # type: ignore
            return mido.open_input(port_name)  # type: ignore
        else:
            MidiController.handle_invalid_port_name(port_name)

    @staticmethod
    def handle_invalid_port_name(port_name: str) -> None:
        logger = logging.getLogger(__name__)
        import mido  # type: ignore

        available = mido.get_input_names()  # type: ignore
        logger.error("Invalid port name: %s", port_name)
        logger.info("Available input ports: %s", available)
        raise InvalidPortError(
            f"Invalid port name: {port_name}. Available: {available}"
        )

    def process_midi_message(self, msg: Any) -> dict | None:
        if msg.type == "control_change":  # type: ignore
            return self.handle_control_change(msg)
        elif msg.type in ["note_on", "note_off"]:  # type: ignore
            return {"type": "Note", "note": msg.note, "velocity": msg.velocity}  # type: ignore
        else:
            return None

    def handle_control_change(self, msg):
        if self.mode == "14bit":
            return self.process_14bit_control_change(msg)
        elif self.mode == "7bit":
            return self.process_7bit_control_change(msg)

    def process_14bit_control_change(self, msg: Any) -> dict | None:
        control_change_number = msg.control  # type: ignore

        if control_change_number < self.MSB_THRESHOLD:  # MSB
            # MSB を保持して LSB を待つ
            self.msb_values[control_change_number] = msg.value  # type: ignore
            return None  # LSBがまだ届いていないため、何もしない
        else:  # LSB
            return self.calc_combined_value(control_change_number, msg.value)  # type: ignore

    def process_7bit_control_change(self, msg: Any) -> dict:
        return {"type": "CC(7bit)", "CC number": msg.control, "value": msg.value}  # type: ignore

    def calc_combined_value(
        self, control_change_number: int, value: int
    ) -> dict | None:
        msb_control = control_change_number - MidiController.MSB_THRESHOLD
        if msb_control in self.msb_values:
            # MSB と LSB から 14 ビット値を計算
            msb = self.msb_values[msb_control]
            lsb = value
            value_14bit = (msb << 7) | lsb
            scaled_value_14bit = MidiController.scale_value(
                value_14bit,
                MidiController.SCALED_14BIT_MIN,
                MidiController.SCALED_14BIT_MAX,
            )
            return {
                "type": "CC(14bit)",
                "CC number": msb_control,
                "value": scaled_value_14bit,
            }
        # まだ対応する MSB が受信されていない場合は None を返す
        return None

    @staticmethod
    def scale_value(value_14bit: int, min_val: float, max_val: float) -> float:
        normalized = value_14bit / MidiController.MAX_14BIT_VAL
        return (
            min_val + (max_val - min_val) * normalized
        )  # 正規化された値を新しい範囲にスケール変換

    def set_debug(self, debug: bool):
        self.debug_enabled = debug

    def sync_grid_knob_values(self):
        import mido  # type: ignore

        names = [name for name in mido.get_output_names() if "Intech Grid MIDI device" in name]  # type: ignore
        if not names:
            return
        grid_output_port_name = names[0]
        with mido.open_output(grid_output_port_name) as outport:  # type: ignore
            msg = mido.Message("control_change", channel=0, control=64, value=127)
            outport.send(msg)

    # 注: 旧プロパティ `enable_debug` は削除（直接 `debug_enabled` を使用する）

    @staticmethod
    def show_available_ports() -> None:
        logger = logging.getLogger(__name__)
        import mido  # type: ignore

        logger.info("Available ports:")
        logger.info("  input: %s", mido.get_input_names())  # type: ignore
        logger.info("  output: %s", mido.get_output_names())  # type: ignore

    def iter_pending(self) -> Any:
        """MIDIメッセージをイテレータとして返す。"""
        return self.inport.iter_pending()  # type: ignore


if __name__ == "__main__":
    # 外部依存なしで利用可能なポート一覧を出力（スタンドアロン実行用）
    MidiController.show_available_ports()
