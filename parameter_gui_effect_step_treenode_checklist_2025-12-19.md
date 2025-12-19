# Parameter GUI: effect step を TreeNodeEx で折りたたみ可能にするチェックリスト（2025-12-19）

対象: `src/grafix/interactive/parameter_gui/`

## 目的

- effect chain の中で **step（= effect 種類）単位**に折りたためるようにして、縦スクロール量と視線移動を減らす。
- `separator` での擬似 `SeparatorText` のような「見た目が崩れる/制約が強い」手法に依存しない。
- 既存方針どおり、**グルーピングは純粋関数＋ unit test**で担保し、描画側は薄く保つ。

## 前提（現状）

- chain 単位のヘッダは `collapsing_header` で出している（例: `xf`, `effect#1`）。
- chain 内は 4 列 table（label / control / min-max / cc）で縦に並ぶ。
- effect 行の label は「ノイズ削減」のため、（望ましくは）`arg` のみ表示にしたい。
  - step 名（`scale#1` 等）は step 見出し側が担う。

## 目標 UI（イメージ）

```
xf  (chain header)
  ▾ scale#1
      auto_center   [control]  [minmax] [cc]
      s             [control]  [minmax] [cc]
  ▸ rotate#1
  ▾ scale#2
      ...
```

## 0) 事前に決める（あなたの確認が必要）

- [x] step 見出しの表示名: `op#N`（例: `scale#1`）
- [x] step 見出しのデフォルト状態: `TREE_NODE_DEFAULT_OPEN`（最初は開く）
- [x] step 見出しに件数を出すか: 出さない
- [x] chain ヘッダ（既存 `collapsing_header`）は維持する

## 実装 TODO（チェックリスト）

### 1) step ブロック化（純粋関数）

狙い: `table.py` に「step 境界判定」を書かない。

- [x] 新 dataclass に「step 単位のまとまり」を表現できる構造を導入
  - 案: `EffectStepBlock(header_id, header_text, items)`
- [x] chain 内の rows から `EffectStepBlock` 列を作る純粋関数を実装
  - step 判定キー: `(row.op, row.site_id)`
  - step 見出し: 0) の決定に従い `op` or `op#N`
    - `N` は `effect_step_ordinal_by_site[(op, site_id)]` を使用
    - 無い場合フォールバック（例: `op`）
- [x] unit test 追加
  - [x] `scale#1` / `rotate#1` / `scale#2` の順に step ブロック化される
  - [x] 同一 step 内で複数 arg が連続しても 1 ブロックになる

### 2) 描画（TreeNodeEx）を導入

狙い: 見出しは table の外に出して全幅で描き、各 step の中だけ table を描く（見た目/実装が安定）。

- [x] effect_chain ブロックの描画を「step ループ」に変更
  - [x] `imgui.tree_node_ex()` を step 見出しとして描画
    - 安定 ID: `##{step_header_id}` を付与（site_id/chain_id を使う）
    - flags: `FRAMED` / `SPAN_FULL_WIDTH` / `DEFAULT_OPEN`（0) に従う）
  - [x] open のときだけ、その step の items を table で描画
  - [x] closed のときは描画しないが、`updated_rows` は入力 rows と 1:1 で揃える（変更なしとして返す）
- [x] カラムヘッダ（label/control/min-max/cc）の描画ポリシーを決めて実装
  - 案: 「最初に開いた step の table」で 1 回だけ描く（ノイズ削減）
  - first open が無い場合（全閉）も破綻しない

### 3) ラベル表示の整理（ノイズ削減）

- [x] effect 行の label を `arg` のみにする（step 名は見出しへ移動）
  - 既にそうなっている場合は維持
- [x] Primitive/Style の表示は今回変更しない

### 4) テスト更新

- [x] 既存の grouping/blocking テストを新構造に追従
- [x] step ブロック化テストを追加（1) で実施）

### 5) 検証（対象限定で実行）

- [x] `PYTHONPATH=src pytest -q tests/interactive/parameter_gui`
- [x] `mypy src/grafix/interactive/parameter_gui`
- [ ] `ruff check src/grafix/interactive/parameter_gui tests/interactive/parameter_gui`（環境に ruff が無く未実行）

## 受け入れ条件（Done の定義）

- effect chain 内で step ごとに `TreeNodeEx` 見出しが出て、step 単位で折りたためる。
- 閉じた step は行が描画されない（= 縦の密度が下がる）。
- effect の各パラメータ行の label は `arg` のみで、繰り返しノイズが無い。
- `PYTHONPATH=src pytest -q tests/interactive/parameter_gui` が通る。

## 事前確認したいこと（追加）

- ruff が入っている環境で `ruff check` だけ最終確認したい。
