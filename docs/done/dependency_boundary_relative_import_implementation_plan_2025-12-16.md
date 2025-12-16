# どこで: `docs/memo/dependency_boundary_relative_import_implementation_plan_2025-12-16.md`。

# 何を: `tests/architecture/test_dependency_boundaries.py` の境界検査が「相対 import」を見逃す穴を塞ぐための実装計画（チェックリスト）をまとめる。

# なぜ: 将来 `from ..export import ...` 等で境界を迂回できる状態をなくし、依存方向の “柵” をテストとして実態どおり機能させるため。

# 依存境界テスト: 相対 import 対応 実装計画（2025-12-16）

## 前提（現在地）

- 対象: `tests/architecture/test_dependency_boundaries.py`
- 現状の `_import_modules_in_file()` は `ast.ImportFrom` のうち `node.level > 0`（相対 import）を `continue` でスキップしている。
  - その結果、`from ..export import svg` のような “境界またぎ相対 import” を検出できない。
- さらに、`ast.ImportFrom` で `modules.add(node.module)` しかしていないため、次の形も見逃す。
  - 例: `from graft import export`（`node.module == "graft"` なので `"graft.export"` を拾えない）
- `src/graft/` 配下には `from .foo import ...` の相対 import が既に多数ある（相対 import 自体を禁止する方向は現実的ではない）。

## ゴール（成功条件）

- `src/graft/core` で次が書かれたらテストが落ちる:
  - `from ..export import ...` / `from .. import export`
  - `from ..interactive import ...` / `from .. import interactive`
  - `from graft import export` / `from graft import interactive`
- 既存の “同一層内” 相対 import（例: `src/graft/core/**` の `from .key import ...`）では落ちない。
- 失敗時メッセージに「どのファイルが」「どの解決後モジュール名で」違反したかが出る。

## 方針（採用案）

### 相対 import を “解決後の絶対モジュール名” として扱う

- ファイルパスから “現在モジュール名” を決める:
  - 例: `repo_root/src/graft/core/pipeline.py` → `graft.core.pipeline`
  - 例: `repo_root/src/graft/core/__init__.py` → `graft.core`（`.__init__` は落とす）
- `ast.ImportFrom` を次のルールで “検査対象モジュール名の集合” に変換する:
  - `level == 0`（絶対 import）:
    - `base = node.module`（`None` は無視）
  - `level > 0`（相対 import）:
    - “現在パッケージ” を `current_module` から作り、`level` に従って親へ移動して `base` を作る
    - `base = <移動後パッケージ> + ("." + node.module if node.module else "")`
  - 追加で、`from X import Y` の “Y 側” も候補として足す（`Y != "*"` のとき）:
    - `base + "." + name`（`from graft import export` → `graft.export` を拾うため）
- 解決不能（ドットが深すぎる等）の相対 import は「テスト側の不備で黙殺」せず、違反として明示的に落とす（＝穴を残さない）。

## テスト計画（最小）

既存テストファイル内に “ヘルパーのユニットテスト” を足す（依存追加なし、対象限定で高速）。

- [ ] `current_module="graft.core.pipeline"` + `from ..export import svg` → `{"graft.export", "graft.export.svg"}` を含む
- [ ] `current_module="graft.core.pipeline"` + `from .. import interactive` → `{"graft.interactive"}` を含む
- [ ] `current_module="graft.core.pipeline"` + `from graft import export` → `{"graft.export"}` を含む
- [ ] `current_module="graft.core.parameters.resolver"` + `from . import context` → `{"graft.core.parameters.context"}` を含む
- [ ] `from ..export import *` は `{"graft.export"}` のみ（`*` は展開しない）
- [ ] 解決不能な相対 import（例: `current_module="graft.core"` で `from ...export import svg`）は AssertionError（もしくは明示メッセージ）になる

## 実装チェックリスト（合意後に着手）

- [ ] 1. “パス → モジュール名” を小関数化（`__init__.py` の扱いを固定）
- [ ] 2. `ast.ImportFrom` の解決ロジックを純粋関数化（`current_module` と `node` から set[str] を返す）
- [ ] 3. `_import_modules_in_file()` を「AST 収集」と「ImportFrom 解決」の合成に整理
- [ ] 4. `from pkg import subpkg`（alias 側）の抜け道も同時に潰す（`base + "." + name` を追加）
- [ ] 5. 失敗メッセージを改善（解決不能な相対 import は “解決不能” を含めて出す）
- [ ] 6. ヘルパーのユニットテストを追加し、`pytest -q tests/architecture/test_dependency_boundaries.py` を通す

## 事前確認したいこと（判断ポイント）

- `from graft import export` を「境界違反として検出する」方針で問題ないか（意図どおりなら穴が一段減る）。；はい
- 非パッケージ扱いのサブディレクトリ（`__init__.py` が無い場所）についても、境界検査のため “パス由来の擬似モジュール名” で解決してよいか。；はい
  - ここで厳密な import 解決（実在確認）を始めると複雑化するため、今回は行わない想定。

## 非ゴール（この計画ではやらない）

- `importlib` / `__import__` / `exec` 等の動的 import 検出
- 実在モジュールの検証（ファイル/ディレクトリ存在チェック）
- 依存境界ルール自体の拡張（`src/graft/interactive` など別領域の追加）
