# どこで: `src/grafix/interactive/midi/midi_controller.py`。
# 何を: MIDI 入力から CC 値を `dict[int, float]` として管理・永続化する。
# なぜ: Parameter 解決で使う `cc_snapshot` を、外部デバイス入力から供給するため。

from __future__ import annotations

import json
import logging
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NoReturn, Protocol, runtime_checkable

from grafix.core.lifecycle import CleanupErrors
from grafix.core.value_validation import exact_string, exact_string_choice
from grafix.file_io import atomic_write_text
from grafix.interactive.diagnostics import DiagnosticAction, DiagnosticEvent


MIDI_CC_SNAPSHOT_SCHEMA_VERSION = 1
CcSnapshotLoadStatus = Literal["loaded", "missing", "corrupt", "old", "future"]


def _cc_number(value: object, *, name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} は int である必要があります")
    if not 0 <= value <= 127:
        raise ValueError(f"{name} は 0..127 である必要があります")
    return value


def _cc_value(value: object, *, name: str) -> float:
    if type(value) is not float:
        raise TypeError(f"{name} は float である必要があります")
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} は有限な 0.0..1.0 である必要があります")
    return value


@runtime_checkable
class MidiInputPort(Protocol):
    """MidiController が所有する入力 port の最小 interface。"""

    def iter_pending(self) -> Iterable[object]:
        """未処理 message を返す。"""

    def close(self) -> None:
        """入力 port を閉じる。"""


def _validated_input_port(value: object) -> MidiInputPort:
    if (
        not isinstance(value, MidiInputPort)
        or not callable(value.iter_pending)
        or not callable(value.close)
    ):
        raise TypeError("inport は iter_pending()/close() を持つ必要があります")
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class CcSnapshotLoadResult:
    """CC snapshot の strict load 結果。"""

    values: tuple[tuple[int, float], ...]
    status: CcSnapshotLoadStatus
    source: Path
    diagnostic: DiagnosticEvent | None = None

    def __post_init__(self) -> None:
        if type(self.values) is not tuple:
            raise TypeError("values は (cc, value) の tuple である必要があります")
        previous_cc = -1
        for index, entry in enumerate(self.values):
            if type(entry) is not tuple or len(entry) != 2:
                raise TypeError(f"values[{index}] は (cc, value) tuple である必要があります")
            cc = _cc_number(entry[0], name=f"values[{index}].cc")
            value = _cc_value(entry[1], name=f"values[{index}].value")
            if cc <= previous_cc:
                raise ValueError("values の CC は昇順かつ一意である必要があります")
            previous_cc = cc
            if type(entry[1]) is not float or entry != (cc, value):
                raise TypeError(f"values[{index}] は canonical 値である必要があります")
        exact_string_choice(
            self.status,
            name="status",
            choices=("loaded", "missing", "corrupt", "old", "future"),
        )
        if not isinstance(self.source, Path):
            raise TypeError("source は Path である必要があります")
        if self.diagnostic is not None and not isinstance(
            self.diagnostic,
            DiagnosticEvent,
        ):
            raise TypeError("diagnostic は DiagnosticEvent または None である必要があります")
        if self.status in {"loaded", "missing"}:
            if self.diagnostic is not None:
                raise ValueError("loaded/missing result に diagnostic は指定できません")
            if self.status == "missing" and self.values:
                raise ValueError("missing result の values は空である必要があります")
        elif self.values or self.diagnostic is None:
            raise ValueError("reject result は空 values と diagnostic を持つ必要があります")

    @property
    def writable(self) -> bool:
        """既存原本を通常 save で更新してよい場合に True を返す。"""

        return self.status in {"loaded", "missing"}

    def as_dict(self) -> dict[int, float]:
        """controller が所有できる mutable copy を返す。"""

        return dict(self.values)


class CcSnapshotWriteBlockedError(RuntimeError):
    """reject した snapshot 原本への暗黙上書きを表す例外。"""


class MidiConnectionError(RuntimeError):
    """MIDI input port の取得または iterator 処理失敗を表す。"""


def _contains_japanese(text: str) -> bool:
    """text が日本語文字（ひらがな/カタカナ/漢字）を含むなら True を返す。"""

    for ch in text:
        code = ord(ch)
        if 0x3040 <= code <= 0x30FF:  # Hiragana/Katakana
            return True
        if 0x4E00 <= code <= 0x9FFF:  # CJK Unified Ideographs
            return True
    return False


def _restore_macos_mojibake(text: str) -> str:
    """MacRoman として誤解釈された UTF-8 の文字化けを復元して返す。"""

    try:
        restored = text.encode("mac_roman").decode("utf-8")
    except Exception:
        return text

    if restored == text:
        return text
    if _contains_japanese(restored) and not _contains_japanese(text):
        return restored
    return text


def _require_snapshot_path(value: object) -> Path:
    """composition root で確定済みの snapshot path だけを受け取る。"""

    if not isinstance(value, Path):
        raise TypeError("snapshot_path は Path である必要があります")
    return value


def _snapshot_diagnostic(
    *,
    status: Literal["corrupt", "old", "future"],
    path: Path,
    details: str,
) -> DiagnosticEvent:
    summaries = {
        "corrupt": "MIDI CC snapshot が破損しているため値を復元しません",
        "old": "古い MIDI CC snapshot のため値を復元しません",
        "future": "未対応の MIDI CC snapshot のため値を復元しません",
    }
    return DiagnosticEvent(
        category="midi",
        severity="warning",
        summary=summaries[status],
        details=details,
        source=str(path),
        actions=(DiagnosticAction("discard", "Clear saved snapshot"),),
        dedupe_key=f"midi-snapshot-{status}:{path}",
    )


def _rejected_snapshot(
    *,
    status: Literal["corrupt", "old", "future"],
    path: Path,
    details: str,
) -> CcSnapshotLoadResult:
    return CcSnapshotLoadResult(
        values=(),
        status=status,
        source=path,
        diagnostic=_snapshot_diagnostic(
            status=status,
            path=path,
            details=details,
        ),
    )


def _decode_cc_snapshot(data: object) -> tuple[tuple[int, float], ...]:
    if type(data) is not dict:
        raise ValueError("MIDI CC snapshot は object である必要があります")
    if set(data) != {"schema_version", "values"}:
        raise ValueError(
            "MIDI CC snapshot は schema_version/values だけを含む必要があります"
        )
    values = data["values"]
    if type(values) is not list:
        raise ValueError("MIDI CC snapshot values は array である必要があります")

    decoded: list[tuple[int, float]] = []
    previous_cc = -1
    for index, raw_entry in enumerate(values):
        if type(raw_entry) is not dict or set(raw_entry) != {"cc", "value"}:
            raise ValueError(
                f"MIDI CC snapshot values[{index}] は cc/value record である必要があります"
            )
        cc = _cc_number(raw_entry["cc"], name=f"values[{index}].cc")
        value = _cc_value(raw_entry["value"], name=f"values[{index}].value")
        if cc <= previous_cc:
            raise ValueError("MIDI CC snapshot values は CC 昇順かつ一意である必要があります")
        previous_cc = cc
        decoded.append((cc, value))
    return tuple(decoded)


def _json_object_without_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    decoded: dict[str, object] = {}
    for key, value in pairs:
        if key in decoded:
            raise ValueError(f"MIDI CC snapshot に重複 key があります: {key!r}")
        decoded[key] = value
    return decoded


def load_cc_snapshot(snapshot_path: Path) -> CcSnapshotLoadResult:
    """CC snapshot を現行 schema だけから診断付きでロードする。"""

    path = _require_snapshot_path(snapshot_path)
    try:
        payload = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return CcSnapshotLoadResult(values=(), status="missing", source=path)
    except (OSError, UnicodeError) as exc:
        return _rejected_snapshot(
            status="corrupt",
            path=path,
            details=f"{type(exc).__name__}: {exc}",
        )

    try:
        data = json.loads(
            payload,
            object_pairs_hook=_json_object_without_duplicate_keys,
        )
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        return _rejected_snapshot(
            status="corrupt",
            path=path,
            details=f"{type(exc).__name__}: {exc}",
        )
    if type(data) is not dict:
        return _rejected_snapshot(
            status="corrupt",
            path=path,
            details="top-level value は object である必要があります",
        )

    if "schema_version" not in data:
        return _rejected_snapshot(
            status="old",
            path=path,
            details="schema_version がありません",
        )
    version = data["schema_version"]
    if type(version) is not int:
        return _rejected_snapshot(
            status="corrupt",
            path=path,
            details="schema_version は int である必要があります",
        )
    if version < MIDI_CC_SNAPSHOT_SCHEMA_VERSION:
        return _rejected_snapshot(
            status="old",
            path=path,
            details=f"schema_version={version}",
        )
    if version > MIDI_CC_SNAPSHOT_SCHEMA_VERSION:
        return _rejected_snapshot(
            status="future",
            path=path,
            details=f"schema_version={version}",
        )

    try:
        values = _decode_cc_snapshot(data)
    except (TypeError, ValueError) as exc:
        return _rejected_snapshot(
            status="corrupt",
            path=path,
            details=f"{type(exc).__name__}: {exc}",
        )
    return CcSnapshotLoadResult(values=values, status="loaded", source=path)


def _normalized_snapshot_values(
    snapshot: dict[int, float],
) -> tuple[tuple[int, float], ...]:
    if type(snapshot) is not dict:
        raise TypeError("snapshot は dict[int, float] である必要があります")
    normalized: list[tuple[int, float]] = []
    for raw_cc, raw_value in snapshot.items():
        cc = _cc_number(raw_cc, name="snapshot cc")
        value = _cc_value(raw_value, name=f"snapshot[{cc}]")
        normalized.append((cc, value))
    return tuple(sorted(normalized, key=lambda item: item[0]))


def _snapshot_records(snapshot: dict[int, float]) -> list[dict[str, int | float]]:
    return [
        {"cc": cc, "value": value}
        for cc, value in _normalized_snapshot_values(snapshot)
    ]


def save_cc_snapshot(snapshot: dict[int, float], snapshot_path: Path) -> None:
    """CC snapshot を現行 schema 一形で atomic 保存する。"""

    path = _require_snapshot_path(snapshot_path)
    payload = {
        "schema_version": MIDI_CC_SNAPSHOT_SCHEMA_VERSION,
        "values": _snapshot_records(snapshot),
    }
    atomic_write_text(
        path,
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
    )


def load_frozen_cc_snapshot(*, snapshot_path: Path) -> CcSnapshotLoadResult:
    """接続不能時に使う CC snapshot を確定済み path からロードする。"""

    return load_cc_snapshot(_require_snapshot_path(snapshot_path))


def maybe_load_frozen_cc_snapshot(
    *,
    port_name: str | None,
    controller: "MidiController | None",
    snapshot_path: Path,
) -> CcSnapshotLoadResult | None:
    """MIDI 接続に失敗した場合に限り、凍結 CC スナップショットを返す。

    - port_name=None（ユーザーが明示的に MIDI 無効）なら凍結しない。
    - controller が存在するなら live snapshot が使えるので凍結しない。
    """

    path = _require_snapshot_path(snapshot_path)
    if port_name is None:
        return None
    if controller is not None:
        return None
    return load_frozen_cc_snapshot(snapshot_path=path)


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
    snapshot_path
        composition root がこの session 用に一度だけ確定した永続化ファイルパス。
    mode
        `"7bit"` または `"14bit"`。
    inport
        既存の入力ポート。指定時は mido を使ってポートを開かない。
    """

    MSB_THRESHOLD = 32
    MAX_7BIT_VAL = 127
    MAX_14BIT_VAL = 16383

    def __init__(
        self,
        port_name: str,
        *,
        snapshot_path: Path,
        mode: str = "7bit",
        inport: MidiInputPort | None = None,
    ) -> None:
        port = exact_string(port_name, name="port_name")
        if not port:
            raise ValueError("port_name は空にできません")
        mode_value = exact_string_choice(
            mode,
            name="mode",
            choices=("7bit", "14bit"),
        )

        self.port_name = port
        self.mode = mode_value
        self._snapshot_path = _require_snapshot_path(snapshot_path)

        self._msb_by_cc: dict[int, int] = {}
        self.cc: dict[int, float] = {}
        self.cc_change_seq = 0
        self.last_cc_change: tuple[int, int] | None = None

        self.inport: MidiInputPort | None = (
            _validated_input_port(inport)
            if inport is not None
            else self.validate_and_open_port(self.port_name)
        )
        self._snapshot_load_result = CcSnapshotLoadResult(
            values=(),
            status="missing",
            source=self._snapshot_path,
        )
        self._load_snapshot()

    @property
    def snapshot_path(self) -> Path:
        """永続化ファイルのパスを返す。"""

        return self._snapshot_path

    @property
    def snapshot_load_result(self) -> CcSnapshotLoadResult:
        """直近の永続 snapshot load 結果を返す。"""

        return self._snapshot_load_result

    def _load_snapshot(self) -> CcSnapshotLoadResult:
        """永続化ファイルを読み、現行 schema の値だけを反映する。"""

        result = load_cc_snapshot(self._snapshot_path)
        self.cc = result.as_dict()
        self._snapshot_load_result = result
        return result

    def save(self) -> None:
        """現在の CC スナップショットを永続化ファイルへ保存する。"""

        if not self._snapshot_load_result.writable:
            raise CcSnapshotWriteBlockedError(
                "reject した MIDI CC snapshot 原本は自動保存で上書きできません: "
                f"status={self._snapshot_load_result.status}, "
                f"path={self._snapshot_path}"
            )
        save_cc_snapshot(self.cc, self._snapshot_path)
        self._snapshot_load_result = CcSnapshotLoadResult(
            values=_normalized_snapshot_values(self.cc),
            status="loaded",
            source=self._snapshot_path,
        )

    def discard_persisted_snapshot(self) -> None:
        """保存済み原本だけを空の現行 schema へ置き換える。"""

        save_cc_snapshot({}, self._snapshot_path)
        self._snapshot_load_result = CcSnapshotLoadResult(
            values=(),
            status="loaded",
            source=self._snapshot_path,
        )

    def snapshot(self) -> dict[int, float]:
        """現在の CC スナップショット（コピー）を返す。"""

        return dict(self.cc)

    def iter_pending(self) -> Iterable[object]:
        """入力ポートの pending メッセージを返す（mido の API に準拠）。"""

        if self.inport is None:
            return iter(())
        return self.inport.iter_pending()

    def poll_pending(self) -> int:
        """pending メッセージを取り出して処理し、CC 更新回数を返す。"""

        try:
            pending = iter(self.iter_pending())
        except Exception as exc:
            raise MidiConnectionError("MIDI input port から pending を取得できません") from exc

        updated = 0
        while True:
            try:
                msg = next(pending)
            except StopIteration:
                break
            except Exception as exc:
                raise MidiConnectionError("MIDI input iterator の読み取りに失敗しました") from exc
            if self.update(msg):
                updated += 1
        return updated

    def update(self, msg: object) -> bool:
        """MIDI メッセージを 1 つ処理し、CC が更新されたら True を返す。"""

        message_type = exact_string(getattr(msg, "type"), name="message.type")
        if message_type != "control_change":
            return False
        control = _cc_number(getattr(msg, "control"), name="message.control")
        value = _cc_number(getattr(msg, "value"), name="message.value")
        return self.update_cc(control=control, value=value)

    def update_cc(self, *, control: int, value: int) -> bool:
        """CC メッセージを処理し、CC が更新されたら True を返す。"""

        control_i = _cc_number(control, name="control")
        value_i = _cc_number(value, name="value")
        if self.mode == "7bit":
            self.cc[control_i] = value_i / self.MAX_7BIT_VAL
            self.cc_change_seq += 1
            self.last_cc_change = (self.cc_change_seq, control_i)
            return True

        if control_i < self.MSB_THRESHOLD:
            self._msb_by_cc[control_i] = value_i
            return False

        msb_cc = control_i - self.MSB_THRESHOLD
        msb = self._msb_by_cc.get(msb_cc)
        if msb is None:
            return False

        value_14bit = (msb << 7) | value_i
        self.cc[msb_cc] = value_14bit / self.MAX_14BIT_VAL
        self.cc_change_seq += 1
        self.last_cc_change = (self.cc_change_seq, msb_cc)
        return True

    def close(self) -> None:
        """入力ポートを close する。"""

        inport = self.inport
        self.inport = None
        if inport is None:
            return
        inport.close()

    @staticmethod
    def validate_and_open_port(port_name: str) -> MidiInputPort:
        """ポート名を検証して入力ポートを開く。"""

        import mido  # type: ignore

        if port_name in mido.get_input_names():  # type: ignore
            inport = mido.open_input(port_name)  # type: ignore
            return _validated_input_port(inport)
        MidiController.handle_invalid_port_name(port_name)

    @staticmethod
    def handle_invalid_port_name(port_name: str) -> NoReturn:
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
        input_names = mido.get_input_names()  # type: ignore
        output_names = mido.get_output_names()  # type: ignore
        input_names_display = [_restore_macos_mojibake(name) for name in input_names]
        output_names_display = [_restore_macos_mojibake(name) for name in output_names]
        logger.info("Available ports:")
        logger.info("  input: %s", input_names_display)
        logger.info("  output: %s", output_names_display)


def shutdown_midi_controller(
    controller: MidiController,
    *,
    on_snapshot_save_skipped: Callable[[MidiController], None],
    report_secondary: Callable[[str], None] | None = None,
) -> None:
    """snapshot save と port close を独立に実行し、最初の失敗を保持する。"""

    errors = CleanupErrors(report_secondary=report_secondary)

    def save_snapshot() -> None:
        try:
            controller.save()
        except CcSnapshotWriteBlockedError:
            on_snapshot_save_skipped(controller)

    errors.attempt(save_snapshot, "save MIDI CC snapshot")
    errors.attempt(controller.close, "close MIDI controller")
    errors.raise_if_any()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    MidiController.show_available_ports()
