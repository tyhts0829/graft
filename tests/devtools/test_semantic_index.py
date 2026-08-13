"""
Purpose:
    Semantic header parserとrepository manifest CLIの抽出契約を固定する。
Use when:
    header形式、走査対象、manifest schema、またはCLI出力を変更する場合。
Constraints:
    - fixture sourceをimportせず、AST抽出と決定的path順だけを検査する。
    - 実repositoryのmanifestにsource本文や追加fieldを許可しない。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL_PATH = ROOT / "tools" / "semantic_index.py"


def _load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("grafix_semantic_index", TOOL_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("semantic index toolをloadできません")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _semantic_source(*, purpose: str = "候補を識別する。") -> str:
    return f'''"""
Purpose:
    {purpose}
Use when:
    次の調査を行う場合:
    次に読むsourceを選ぶ。
Constraints:
    - sourceを実行しない。
    - 次の順序を維持する:
      維持する。
See:
    `architecture.md`。
"""

VALUE = 1
'''


def test_parse_semantic_header_extracts_only_routing_fields(tmp_path: Path) -> None:
    tool = _load_tool()

    header = tool.parse_semantic_header(
        _semantic_source(purpose="候補を\n    短い説明で識別する。"),
        path=tmp_path / "module.py",
    )

    assert header is not None
    assert header.manifest_item(path="module.py") == {
        "path": "module.py",
        "purpose": "候補を 短い説明で識別する。",
        "use_when": "次の調査を行う場合: 次に読むsourceを選ぶ。",
        "constraints": ["sourceを実行しない。", "次の順序を維持する: 維持する。"],
    }


@pytest.mark.parametrize(
    "source",
    [
        '"""通常のmodule説明。"""\n\nVALUE = 1\n',
        '"""通常のmodule説明。\n\nSee:\n    詳細資料。\n"""\n',
    ],
)
def test_parse_semantic_header_skips_ordinary_docstring(
    tmp_path: Path,
    source: str,
) -> None:
    tool = _load_tool()

    assert (
        tool.parse_semantic_header(
            source,
            path=tmp_path / "ordinary.py",
        )
        is None
    )


@pytest.mark.parametrize(
    "source",
    [
        '"""\nPurpose:\n    説明。\nUse when:\n    用途。\n"""\n',
        '"""\n概要。\nPurpose:\n    説明。\nUse when:\n    用途。\nConstraints:\n    制約。\n"""\n',
        '"""\nPurpose:\n    説明。\nUse when:\n    用途。\nConstraints:\n    制約。\nNotes:\n    追加情報。\n"""\n',
        '"""\nUse when:\n    用途。\nConstraints:\n    制約。\n"""\n',
    ],
)
def test_parse_semantic_header_rejects_malformed_sections(
    tmp_path: Path,
    source: str,
) -> None:
    tool = _load_tool()

    with pytest.raises(tool.SemanticIndexError):
        tool.parse_semantic_header(source, path=tmp_path / "malformed.py")


def _semantic_source_with_line_count(line_count: int) -> str:
    continuation_count = line_count - 8
    lines = [
        '"""',
        "Purpose:",
        "    説明。",
        "Use when:",
        "    用途。",
        "Constraints:",
        "    制約。",
        *("    補足。" for _ in range(continuation_count)),
        '"""',
    ]
    return "\n".join(lines) + "\n"


def test_parse_semantic_header_enforces_five_to_fifteen_source_lines(
    tmp_path: Path,
) -> None:
    tool = _load_tool()

    assert tool.parse_semantic_header(
        _semantic_source_with_line_count(15),
        path=tmp_path / "fifteen.py",
    )
    with pytest.raises(tool.SemanticIndexError, match="16行"):
        tool.parse_semantic_header(
            _semantic_source_with_line_count(16),
            path=tmp_path / "sixteen.py",
        )
    escaped_one_line = (
        r'"""Purpose:\n    説明。\nUse when:\n    用途。\nConstraints:\n    制約。"""' "\n"
    )
    with pytest.raises(tool.SemanticIndexError, match="1行"):
        tool.parse_semantic_header(escaped_one_line, path=tmp_path / "one.py")


def test_build_manifest_is_path_sorted_and_skips_nonsemantic_files(
    tmp_path: Path,
) -> None:
    tool = _load_tool()
    (tmp_path / "b.py").write_text(_semantic_source(purpose="B。"), encoding="utf-8")
    (tmp_path / "a.py").write_text(_semantic_source(purpose="A。"), encoding="utf-8")
    (tmp_path / "ordinary.py").write_text('"""通常説明。"""\n', encoding="utf-8")

    manifest = tool.build_manifest((tmp_path,), repository_root=tmp_path)

    assert [item["path"] for item in manifest] == ["a.py", "b.py"]
    assert all(set(item) == {"path", "purpose", "use_when", "constraints"} for item in manifest)


def test_build_manifest_sorts_by_emitted_path_for_mixed_roots(tmp_path: Path) -> None:
    tool = _load_tool()
    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    inside = repository_root / "z.py"
    outside = tmp_path / "zz.py"
    inside.write_text(_semantic_source(), encoding="utf-8")
    outside.write_text(_semantic_source(), encoding="utf-8")

    manifest = tool.build_manifest((inside, outside), repository_root=repository_root)
    paths = [item["path"] for item in manifest]

    assert paths == sorted(paths)


def test_build_manifest_excludes_generated_output_vendor_and_cache_trees(
    tmp_path: Path,
) -> None:
    tool = _load_tool()
    excluded_directories = (
        "__pycache__",
        "dist",
        "generated",
        "output",
        "package.egg-info",
        "third_party",
        "vendor",
    )
    for directory in excluded_directories:
        target = tmp_path / directory
        target.mkdir()
        (target / "excluded.py").write_text(_semantic_source(), encoding="utf-8")
    agent_loop = tmp_path / "sketch" / "agent_loop"
    agent_loop.mkdir(parents=True)
    (agent_loop / "excluded.py").write_text(_semantic_source(), encoding="utf-8")
    (tmp_path / "included.py").write_text(_semantic_source(), encoding="utf-8")

    manifest = tool.build_manifest((tmp_path,), repository_root=tmp_path)

    assert [item["path"] for item in manifest] == ["included.py"]


def test_build_manifest_reports_syntax_error_with_path(tmp_path: Path) -> None:
    tool = _load_tool()
    broken = tmp_path / "broken.py"
    broken.write_text("def broken(:\n", encoding="utf-8")

    with pytest.raises(tool.SemanticIndexError, match="broken.py"):
        tool.build_manifest((tmp_path,), repository_root=tmp_path)


def test_cli_writes_json_manifest(tmp_path: Path) -> None:
    source = tmp_path / "source.py"
    output = tmp_path / "manifest.json"
    source.write_text(_semantic_source(), encoding="utf-8")

    completed = subprocess.run(
        (sys.executable, str(TOOL_PATH), str(source), "--output", str(output)),
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert len(manifest) == 1
    assert manifest[0]["path"] == source.as_posix()


def test_cli_prints_json_manifest_to_stdout(tmp_path: Path) -> None:
    source = tmp_path / "source.py"
    source.write_text(_semantic_source(), encoding="utf-8")

    completed = subprocess.run(
        (sys.executable, str(TOOL_PATH), str(source)),
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)[0]["path"] == source.as_posix()


def test_cli_reports_output_write_error_without_traceback(tmp_path: Path) -> None:
    source = tmp_path / "source.py"
    source.write_text(_semantic_source(), encoding="utf-8")

    completed = subprocess.run(
        (
            sys.executable,
            str(TOOL_PATH),
            str(source),
            "--output",
            str(tmp_path / "missing" / "manifest.json"),
        ),
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr.startswith("error: ")
    assert "Traceback" not in completed.stderr


def test_repository_manifest_is_nonempty_and_field_limited() -> None:
    tool = _load_tool()

    manifest = tool.build_manifest((ROOT / "tools",), repository_root=ROOT)

    assert manifest
    assert all(not Path(item["path"]).is_absolute() for item in manifest)
    assert all(set(item) == {"path", "purpose", "use_when", "constraints"} for item in manifest)
