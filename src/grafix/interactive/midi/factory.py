# どこで: `src/grafix/interactive/midi/factory.py`。
# 何を: port_name/mode に従って MidiController を生成する（auto 接続 / mido 有無を含む）。
# なぜ: `src/grafix/api/runner.py` を配線に寄せ、MIDI 依存ロジックを interactive 側に閉じ込めるため。

from __future__ import annotations

from .midi_controller import MidiController

_AUTO_MIDI_PORT = "auto"


def create_midi_controller(
    *, port_name: str | None, mode: str, profile_name: str
) -> MidiController | None:
    """port_name/mode に従い MidiController を作る。

    - port_name=None の場合: None（MIDI 無効）
    - port_name="auto" の場合:
      - mido が使えて入力ポートが 1 つ以上あるときだけ接続する
      - mido が無い/入力ポートが無い場合は None
    - 明示ポート指定の場合:
      - mido が無い場合は例外（ユーザーの意図が強いのでエラー）
      - それ以外は MidiController 側の検証に任せる
    """

    if port_name is None:
        return None

    if port_name == _AUTO_MIDI_PORT:
        try:
            import mido  # type: ignore
        except Exception:
            return None
        names = list(mido.get_input_names())  # type: ignore
        if not names:
            return None
        return MidiController(names[0], mode=mode, profile_name=profile_name)

    # 明示指定のときは mido が必要（port 有無の検証にも使う）
    try:
        import mido  # type: ignore  # noqa: F401
    except Exception as exc:
        raise RuntimeError(
            "midi_port_name を指定するには mido が必要です（pip で導入してください）。"
        ) from exc
    return MidiController(port_name, mode=mode, profile_name=profile_name)
