"""runtime config の strict validation と effective value 表示 CLI。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from grafix.core.runtime_config import RuntimeConfigReport
from grafix.runtime_config_loader import load_runtime_config_report


def _add_config_path_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", nargs="?", help="検証する config.yaml（省略時は通常探索）")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m grafix config")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate", help="config を strict validation する")
    show_parser = subparsers.add_parser("show", help="effective config と出典を表示する")
    _add_config_path_argument(validate_parser)
    _add_config_path_argument(show_parser)
    return parser.parse_args(argv)


def _format_resolved_path(value: Path | tuple[Path, ...] | None) -> str:
    if value is None:
        return "-"
    if isinstance(value, tuple):
        return repr(tuple(str(path) for path in value))
    return str(value)


def _print_report(report: RuntimeConfigReport) -> None:
    print(f"config_source: {report.active_source}")
    for value in report.values:
        print(f"{value.key}:")
        print(f"  source: {value.source}")
        print(f"  effective_value: {value.effective_value!r}")
        if value.is_path:
            print(f"  resolved_path: {_format_resolved_path(value.resolved_path)}")


def main(argv: list[str] | None = None) -> int:
    """``config validate/show`` を実行し、成否を exit code で返す。"""

    if argv is None:
        argv = sys.argv[1:]
    args = _parse_args(argv)

    try:
        report = load_runtime_config_report(args.path)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"config invalid: {exc}", file=sys.stderr)
        return 2

    if args.command == "validate":
        print(f"config valid: {report.active_source}")
        return 0
    if args.command == "show":
        _print_report(report)
        return 0
    raise AssertionError(f"unknown config command: {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())
