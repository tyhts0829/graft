# どこで: `rel_cc_check.py`。
# 何を: ロータリーエンコーダー等の「相対 CC」方式を推定する CLI。
# なぜ: デバイスごとに value→Δ の復号方式が異なり、実機ログから判定したい。

from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass


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


def _decode_signed_64(value: int) -> int:
    """signed_64 方式で value を Δ（step）へ復号して返す。"""

    if value == 64:
        return 0
    if 1 <= value <= 63:
        return int(value)
    if 65 <= value <= 127:
        return -int(value - 64)
    return 0


def _decode_twos_complement(value: int) -> int:
    """twos_complement（7bit 符号付き）方式で value を Δ（step）へ復号して返す。"""

    if 0 <= value <= 63:
        return int(value)
    if 64 <= value <= 127:
        return int(value - 128)
    return 0


def _decode_binary_offset(value: int) -> int:
    """binary_offset（value-64）方式で value を Δ（step）へ復号して返す。"""

    if 0 <= value <= 127:
        return int(value - 64)
    return 0


def _decode_inc_dec(value: int) -> int:
    """inc_dec 方式で value を Δ（step）へ復号して返す。"""

    if value == 1:
        return 1
    if value == 127:
        return -1
    return 0


@dataclass(frozen=True)
class Scheme:
    """相対 CC の復号方式。"""

    name: str
    decode: Callable[[int], int]
    description: str


SCHEMES: tuple[Scheme, ...] = (
    Scheme(
        name="signed_64",
        decode=_decode_signed_64,
        description="64=no-op, 1..63=+1..+63, 65..127=-1..-63",
    ),
    Scheme(
        name="twos_complement",
        decode=_decode_twos_complement,
        description="0..63=+0..+63, 64..127=-64..-1",
    ),
    Scheme(
        name="binary_offset",
        decode=_decode_binary_offset,
        description="delta=value-64（64=no-op）",
    ),
    Scheme(
        name="inc_dec",
        decode=_decode_inc_dec,
        description="1=+1, 127=-1, その他=0",
    ),
)


@dataclass(frozen=True)
class Score:
    """方向付きログに対する方式のスコア。"""

    scheme: Scheme
    total: int
    matches: int
    mismatches: int
    zeros: int
    mean_abs: float

    @property
    def nonzero(self) -> int:
        return int(self.matches + self.mismatches)

    @property
    def accuracy(self) -> float:
        if self.nonzero <= 0:
            return 0.0
        return float(self.matches) / float(self.nonzero)

    @property
    def coverage(self) -> float:
        if self.total <= 0:
            return 0.0
        return float(self.nonzero) / float(self.total)


def _format_port_list(raw_names: list[str]) -> str:
    if not raw_names:
        return "  (入力ポートが見つかりませんでした)\n"
    lines = []
    for i, raw in enumerate(raw_names):
        display = _restore_macos_mojibake(raw)
        suffix = "" if display == raw else f"  (表示: {display})"
        lines.append(f"  [{i}] {raw}{suffix}")
    return "\n".join(lines) + "\n"


def _require_mido():
    try:
        import mido  # type: ignore

        return mido
    except Exception as e:  # pragma: no cover
        raise SystemExit(
            "mido の import に失敗しました。`pip install -e .`（または `pip install mido python-rtmidi`）"
            "を行ってから再実行してください。"
        ) from e


def _choose_port(mido, *, port_name: str | None, port_index: int | None) -> str:
    raw_names = list(mido.get_input_names())
    if port_name is None and port_index is None:
        if len(raw_names) == 1:
            return str(raw_names[0])
        print("入力ポートを指定してください。`--list-ports` で一覧を表示できます。", file=sys.stderr)
        print("利用可能な入力ポート:", file=sys.stderr)
        print(_format_port_list(raw_names), file=sys.stderr, end="")
        raise SystemExit(2)

    if port_index is not None:
        if port_index < 0 or port_index >= len(raw_names):
            print("port_index が範囲外です。", file=sys.stderr)
            print(_format_port_list(raw_names), file=sys.stderr, end="")
            raise SystemExit(2)
        return str(raw_names[port_index])

    assert port_name is not None
    if port_name in raw_names:
        return str(port_name)

    matched = [n for n in raw_names if str(port_name) in str(n)]
    if len(matched) == 1:
        return str(matched[0])
    if matched:
        print("port_name が複数候補に一致しました。より具体的に指定してください。", file=sys.stderr)
        print(_format_port_list(matched), file=sys.stderr, end="")
        raise SystemExit(2)

    print("port_name が見つかりませんでした。", file=sys.stderr)
    print(_format_port_list(raw_names), file=sys.stderr, end="")
    raise SystemExit(2)


def _drain_pending(inport) -> None:
    for _ in inport.iter_pending():
        pass


def _scan_active_cc(
    inport,
    *,
    seconds: float,
    show_raw: bool,
) -> Counter[tuple[int, int]]:
    deadline = time.monotonic() + float(seconds)
    counts: Counter[tuple[int, int]] = Counter()
    while time.monotonic() < deadline:
        for msg in inport.iter_pending():
            if show_raw:
                print(f"[RAW] {msg}")
            if getattr(msg, "type", None) != "control_change":
                continue
            try:
                ch = int(getattr(msg, "channel"))
                cc = int(getattr(msg, "control"))
            except Exception:
                continue
            counts[(ch, cc)] += 1
        time.sleep(0.001)
    return counts


def _pick_target_from_counts(
    counts: Counter[tuple[int, int]],
    *,
    interactive: bool,
) -> tuple[int, int] | None:
    if not counts:
        return None

    top = counts.most_common(12)
    print("検出された CC（多い順）:")
    for i, ((ch, cc), n) in enumerate(top):
        print(f"  [{i}] ch={ch+1:02d} cc={cc:03d} x{n}")

    if not interactive:
        return top[0][0]

    choice = input("解析対象を選択 (Enter=0): ").strip()
    if not choice:
        return top[0][0]
    try:
        i = int(choice)
    except ValueError:
        return top[0][0]
    if 0 <= i < len(top):
        return top[i][0]
    return top[0][0]


def _capture_values(
    inport,
    *,
    seconds: float,
    target_channel: int | None,
    target_cc: int,
    show_raw: bool,
    per_message: bool,
) -> list[int]:
    deadline = time.monotonic() + float(seconds)
    values: list[int] = []
    while time.monotonic() < deadline:
        for msg in inport.iter_pending():
            if show_raw:
                print(f"[RAW] {msg}")
            if getattr(msg, "type", None) != "control_change":
                continue
            try:
                ch = int(getattr(msg, "channel"))
                cc = int(getattr(msg, "control"))
                val = int(getattr(msg, "value"))
            except Exception:
                continue
            if cc != target_cc:
                continue
            if target_channel is not None and ch != target_channel:
                continue
            if per_message:
                deltas = " ".join(f"{s.name}={s.decode(val):+d}" for s in SCHEMES)
                print(f"ch={ch+1:02d} cc={cc:03d} value={val:03d} | {deltas}")
            values.append(val)
        time.sleep(0.001)
    return values


def _analyze(
    *,
    positive_values: list[int],
    negative_values: list[int],
) -> list[Score]:
    total = int(len(positive_values) + len(negative_values))

    out: list[Score] = []
    for scheme in SCHEMES:
        pos = [int(scheme.decode(v)) for v in positive_values]
        neg = [int(scheme.decode(v)) for v in negative_values]

        matches = sum(d > 0 for d in pos) + sum(d < 0 for d in neg)
        mismatches = sum(d < 0 for d in pos) + sum(d > 0 for d in neg)
        zeros = sum(d == 0 for d in pos) + sum(d == 0 for d in neg)

        nonzero_deltas = [abs(d) for d in (pos + neg) if d != 0]
        mean_abs = float(statistics.mean(nonzero_deltas)) if nonzero_deltas else float("inf")

        out.append(
            Score(
                scheme=scheme,
                total=total,
                matches=int(matches),
                mismatches=int(mismatches),
                zeros=int(zeros),
                mean_abs=mean_abs,
            )
        )

    out.sort(key=lambda s: (-s.accuracy, -s.coverage, s.mean_abs, -s.nonzero, s.scheme.name))
    return out


def _format_counter(counter: Counter[int], *, limit: int = 16) -> str:
    if not counter:
        return "(なし)"
    items = counter.most_common(limit)
    body = ", ".join(f"{k}:{v}" for k, v in items)
    if len(counter) > limit:
        body += ", ..."
    return body


def _print_analysis(
    *,
    positive_values: list[int],
    negative_values: list[int],
    scores: list[Score],
) -> None:
    pos_counts = Counter(positive_values)
    neg_counts = Counter(negative_values)
    print("観測 value（正方向）:", _format_counter(pos_counts))
    print("観測 value（逆方向）:", _format_counter(neg_counts))
    print("")

    print("方式ごとのスコア（符号一致率 / 非ゼロ率 / 平均|Δ|）:")
    for s in scores:
        mean_abs = "inf" if s.mean_abs == float("inf") else f"{s.mean_abs:.2f}"
        print(
            f"- {s.scheme.name:14s}  acc={s.accuracy:.2f}  cov={s.coverage:.2f}  "
            f"mean|Δ|={mean_abs:>5s}  (match={s.matches} mis={s.mismatches} zero={s.zeros})"
        )

    if not scores:
        return

    best = scores[0]
    print("")
    print("推定:", best.scheme.name)
    print("説明:", best.scheme.description)

    if len(scores) >= 2:
        second = scores[1]
        tied = (
            abs(best.accuracy - second.accuracy) < 1e-9
            and abs(best.coverage - second.coverage) < 1e-9
            and abs(best.mean_abs - second.mean_abs) < 1e-9
        )
        if tied:
            print("")
            print("注意: 上位候補が同点です。今回のログだけでは方式を一意に決めきれません。")
            print("      速く回して 2/126/66/62 などが出るか確認して再実行してください。")


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ロータリーエンコーダー等が送る相対 CC 方式（signed_64 等）を推定します。",
    )
    parser.add_argument("--list-ports", action="store_true", help="利用可能な入力ポートを表示して終了。")
    parser.add_argument("--port", type=str, default=None, help="入力ポート名（部分一致1件なら自動選択）。")
    parser.add_argument("--port-index", type=int, default=None, help="入力ポートの index（--list-ports 参照）。")

    parser.add_argument("--mode", choices=("calibrate", "monitor"), default="calibrate")
    parser.add_argument("--scan-seconds", type=float, default=5.0, help="CC 自動検出の待ち時間（秒）。")
    parser.add_argument("--capture-seconds", type=float, default=4.0, help="各方向の収録時間（秒）。")
    parser.add_argument("--cc", type=int, default=None, help="解析対象の CC 番号（省略で自動検出）。")
    parser.add_argument(
        "--channel",
        type=int,
        default=None,
        help="解析対象の MIDI channel（1..16）。省略で全 channel を対象。",
    )

    parser.add_argument("--raw", action="store_true", help="mido の生ログも表示。")
    parser.add_argument(
        "--per-message",
        action="store_true",
        help="1 メッセージごとに各方式の Δ を表示（verbose）。",
    )
    parser.add_argument(
        "--no-interactive",
        action="store_true",
        help="対話入力を行わない（自動選択のみ）。",
    )
    return parser


def _run_monitor(mido, args: argparse.Namespace) -> None:
    port_name = _choose_port(mido, port_name=args.port, port_index=args.port_index)
    print("Listening:", _restore_macos_mojibake(port_name))
    inport = mido.open_input(port_name)

    target_cc: int | None = int(args.cc) if args.cc is not None else None
    target_channel: int | None
    if args.channel is None:
        target_channel = None
    else:
        if not (1 <= int(args.channel) <= 16):
            raise SystemExit("--channel は 1..16 を指定してください。")
        target_channel = int(args.channel) - 1

    try:
        while True:
            for msg in inport.iter_pending():
                if args.raw:
                    print(f"[RAW] {msg}")
                if getattr(msg, "type", None) != "control_change":
                    continue
                try:
                    ch = int(getattr(msg, "channel"))
                    cc = int(getattr(msg, "control"))
                    val = int(getattr(msg, "value"))
                except Exception:
                    continue

                if target_cc is not None and cc != target_cc:
                    continue
                if target_channel is not None and ch != target_channel:
                    continue

                deltas = " ".join(f"{s.name}={s.decode(val):+d}" for s in SCHEMES)
                print(f"ch={ch+1:02d} cc={cc:03d} value={val:03d} | {deltas}")
            time.sleep(0.001)
    except KeyboardInterrupt:
        pass
    finally:
        inport.close()


def _run_calibrate(mido, args: argparse.Namespace) -> None:
    port_name = _choose_port(mido, port_name=args.port, port_index=args.port_index)
    print("Listening:", _restore_macos_mojibake(port_name))
    inport = mido.open_input(port_name)

    interactive = not bool(args.no_interactive)

    target_channel: int | None
    if args.channel is None:
        target_channel = None
    else:
        if not (1 <= int(args.channel) <= 16):
            raise SystemExit("--channel は 1..16 を指定してください。")
        target_channel = int(args.channel) - 1

    try:
        target_cc: int
        if args.cc is not None:
            target_cc = int(args.cc)
            print(f"解析対象: ch={'*' if target_channel is None else target_channel + 1:>2} cc={target_cc:03d}")
        else:
            print("")
            print("CC を自動検出します。解析したいノブ/エンコーダーを回してください…")
            _drain_pending(inport)
            counts = _scan_active_cc(
                inport,
                seconds=float(args.scan_seconds),
                show_raw=bool(args.raw),
            )
            picked = _pick_target_from_counts(counts, interactive=interactive)
            if picked is None:
                raise SystemExit("CC が検出できませんでした。--cc を明示指定して再実行してください。")

            picked_ch, picked_cc = picked
            if target_channel is None:
                target_channel = picked_ch
            target_cc = picked_cc
            print(f"解析対象: ch={target_channel + 1:02d} cc={target_cc:03d}")

        print("")
        print("以降、2 回収録します（正方向 → 逆方向）。")
        print("※ 方向の定義は任意です。ここでの「正方向」はあなたが最初に回す方向です。")
        print("")

        if interactive:
            input("Enter を押すと『正方向』の収録を開始します: ")
        print(f"『正方向』に {args.capture_seconds:.1f}s 回し続けてください…")
        _drain_pending(inport)
        pos_values = _capture_values(
            inport,
            seconds=float(args.capture_seconds),
            target_channel=target_channel,
            target_cc=target_cc,
            show_raw=bool(args.raw),
            per_message=bool(args.per_message),
        )

        if interactive:
            input("Enter を押すと『逆方向』の収録を開始します: ")
        print(f"『逆方向』に {args.capture_seconds:.1f}s 回し続けてください…")
        _drain_pending(inport)
        neg_values = _capture_values(
            inport,
            seconds=float(args.capture_seconds),
            target_channel=target_channel,
            target_cc=target_cc,
            show_raw=bool(args.raw),
            per_message=bool(args.per_message),
        )

        print("")
        if not pos_values and not neg_values:
            raise SystemExit("CC メッセージが 1 件も収録できませんでした。")
        if not pos_values or not neg_values:
            print("注意: 片方向しか収録できていません。推定精度が下がります。")

        scores = _analyze(positive_values=pos_values, negative_values=neg_values)
        _print_analysis(positive_values=pos_values, negative_values=neg_values, scores=scores)
    finally:
        inport.close()


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)
    mido = _require_mido()

    if args.list_ports:
        raw_names = list(mido.get_input_names())
        print("利用可能な入力ポート:")
        print(_format_port_list(raw_names), end="")
        return 0

    if args.mode == "monitor":
        _run_monitor(mido, args)
        return 0

    _run_calibrate(mido, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
