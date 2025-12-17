# どこで: `docs/memo/conversation_md_refactor_review_2025-12-16.md`。
# 何を: `docs/conversation.md` の方針が現実装で“本当に”実現できているかを、依存分離と理解容易性の観点で厳しめにレビューした結果。
# なぜ: 「形だけの分割」に留まっていないか、次に直すべき本質（設計上の穴）を明確にするため。

# `docs/conversation.md` 準拠レビュー（2025-12-16）

## 結論（率直）

- **依存分離（core/export/interactive）自体は達成**。`src/core/` から `pyglet/moderngl/imgui` を排除し、`src/export/` と `src/interactive/` を外側に押し出せている。さらに `tests/architecture/test_dependency_boundaries.py` が “柵” として機械的に破壊を検知できるため、ディレクトリ分割の見た目だけではなく**構造として壊れにくい**。
- 一方で、会話文の主目的だった **「ヘッドレス一括出力がすぐ成立する」成果物レベルは未達**。`src/export/svg.py` / `image.py` / `gcode.py` がいずれも `NotImplementedError` のため、export 層は現状 “箱だけ”。
- 総合すると、**「依存方向の整理」はかなり良いが、「ヘッドレス出力」という価値の実現はまだこれから**、という状態。

## 対応表（`docs/conversation.md` → 現実装）

| 会話文の要点 | 判定 | 現実装での根拠 / コメント |
|---|---:|---|
| Core / Export(Render) / Interactive の 3 層に分離する | ✅ | `src/core/`, `src/export/`, `src/interactive/` が明確に存在。責務も概ねその通り。 |
| core と作品スクリプトから GPU/Window 依存を追い出す | ✅ | `tests/architecture/test_dependency_boundaries.py` が core の `pyglet/moderngl/imgui` import を禁止。`src/api/__init__.py` の `run()` は遅延 import で、`import src.api` 時点では GUI 依存が入らない。 |
| “render/export は後段アダプタ”として隔離する | ⚠️ | 形は `src/export/` に隔離できているが、出力実装が未着手（スタブ）。「隔離した」ことは成立、しかし「隔離したことでヘッドレス出力が回る」は未成立。 |
| まず SVG を正として吐けるようにする | ❌ | `src/export/svg.py` が未実装。現状の Export 経路は必ず例外で落ちる。 |
| SVG→PNG 等のラスタライズは後段に押し込む | ❌ | `src/export/image.py` も未実装。方針としては README/architecture.md に明記されているが、コードとしては無い。 |
| renderer の import を動的にして、必要時だけ依存を要求する | ⚠️ | `src.api.run` は公開 API からは遅延 import される（良い）。一方で renderer 選択（例: `--renderer gl/svg`）や `load_renderer()` 相当の “プラグイン読み込み” は未導入。 |
| `graft thumbs` 的なヘッドレス一括パイプライン（build→realize→export→index） | ❌ | CLI/バッチ実装は見当たらない。`src/core/pipeline.py` の土台はあるが、export が未実装なので成立しない。 |
| 依存方向をルール化して守る（機械的に検査） | ✅ | `tests/architecture/test_dependency_boundaries.py` により core/export の境界は検査できている。 |
| 依存ライブラリも層で分け、extras 等で “入れなくてよい” 状態にする | ❌ | 依存管理ファイル（pyproject/requirements 等）が無く、extras 分離は未着手。現状 README の Dependencies は一括列挙。 |

## 「形だけ」になっていないか？（依存分離の厳しめ評価）

### 良い（根本に効いている）点

- **境界がテストで固定されている**  
  口約束ではなく `tests/architecture/test_dependency_boundaries.py` が core/export を守るため、将来の変更で“いつの間にか core が pyglet を読む”事故が起きにくい。これは「形だけの分割」ではなく**運用可能な境界**。

- **公開 API の import 体験がヘッドレス安全**  
  `src/api/__init__.py` の `run()` が遅延 import なので、`from src.api import E, G, L, Export` までは GUI 依存なしで通る設計になっている（run を “呼ぶ” まで要求しない）。

- **interactive が core の出力を“使うだけ”になっている**  
  `DrawWindowSystem` が `realize_scene()` を呼び、`RealizedGeometry` を `DrawRenderer` に渡す流れは綺麗で、GPU/Window をコアに逆流させない。

### 気になる（根本の穴/将来壊れる可能性）点

- **export 層が未実装なので、「interactive は無くても作品生成できる」を満たしていない**  
  会話文の価値は “ヘッドレスでも大量に吐ける” で、そのための分離だったはずだが、現状は出力が無い。依存分離は達成しているのに、成果物の価値がまだ出ていないので、ここが最優先のギャップ。

- **境界テストが “相対 import” を見逃す設計になっている**  
  `tests/architecture/test_dependency_boundaries.py` の `_import_modules_in_file()` は `from ..foo import bar` のような相対 import を `node.level > 0` としてスキップしている。現状コードが相対 import を使っていないので直ちに壊れてはいないが、**理屈上は境界を迂回できる穴**が残っている（将来の破壊に弱い）。

- **`src/api/run.py` が “api 層” に置かれている点は境界理解を少し曇らせる**  
  公開 API としては正しいが、物理配置としては `interactive` の責務に見える（run 実装は pyglet に依存するため）。`src/api/__init__.py` の遅延 import で実害は抑えている一方、「層の見通し」は少し落ちる。

## 理解しやすさ（コードベースの読みやすさ）レビュー

### 良い点

- 多くのファイルに「どこで/何を/なぜ」ヘッダがあり、入口で迷いにくい。
- `src/core/pipeline.py` が “描画/出力の共通パイプライン” として機能していて、`interactive` と `export` の接続点が読み取りやすい。
- `interactive/runtime/` に “配線” が集約されており、ウィンドウループやサブシステムの責務分離が明快。

### 迷いやすい点（厳しめ）

- `src/core/effects/from_previous_project/` / `primitives/from_previous_project/` が core の直下に大量にあり、読者にとって「どれが現行で、どれが移植メモなのか」を毎回判断する必要がある。コアの輪郭を太らせている。
- `Export` が “コンストラクタ呼び出し = 実行” の副作用 API で、現状は必ず `NotImplementedError` になるため、API の存在が期待値を上げる割に動かない（学習コストに対してリターンが薄い）。
- `src` と `api` の import 経路が（`main.py` と `sketch/` で）混在しており、利用者目線での “正しい入口” が揺れやすい。

## 次のアクション候補（優先度順）

1. **SVG export を最小実装で通す**（会話文の主目的に直結）  
   `RealizedLayer`（coords/offsets）→ `<path d="...">` の変換をまず成立させる。
2. **境界テストの相対 import スキップ問題を潰す**  
   「ルールがあるのに抜け道がある」状態を解消する。
3. **“入口” の明確化**（ドキュメント/サンプルの import を統一）  
   headless と interactive の使い分けが一目で分かる導線にする。

