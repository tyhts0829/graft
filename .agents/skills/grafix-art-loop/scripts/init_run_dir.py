#!/usr/bin/env python3
"""
Purpose:
    Grafix Art Loopの一runに必要なflat workspaceと唯一の管理JSONを初期化するCLI。
Use when:
    候補制作を始める前にrun ID、candidate directory、run.jsonの骨格を確定するとき。
Constraints:
    - rootはsketch/agent_loop/runs配下に限定し、既存run directoryを再利用・上書きしない。
    - run IDのcandidate数と--nを一致させ、candidate IDを連番で一意にする。
    - run.json以外のrole別管理JSONや旧来の多段directoryを生成しない。
    - latest pointer更新は明示opt-inとし、dry-runではfilesystemを変更しない。
Side effects:
    run workspaceとrun.jsonを作成し、opt-in時のみlatest pointer fileを更新する。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

RUN_ID_PATTERN = re.compile(r"^run_(\d{8})_(\d{6})_n(\d+)$")
DEFAULT_ROOT = Path("sketch/agent_loop/runs")


@dataclass(frozen=True)
class RunSpec:
    run_id: str
    run_dir: Path
    n: int


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Initialize a flat Grafix Art Loop run directory skeleton."
    )
    parser.add_argument("--n", type=int, default=3, help="Number of candidates (default: 3).")
    parser.add_argument(
        "--run-id",
        help="Explicit run_id (must match run_YYYYMMDD_HHMMSS_n{n}).",
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="Runs root directory.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print planned paths without creating them."
    )
    parser.add_argument(
        "--update-latest",
        action="store_true",
        help="Update .latest_run_id/.last_run_id under the runs root (opt-in).",
    )
    return parser.parse_args()


def validate_positive(name: str, value: int) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be positive (got {value})")


def normalize_root(root: Path) -> Path:
    allowed = DEFAULT_ROOT.resolve()
    resolved = root.resolve()
    try:
        resolved.relative_to(allowed)
    except ValueError as exc:
        raise ValueError(f"--root must be under {DEFAULT_ROOT} (got {root})") from exc
    return resolved


def generate_run_id(n: int, now: datetime) -> str:
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    return f"run_{timestamp}_n{n}"


def validate_run_id(run_id: str, *, n: int) -> None:
    matched = RUN_ID_PATTERN.fullmatch(run_id)
    if matched is None:
        raise ValueError(
            "run_id must match run_YYYYMMDD_HHMMSS_n{n} "
            f"(got {run_id!r}, example: run_20260721_153000_n3)"
        )
    n_in_id = int(matched.group(3))
    if n_in_id != n:
        raise ValueError(f"run_id encodes n={n_in_id}, but --n is {n}")


def build_run_spec(args: argparse.Namespace) -> RunSpec:
    validate_positive("n", args.n)

    root = normalize_root(args.root)
    run_id = args.run_id or generate_run_id(args.n, datetime.now())
    validate_run_id(run_id, n=args.n)
    return RunSpec(run_id=run_id, run_dir=root / run_id, n=args.n)


def _width(count: int) -> int:
    return max(2, len(str(count)))


def candidate_ids(n: int) -> list[str]:
    width = _width(n)
    return [f"v{index:0{width}d}" for index in range(1, n + 1)]


def planned_dirs(spec: RunSpec) -> list[Path]:
    candidates_dir = spec.run_dir / "candidates"
    return [
        spec.run_dir,
        candidates_dir,
        *(candidates_dir / candidate_id for candidate_id in candidate_ids(spec.n)),
        spec.run_dir / "final",
    ]


def initial_run_data(spec: RunSpec) -> dict[str, object]:
    return {
        "run_id": spec.run_id,
        "candidate_count": spec.n,
        "candidates": [
            {"id": candidate_id, "status": "pending", "attempts": 0}
            for candidate_id in candidate_ids(spec.n)
        ],
        "winner": None,
    }


def update_latest_files(root: Path, run_id: str) -> None:
    latest_path = root / ".latest_run_id"
    last_path = root / ".last_run_id"
    previous = latest_path.read_text(encoding="utf-8").strip() if latest_path.exists() else ""
    if previous:
        last_path.write_text(previous + "\n", encoding="utf-8")
    latest_path.write_text(run_id + "\n", encoding="utf-8")


def main() -> int:
    args = _parse_args()
    try:
        spec = build_run_spec(args)
        paths = planned_dirs(spec)
        run_json_path = spec.run_dir / "run.json"

        print(f"run_id={spec.run_id} run_dir={spec.run_dir}")
        for path in [paths[0], run_json_path, *paths[1:]]:
            print(path)

        if args.dry_run:
            return 0

        if spec.run_dir.exists():
            raise ValueError(f"run_dir already exists: {spec.run_dir}")

        for path in paths:
            path.mkdir(parents=True, exist_ok=True)
        run_json_path.write_text(
            json.dumps(initial_run_data(spec), indent=2) + "\n",
            encoding="utf-8",
        )

        if args.update_latest:
            update_latest_files(spec.run_dir.parent, spec.run_id)

        print("status=ok")
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
