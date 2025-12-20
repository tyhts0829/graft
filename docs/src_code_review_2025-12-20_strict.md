# src 配下モジュール 厳しめコードレビュー（2025-12-20）

対象: `src/grafix/`（`api/`, `core/`, `export/`, `interactive/`）  
観点: アーキテクチャ / 可読性 / クラス設計（厳しめ）  
備考: 実コード読解 + ローカルで `mypy src/grafix` を実行（`ruff` は環境に無く実行不可）。

---

## 総評（先に結論）

- **コア発想（Geometry DAG + realize + interactive/export の分離）は良い**。`core` が `interactive` を参照しない依存方向も健全で、設計の芯はできている。
- ただし **「src に置いてはいけないコード（`from_previous_project`）が大量に混在」**しており、可読性/保守性/型検査の全てを破壊している。ここが最大のボトルネック。
- もう一つの大きい問題は **設定ファイルの二重管理**（`pyproject.toml` と `mypy.ini`）。期待している品質ゲート（mypy）が事実上機能していない。
- `ParamStore` が **肥大化して God-object 化**しており、読み手が「どこを触ると何が壊れるか」を追えない。設計の中心としては危険。

---

## 最優先で直すべき構造問題（放置すると負債が加速）

### 1) `from_previous_project` が `src/` にあるのはアウト

該当例:

- `src/grafix/core/effects/from_previous_project/`
- `src/grafix/core/primitives/from_previous_project/`

現状の問題:

- **現行プロジェクトと無関係な import（`engine.*`, `common.*`, `util.*` 等）が残っており、型検査/静的解析/IDE 体験を破壊**している。
- 「参考コード」目的なら **`src`（=製品コード置き場）に置くべきではない**。`src` にある時点で「動くはずのコード」に見える。
- 実際 `mypy src/grafix` はこの領域が主因で大量エラーになっている。

提案（方針）:

- 参考として残すなら `docs/archive/` や `sketch/` 等に移す。  
  そうでないなら削除。**どちらにせよ `src` から追い出す**のが最優先。

### 2) mypy 設定が二重で、意図が反映されていない

現状:

- `pyproject.toml` の `[tool.mypy]` に `ignore_missing_imports = true` がある。
- しかしルートに `mypy.ini` があり、こちらが優先されているため、**`ignore_missing_imports` が効かず import 系エラーが噴出**している。

提案:

- **設定は 1 箇所に寄せる**（`mypy.ini` をやめて `pyproject.toml` へ統合する、または `mypy.ini` に必要設定を全て書く）。

---

## アーキテクチャレビュー

### 良い点（残すべき設計の芯）

- `core` / `interactive` / `export` の分割は概ね適切。
  - `core` が純粋な “レシピ（Geometry）→実体（RealizedGeometry）” を担い、
  - `interactive` が GUI/GL/MIDI を担い、
  - `export` がヘッドレス出力を担う、という方向性は読みやすい。
- `Geometry` を **不変（`frozen=True`）+ 内容署名（`id`）**で扱う設計は強い。
  - キャッシュ（`realize_cache`）と相性が良い。
  - `RealizedGeometry` も writeable=False に固定し、不変条件を保とうとしているのは堅い。
- `contextvars` + `parameter_context` による **フレーム単位スナップショット固定**は筋が良い。
  - GUI がフレーム中に値を変えても「このフレームの決定値がブレない」ため、描画が安定する。

### 悪い点（設計の境界が曖昧/責務が混ざっている）

- **「登録（registry）」が import 副作用に依存**している。
  - `grafix.api.effects` / `grafix.api.primitives` がモジュール import で組み込み effect/primitive を登録している。
  - これは “動く” が、**「どの import が何を登録するか」**が追いにくく、ビルド時間/起動時間/テストの分離が悪化しやすい。
  - `mp_draw` 側で “念のため import” しているのは、この不安定さの裏返し。
- `export` は「ヘッドレス出力」と言いながら、現時点では `Export.__init__()` が実行責務を持つなど **API 形状が不自然**。
  - **コンストラクタが副作用（ファイル書き込み）を持つ**のは読み手に優しくない。
  - せめて `Export(...).save()` のように “実行メソッド” を切るか、関数 API に寄せたい。

---

## 可読性レビュー（厳しめ）

### 1) 長大ファイルが多すぎる（分割の単位が粗い）

行数が大きい例（目視で 500 行超が多数）:

- `src/grafix/core/effects/displace.py`（約 769 行）
- `src/grafix/core/effects/fill.py`（約 759 行）
- `src/grafix/core/effects/weave.py`（約 636 行）
- `src/grafix/core/parameters/store.py`（約 580 行）
- `src/grafix/interactive/parameter_gui/table.py`（約 591 行）
- `src/grafix/core/primitives/text.py`（約 520 行）

問題点:

- “何をしているか” を掴むまでのコストが高く、局所修正が怖い。
- テスト可能な純粋関数へ分離されていない箇所が残りやすい。

提案:

- **処理のレイヤ**（前処理/幾何計算/後処理/検証/変換）でファイル内分割、またはモジュール分割する。
- 特に `ParamStore` と GUI の table は、責務が増えやすいので早めに分割した方が後が楽。

### 2) 例外処理が過剰・握りつぶしが多い箇所がある

例: `src/grafix/cc.py` の `CcView` は広い `except Exception` が多く、結果として **入力ミスと実装バグを区別できない**。

問題点:

- “壊れているのに静かに動く” はデバッグコストを爆発させる。
- 特に `cc` はライブ入力なので、バグが出ると再現が難しい。

提案:

- 期待する異常系だけを捕捉し、**本当におかしいケースは例外で落とす**。
- どうしても握りつぶすなら、せめて `logging` で 1 回だけ警告を出す等、観測可能性を残す。

### 3) ドキュメンテーションの流儀が混ざっている

- 多くのモジュールは「どこで/何を/なぜ」ヘッダ＋ NumPy docstring を徹底していて良い。
- 一方で `interactive/gl/line_mesh.py` などは説明が口語コメント中心で、プロジェクト内の統一感が崩れている。
- `core/effects/` はディレクトリ内 AGENTS により “どこで/何を/なぜ禁止” という例外ルールがあるのは理解できるが、**例外ルールがあるなら他ディレクトリはより統一しないと読み手が迷う**。

---

## クラス設計レビュー

### 良い点

- `Geometry`, `Layer`, `RenderSettings` など、**小さく immutable なデータクラス**が軸になっているのは強い。
- `RealizedGeometry` が不変条件を強制するのも良い（配列 writeable=False）。

### 悪い点（設計負債になりやすい）

#### 1) `ParamStore` が役割を持ちすぎ（God-object）

`ParamStore` は少なくとも以下を同居させている:

- state/meta 管理
- label 管理
- ordinal（グループ安定化）の割当・圧縮
- effect chain 情報（chain_id/step_index）管理
- reconcile（loaded/observed の突合）と migrate
- prune
- JSON serialize/deserialize

結果:

- 変更が “横に波及” しやすい（特に reconcile/ordinal/label が絡む）。
- 仕様がコードの奥に埋もれ、読み手が追えない。

提案:

- `ParamStore` を「最小の永続辞書」として核にし、周辺機能（ordinal 管理、reconcile、JSON 永続化）は別オブジェクト/別モジュールへ分離したい。

#### 2) `ParamMeta.kind` が文字列で、実質的に型安全がない

現状 `kind: str`（`"float" | "int" | ...`）で分岐している。

問題点:

- スペルミスで静かに壊れる（`"floaat"` など）。
- 分岐の網羅性が保証されない。

提案:

- `Literal[...]` か `Enum` で型として固定する（最低でも `Literal`）。

#### 3) `Export` が “クラス” の形をしているが “関数” 的

`Export.__init__()` が実行を担い、生成＝副作用になっている。

問題点:

- 呼び出し側が例外/実行タイミングを制御しづらい。
- 生成した `Export` インスタンスを保持する意味が薄い。

提案:

- `export(draw, t, ...)` の関数に寄せるか、`Export` を “設定＋実行メソッド” 型にする。

---

## 型・静的解析（mypy 実行結果の要約）

`mypy src/grafix` は **116 errors / 46 files**（この環境）でした。

主因（構造的）:

- `from_previous_project` の壊れた import が大量（前述）。
- mypy 設定が二重で、`ignore_missing_imports` 等が効いていない可能性が高い（前述）。

現行コード側で “素直に直せる” 類の指摘例:

- `src/grafix/core/realize.py`: `func` 変数を primitive/effect で使い回しており、型推論が崩れている（設計的にも読みづらいので変数名を分けた方が良い）。
- `src/grafix/core/realized_geometry.py`: `new_offsets` の型注釈不足。
- `src/grafix/core/parameters/view.py`: `cc_key` のタプル分解周りで型が確定しない（素直に注釈か正規化関数が欲しい）。
- `src/grafix/api/export.py`: `canvas_size=tuple(canvas_size)` が `tuple[int, ...]` 扱いになり型不一致（`tuple[int, int]` を明示するべき）。

---

## 依存関係/パッケージング観点（アーキテクチャ寄りの指摘）

- `pyproject.toml` の `dependencies` が **“core だけ使いたい” ケースでも interactive 依存を強制**する形になっている。
  - `moderngl`, `pyglet`, `imgui`, `mido`, `python-rtmidi` 等は環境依存が強い。
  - 将来配布するなら、`core` と `interactive` の依存は extras に分離しないとインストール障壁が高い。

（※現段階でユーザーがいない前提でも、開発体験として “必要なときだけ入る” は効く）

---

## まとめ（いまのまま進むと詰まるポイント）

- **`from_previous_project` を `src` に置いている限り、レビューも解析も常にノイズまみれ**になる。
- mypy 設定が統一されていないため、**「型で守る」方針が実質崩れている**。
- `ParamStore` の肥大化は、今の機能追加ペースのまま続くと **最初に破綻する中心点**になりやすい。

