# どこで: `src/grafix/interactive/midi/midi_controller.py`。
# 何を: MIDI 入力から CC 値を `dict[int, float]` として管理・永続化する。
# なぜ: Parameter 解決で使う `cc_snapshot` を、外部デバイス入力から供給するため。

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path

_DEFAULT_SAVE_DIR = Path("data") / "output" / "midi_cc"


def _sanitize_filename_fragment(text: str) -> str:
    """ファイル名に埋め込めるように text を正規化して返す。"""

    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", str(text))
    normalized = normalized.strip("._-")
    return normalized or "unknown"


def _default_profile_name() -> str:
    """実行スクリプト名から profile 名を推定して返す。"""

    argv0 = sys.argv[0] if sys.argv else ""
    stem = Path(str(argv0)).stem if argv0 else ""
    return stem or "unknown"


def default_cc_snapshot_path(
    *, port_name: str, profile_name: str, save_dir: Path | None
) -> Path:
    """CC スナップショットの既定保存パスを返す。"""

    base = save_dir if save_dir is not None else _DEFAULT_SAVE_DIR
    port_fragment = _sanitize_filename_fragment(port_name)
    profile_fragment = _sanitize_filename_fragment(profile_name)
    return base / f"{profile_fragment}_{port_fragment}.json"


def load_cc_snapshot(path: Path) -> dict[int, float]:
    """CC スナップショットを JSON からロードして返す。無ければ空 dict を返す。"""

    try:
        payload = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError:
        return {}

    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return {}

    if isinstance(data, dict) and "cc" in data:
        cc_raw = data.get("cc")
    else:
        cc_raw = data

    if not isinstance(cc_raw, dict):
        return {}

    out: dict[int, float] = {}
    for key, value in cc_raw.items():
        try:
            cc_number = int(key)
            out[cc_number] = float(value)
        except Exception:
            continue
    return out


def save_cc_snapshot(snapshot: dict[int, float], path: Path) -> None:
    """CC スナップショットを JSON として保存する。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"cc": {str(k): float(v) for k, v in sorted(snapshot.items())}}
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


class InvalidPortError(Exception):
    """要求された MIDI ポート名が存在しない場合に送出される例外。"""


class MidiController:
    """MIDI 入力ポートを開き、CC 値のスナップショットを管理する。

    - CC 値は `dict[int, float]` に保持し、値域は 0.0–1.0 に正規化する。
    - 14bit CC は MSB/LSB の 2 メッセージを合成し、0–16383 を 0.0–1.0 に正規化する。

    Parameters
    ----------
    port_name
        入力ポート名。
    mode
        `"7bit"` または `"14bit"`。
    profile_name
        永続化ファイル名に埋め込む profile 名。未指定時は実行スクリプト名から推定する。
    save_dir
        永続化ディレクトリ。未指定時は `data/output/midi_cc/` を使う。
    inport
        既存の入力ポート（テスト用）。指定時は mido を使ってポートを開かない。
    """

    MSB_THRESHOLD = 32
    MAX_7BIT_VAL = 127
    MAX_14BIT_VAL = 16383

    def __init__(
        self,
        port_name: str,
        *,
        mode: str = "7bit",
        profile_name: str | None = None,
        save_dir: Path | None = None,
        inport: object | None = None,
    ) -> None:
        if mode not in ("7bit", "14bit"):
            raise ValueError(f"unknown mode: {mode!r}")

        self.port_name = str(port_name)
        self.mode = str(mode)
        self.profile_name = (
            str(profile_name) if profile_name is not None else _default_profile_name()
        )
        self._save_dir = save_dir
        self._path = default_cc_snapshot_path(
            port_name=self.port_name,
            profile_name=self.profile_name,
            save_dir=self._save_dir,
        )

        self._logger = logging.getLogger(__name__)
        self._msb_by_cc: dict[int, int] = {}
        self.cc: dict[int, float] = {}

        self.inport = (
            inport
            if inport is not None
            else self.validate_and_open_port(self.port_name)
        )
        self.load()

    @property
    def path(self) -> Path:
        """永続化ファイルのパスを返す。"""

        return self._path

    def load(self) -> None:
        """永続化ファイルから CC スナップショットをロードして反映する。"""

        self.cc = load_cc_snapshot(self._path)

    def save(self) -> None:
        """現在の CC スナップショットを永続化ファイルへ保存する。"""

        save_cc_snapshot(self.cc, self._path)

    def snapshot(self) -> dict[int, float]:
        """現在の CC スナップショット（コピー）を返す。"""

        return dict(self.cc)

    def iter_pending(self):
        """入力ポートの pending メッセージを返す（mido の API に準拠）。"""

        if self.inport is None:
            return iter(())
        return self.inport.iter_pending()  # type: ignore[attr-defined]

    def poll_pending(self, *, max_messages: int | None = None) -> int:
        """pending メッセージを取り出して処理し、CC 更新回数を返す。"""

        updated = 0
        for i, msg in enumerate(self.iter_pending()):
            if max_messages is not None and i >= max_messages:
                break
            if self.update(msg):
                updated += 1
        return updated

    def update(self, msg: object) -> bool:
        """MIDI メッセージを 1 つ処理し、CC が更新されたら True を返す。"""

        if getattr(msg, "type", None) != "control_change":
            return False
        try:
            control = int(getattr(msg, "control"))
            value = int(getattr(msg, "value"))
        except Exception:
            return False
        return self.update_cc(control=control, value=value)

    def update_cc(self, *, control: int, value: int) -> bool:
        """CC メッセージを処理し、CC が更新されたら True を返す。"""

        if self.mode == "7bit":
            self.cc[int(control)] = float(value) / float(self.MAX_7BIT_VAL)
            return True

        control_i = int(control)
        value_i = int(value)

        if control_i < self.MSB_THRESHOLD:
            self._msb_by_cc[control_i] = value_i
            return False

        msb_cc = control_i - self.MSB_THRESHOLD
        msb = self._msb_by_cc.get(msb_cc)
        if msb is None:
            return False

        value_14bit = (int(msb) << 7) | value_i
        self.cc[int(msb_cc)] = float(value_14bit) / float(self.MAX_14BIT_VAL)
        return True

    def close(self) -> None:
        """入力ポートを close する（対応していれば）。"""

        inport = self.inport
        self.inport = None
        if inport is None:
            return
        close = getattr(inport, "close", None)
        if callable(close):
            close()

    @staticmethod
    def validate_and_open_port(port_name: str):
        """ポート名を検証して入力ポートを開く。"""

        import mido  # type: ignore

        if port_name in mido.get_input_names():  # type: ignore
            return mido.open_input(port_name)  # type: ignore
        MidiController.handle_invalid_port_name(port_name)

    @staticmethod
    def handle_invalid_port_name(port_name: str) -> None:
        """InvalidPortError を送出する（利用可能ポート名も含める）。"""

        import mido  # type: ignore

        available = mido.get_input_names()  # type: ignore
        raise InvalidPortError(
            f"Invalid port name: {port_name}. Available: {available}"
        )

    @staticmethod
    def show_available_ports() -> None:
        """利用可能な MIDI 入出力ポート名をログへ出す。"""

        import mido  # type: ignore

        logger = logging.getLogger(__name__)
        logger.info("Available ports:")
        logger.info("  input: %s", mido.get_input_names())  # type: ignore
        logger.info("  output: %s", mido.get_output_names())  # type: ignore


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    MidiController.show_available_ports()
