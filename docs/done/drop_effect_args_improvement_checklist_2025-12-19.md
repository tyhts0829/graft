# drop effect 引数改善チェックリスト（2025-12-19）

対象: `src/grafix/core/effects/drop.py`

## 目的

- `drop` の引数を「分かりやすく」「意図しない挙動をしにくく」する。
- 旧仕様（OR 条件 + keep/drop）を維持しつつ、曖昧さ・誤用を減らす。
- `by="face"` を「きちんと」実装して、面単位での間引きを可能にする。

## 前提（現状）

- `min_length/max_length` は 0 未満を無効扱い（sentinel は -1.0 だが「0 未満」全般が無効）。
- `keep_mode/by` は `ParamMeta(kind="choice")` だが、未知文字列でもそのまま effect 実装に渡り得る。
- `offset` は interval の位相だが、名前が「距離オフセット」と紛らわしい。
- 現在の `by="face"` は実装上 `line` と同扱い（面としてのグルーピングや選択が無い）。

## 方針（提案）

- 例外で落とすより、「無効値は no-op（=入力を返す）」に寄せる（既存 effect の流儀に合わせる）。
- 引数名・doc を優先して改善し、ロジックの複雑化はしない。

## 変更候補（要確認）

- `offset` をリネームする（破壊的変更）
  - 候補: `interval_offset` / `phase` / `index_offset`（採用: `index_offset`）
  - 目的: 「インデックス位相」だと一目で分かるようにする
- `probability` の異常値ポリシーを決める
  - 候補 A: 非有限（NaN/inf）や範囲外は no-op
  - 候補 B: <0 は 0、>1 は 1 に丸める、非有限は no-op（採用）
- `keep_mode/by` の未知値ポリシーを決める
  - 候補 A: 未知値なら no-op（採用）
  - 候補 B: 未知値ならデフォルト（keep_mode="drop", by="line"）に丸める
- `by="face"` の「面」定義と選択単位を決める（今回きちんと実装する）
  - 面の単位案:
    - 案 1: 1 ポリライン（頂点数 >=3）を 1 face ring とみなす（穴は扱わない）（採用）
    - 案 2: even-odd の包含関係で `[outer, hole...]` を 1 face とみなす（`fill` と整合）
  - 2D 化（包含判定）の扱い案（今回: 穴を扱わないため該当なし）
    - 案 A: XY 平面（z を無視）で判定する（最小実装、2D 前提）
    - 案 B: 平面整列して判定する（例: 全点から推定平面を作る）※複雑化注意
  - `min_length/max_length` の「長さ」解釈（face 時）案:
    - 案 A: outer ring の周長（閉曲線として最後 → 最初も含む）（採用）
    - 案 B: outer+holes の周長合計
  - `by="face"` のとき、2 点ポリライン等（線）の扱い案:
    - 案 A: そのまま出力に残し、face だけを間引く（採用）
    - 案 B: line も「独立 face」とみなして同じ規則で間引く

## 実装 TODO（チェックリスト）

### 仕様確定（先に確認）

- [x] `offset` の新しい名前を決める（`index_offset`）
- [x] `probability` の異常値ポリシーを確定する（<0→0、>1→1、非有限→no-op）
- [x] `keep_mode/by` の未知値ポリシーを確定する（未知値なら no-op）
- [x] `by="face"` の面の単位（案 1）を確定する
- [x] `by="face"` の 2D 化（該当なし: 穴を扱わない）
- [x] `by="face"` の length 解釈（案 A: 周長）を確定する
- [x] `by="face"` のときの line 扱い（案 A: line は常に残す）を確定する

### 実装（drop 本体）

- [x] `offset` を新名称へ変更（`drop_meta` と関数シグネチャ）
- [x] docstring を新名称に合わせて更新（API スタブに反映される）
- [x] `probability` を検証/正規化（<0→0、>1→1、非有限→no-op）
- [x] `keep_mode` を検証/正規化（未知値なら no-op）
- [x] `by` を検証/正規化（未知値なら no-op）
- [x] `by="face"` の選択単位を実装
  - [x] ring（頂点数>=3）抽出ロジックを確定方針どおりに実装
  - [x] face グルーピング（案 2）は不要（穴を扱わない）
  - [x] face index の順序（interval 判定に使う）を定義し、決定的にする（入力順）
  - [x] face 周長（length 判定）計算を実装（閉曲線として最後 → 最初を含む）
  - [x] probability を face 単位で適用（1 face=1 回の乱数判定）
  - [x] face 判定結果から `offsets` を再構築し、face ring を drop/keep する
  - [x] `by="face"` 時の line は常に残す（face 判定対象外）

### テスト

- [x] `tests/core/effects/test_drop.py` を更新（リネーム追従）
- [x] 異常値テストを追加
  - [x] `probability` が NaN/inf/範囲外のときの挙動（NaN/inf は impl 直接呼び出しで検証）
  - [x] `keep_mode/by` が未知値のときの挙動
- [x] `by="face"` のテストを追加
  - [x] face ring（頂点数>=3）を対象に interval/probability/length が効く
  - [x] 穴（hole）は今回扱わない（テスト不要）
  - [x] 2 点ポリラインが face 操作の影響を受けない

### スタブ同期（公開 API 変更があるため）

- [x] `python -m tools.gen_g_stubs`（生成結果で上書き）
- [x] `PYTHONPATH=src pytest -q tests/stubs/test_api_stub_sync.py`

### 検証（対象限定で実行）

- [x] `PYTHONPATH=src pytest -q tests/core/effects/test_drop.py`
- [x] `mypy src/grafix/core/effects/drop.py`
- [ ] `ruff check src/grafix/core/effects/drop.py tests/core/effects/test_drop.py`（環境に ruff が無く未実行）
