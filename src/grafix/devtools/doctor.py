"""
Purpose:
    Grafix の GL・external command・MIDI・font・output path を小さな probe で検査し、human/JSON 共通の structured report にする。
Use when:
    ``grafix doctor`` の診断項目、severity、環境 probe の isolation/cleanup、exit status を変更・調査する場合。
Constraints:
    - optional な resvg/ffmpeg/MIDI の不在は warning とし、core 実行不能を表す error と混同しない。
    - abort し得る MIDI backend query は短命 subprocess へ隔離する。
    - output write probe は一時 file だけを作り必ず削除し、対象 output directory 自体を作らない。
Side effects:
    config/font/filesystem を読み、MIDI subprocess と一時 write probe を実行する。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from grafix.core.evaluation_config import EvaluationConfig, bind_evaluation_config
from grafix.core.font_resolver import default_font_path
from grafix.core.runtime_config import bind_runtime_config
from grafix.runtime_config_loader import load_runtime_config
from grafix.core.value_validation import exact_string, exact_string_choice

DoctorStatus = Literal["ok", "warning", "error"]


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    """1 項目の環境診断結果。"""

    name: str
    status: DoctorStatus
    summary: str
    details: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "name",
            exact_string(self.name, name="name"),
        )
        object.__setattr__(
            self,
            "status",
            cast(
                DoctorStatus,
                exact_string_choice(
                    self.status,
                    name="status",
                    choices=("ok", "warning", "error"),
                ),
            ),
        )
        object.__setattr__(
            self,
            "summary",
            exact_string(self.summary, name="summary"),
        )
        if type(self.details) is not tuple:
            raise TypeError("details は str の tuple である必要があります")
        object.__setattr__(
            self,
            "details",
            tuple(
                exact_string(item, name=f"details[{index}]")
                for index, item in enumerate(self.details)
            ),
        )

    def to_dict(self) -> dict[str, object]:
        """JSON 化可能な辞書へ変換する。"""

        return {
            "name": self.name,
            "status": self.status,
            "summary": self.summary,
            "details": list(self.details),
        }


@dataclass(frozen=True, slots=True)
class DoctorReport:
    """Grafix doctor の structured result。"""

    checks: tuple[DoctorCheck, ...]

    def __post_init__(self) -> None:
        if type(self.checks) is not tuple or any(
            not isinstance(check, DoctorCheck)
            for check in self.checks
        ):
            raise TypeError("checks は DoctorCheck の tuple である必要があります")

    @property
    def healthy(self) -> bool:
        """error が無ければ True を返す。"""

        return all(check.status != "error" for check in self.checks)

    def to_dict(self) -> dict[str, object]:
        """JSON 化可能な辞書へ変換する。"""

        return {
            "healthy": self.healthy,
            "checks": [check.to_dict() for check in self.checks],
        }


def _check_gl() -> DoctorCheck:
    modules = ("moderngl", "OpenGL")
    missing = tuple(name for name in modules if importlib.util.find_spec(name) is None)
    if missing:
        return DoctorCheck(
            name="gl",
            status="error",
            summary="OpenGL Python binding が不足しています",
            details=missing,
        )
    return DoctorCheck(
        name="gl",
        status="ok",
        summary="moderngl と PyOpenGL を検出しました",
    )


def _check_command(name: str) -> DoctorCheck:
    path = shutil.which(name)
    if path is None:
        # export/recording を使わない利用者もいるため、command 不在だけでは doctor を失敗にしない。
        return DoctorCheck(
            name=name,
            status="warning",
            summary=f"{name} command が見つかりません（該当機能のみ利用不可）",
        )
    return DoctorCheck(name=name, status="ok", summary=f"{name} を検出しました", details=(path,))


def _check_midi() -> DoctorCheck:
    if importlib.util.find_spec("mido") is None:
        return DoctorCheck(
            name="midi",
            status="warning",
            summary="mido を検出できません",
        )

    try:
        # RtMidi backend は OS 初期化失敗時に Python 例外を越えて process abort する場合がある。
        # doctor 本体を守るため、port query だけを短命な subprocess へ隔離する。
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                "import json, mido; print(json.dumps(list(mido.get_input_names())))",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return DoctorCheck(
            name="midi",
            status="warning",
            summary="MIDI input 一覧を取得できません",
            details=(f"{type(exc).__name__}: {exc}",),
        )

    if completed.returncode != 0:
        details = completed.stderr.strip() or completed.stdout.strip()
        return DoctorCheck(
            name="midi",
            status="warning",
            summary="MIDI backend の初期化に失敗しました",
            details=((details[-500:] if details else f"exit code {completed.returncode}"),),
        )
    try:
        raw_names = json.loads(completed.stdout)
        names = tuple(str(name) for name in raw_names)
    except (TypeError, ValueError) as exc:
        return DoctorCheck(
            name="midi",
            status="warning",
            summary="MIDI backend の応答を解釈できません",
            details=(f"{type(exc).__name__}: {exc}",),
        )

    return DoctorCheck(
        name="midi",
        status="ok",
        summary=f"MIDI input を {len(names)} 件検出しました",
        details=names,
    )


def _check_font(path: Path | None = None) -> DoctorCheck:
    try:
        resolved = default_font_path() if path is None else Path(path)
    except Exception as exc:
        return DoctorCheck(
            name="font",
            status="error",
            summary="既定 font を解決できません",
            details=(f"{type(exc).__name__}: {exc}",),
        )

    if not resolved.is_file():
        return DoctorCheck(
            name="font",
            status="error",
            summary="既定 font file が存在しません",
            details=(str(resolved),),
        )
    try:
        with resolved.open("rb") as stream:
            stream.read(1)
    except OSError as exc:
        return DoctorCheck(
            name="font",
            status="error",
            summary="既定 font file を読み取れません",
            details=(str(resolved), f"{type(exc).__name__}: {exc}"),
        )
    return DoctorCheck(
        name="font",
        status="ok",
        summary="既定 font を読み取れます",
        details=(str(resolved),),
    )


def _nearest_existing_directory(path: Path) -> Path | None:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate if candidate.is_dir() else None


def _check_output_write(path: Path) -> DoctorCheck:
    output_dir = Path(path).expanduser().resolve(strict=False)
    if output_dir.exists() and not output_dir.is_dir():
        return DoctorCheck(
            name="output_write",
            status="error",
            summary="output path が directory ではありません",
            details=(str(output_dir),),
        )

    probe_parent = output_dir if output_dir.is_dir() else _nearest_existing_directory(output_dir)
    if probe_parent is None:
        return DoctorCheck(
            name="output_write",
            status="error",
            summary="output path の既存 parent directory がありません",
            details=(str(output_dir),),
        )

    try:
        # probe directory と file は context 終了時に削除し、output 本体は作らない。
        with tempfile.TemporaryDirectory(prefix=".grafix-doctor-", dir=probe_parent) as temp_dir:
            probe = Path(temp_dir) / "write-probe"
            probe.write_bytes(b"grafix")
            if probe.read_bytes() != b"grafix":
                raise OSError("write probe の内容が一致しません")
    except OSError as exc:
        return DoctorCheck(
            name="output_write",
            status="error",
            summary="output directory へ書き込めません",
            details=(str(output_dir), f"{type(exc).__name__}: {exc}"),
        )

    summary = (
        "output directory へ書き込めます"
        if output_dir.is_dir()
        else "output directory の作成先へ書き込めます"
    )
    return DoctorCheck(
        name="output_write",
        status="ok",
        summary=summary,
        details=(str(output_dir),),
    )


def run_doctor(
    *,
    output_dir: str | Path | None = None,
    font_path: str | Path | None = None,
    config_path: str | Path | None = None,
) -> DoctorReport:
    """GL・外部command・MIDI・font・出力先を検査する。

    Parameters
    ----------
    output_dir : str or Path or None, optional
        write probe 対象。None の場合は runtime config の ``output_dir``。
    font_path : str or Path or None, optional
        read probe 対象。None の場合は Grafix の既定 font。

    Returns
    -------
    DoctorReport
        項目ごとの status と説明を持つ structured result。

    Notes
    -----
    ``resvg`` / ``ffmpeg`` が無い場合は warning とし、doctor 全体は正常終了できる。
    output write probe は一時 file だけを作成し、終了時に削除する。
    """

    configured_output: Path | None = None
    config_error: Exception | None = None
    config = None
    if config_path is not None or output_dir is None or font_path is None:
        try:
            config = load_runtime_config(config_path)
        except Exception as exc:
            config_error = exc
    if output_dir is None:
        if config is not None:
            configured_output = Path(config.output_dir)
    else:
        configured_output = Path(output_dir)

    config_context = nullcontext() if config is None else bind_runtime_config(config)
    evaluation_context = (
        nullcontext()
        if config is None
        else bind_evaluation_config(EvaluationConfig(font_dirs=config.font_dirs))
    )
    with config_context, evaluation_context:
        checks = [
            _check_gl(),
            _check_command("resvg"),
            _check_command("ffmpeg"),
            _check_midi(),
            _check_font(None if font_path is None else Path(font_path)),
        ]
    if config_error is not None:
        checks.append(
            DoctorCheck(
                name="runtime_config",
                status="error",
                summary="runtime config を読み込めません",
                details=(f"{type(config_error).__name__}: {config_error}",),
            )
        )
    if configured_output is None:
        assert config_error is not None
        checks.append(
            DoctorCheck(
                name="output_write",
                status="error",
                summary="runtime config から output directory を取得できません",
                details=(f"{type(config_error).__name__}: {config_error}",),
            )
        )
    else:
        checks.append(_check_output_write(configured_output))
    return DoctorReport(checks=tuple(checks))


def _render_human(report: DoctorReport) -> str:
    labels = {"ok": "OK", "warning": "WARN", "error": "ERROR"}
    lines: list[str] = []
    for check in report.checks:
        lines.append(f"[{labels[check.status]}] {check.name}: {check.summary}")
        lines.extend(f"  {detail}" for detail in check.details)
    lines.append("Grafix doctor: healthy" if report.healthy else "Grafix doctor: errors found")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """``grafix doctor`` の CLI を実行する。

    Parameters
    ----------
    argv : list[str] or None, optional
        CLI 引数。None の場合は ``sys.argv`` を使う。

    Returns
    -------
    int
        error が無ければ 0、それ以外は 1。
    """

    parser = argparse.ArgumentParser(prog="python -m grafix doctor")
    parser.add_argument("--config", type=Path, help="明示する config.yaml")
    parser.add_argument("--json", action="store_true", help="structured JSON を表示する")
    args = parser.parse_args(argv)

    report = (
        run_doctor()
        if args.config is None
        else run_doctor(config_path=args.config)
    )

    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))  # noqa: T201
    else:
        print(_render_human(report))  # noqa: T201
    return 0 if report.healthy else 1


__all__ = [
    "DoctorCheck",
    "DoctorReport",
    "DoctorStatus",
    "main",
    "run_doctor",
]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
