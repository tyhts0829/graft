"""`grafix.api.__init__.pyi` が最新生成結果と一致することのテスト。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_api_stub_sync(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    generated_path = tmp_path / "grafix-api.pyi"
    packaged_config_path = repo_root / "src/grafix/resource/default_config.yaml"
    env = dict(os.environ)
    existing_pythonpath = env.get("PYTHONPATH")
    source_paths = [str(repo_root / "src"), str(repo_root)]
    if existing_pythonpath:
        source_paths.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(source_paths)
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    # CLI と同じ fresh process で生成し、先行 test が global registry へ
    # 登録した局所 operation を stub 契約へ漏らさない。
    subprocess.run(
        (
            sys.executable,
            "-m",
            "grafix",
            "stub",
            "--no-default-import",
            "--config",
            str(packaged_config_path),
            "--output",
            str(generated_path),
        ),
        cwd=repo_root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    expected = generated_path.read_text(encoding="utf-8")
    stub_path = repo_root / "src" / "grafix" / "api" / "__init__.pyi"
    actual = stub_path.read_text(encoding="utf-8")
    assert actual == expected
