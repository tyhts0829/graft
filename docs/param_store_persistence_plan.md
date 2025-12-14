# param_store_persistence_plan.md

どこで: `src/api/run.py` と `src/parameters/store.py` 周辺。永続化データはリポジトリ直下 `data/param_store/` 配下。
何を: `parameter_gui` 等で編集した `ParamStore`（UI 値/override/ui_min/ui_max/choices/CC 割当など）を JSON として保存し、次回起動時に自動復元する仕組みを追加する。
なぜ: プレビュー調整の反復（起動 → 調整 → 終了 → 再起動）で毎回つまみを戻す手間を無くし、スケッチ単位で「前回の状態」から作業を再開できるようにするため。

## 0. ゴール / スコープ

- run 実行終了時に `ParamStore` を JSON 保存し、次回 run 開始時に復元する。
- 保存先は `data` 配下に新規ディレクトリを切り、そこへ保存する（例: `data/param_store/`）。
- 永続化ファイル名には「描画スクリプト名（例: `main.py` / `sketch/251214.py` の stem）」を必ず含める。
- ここでは設計と実装タスク分割までを扱い、実装は別タスクで進める。

## 1. 要件（今回の確定事項）

- パラメータは JSON で永続化する。
- 保存場所は `data/<dir>/`（`data` 配下にディレクトリを切る）。
- 保存ファイル名に描画スクリプト名を含める。
- 次に再び描画するとき、前回保存された値へ復帰する。

## 2. 保存キー（描画スクリプト名）の決め方

### 2.1 取得元

- `run(draw=...)` に渡された `draw` から「定義元ファイルパス」を取得する。
  - 第一候補: `draw.__code__.co_filename`
  - 代替: `inspect.getsourcefile(draw)`

### 2.2 ファイル名へ落とすルール（衝突を避けつつ単純）

- 既定の「スクリプトキー」は以下のいずれか:
  - **推奨**: リポジトリルートからの相対パス（拡張子除去）を `__` 区切りに正規化  
    例: `main.py` → `main`、`sketch/251214.py` → `sketch__251214`
  - 最小: stem のみ（`251214` など）※同名衝突の可能性あり
- ファイル名に使えない文字は `_` に置換する（例: 空白、`:`、`?` など）。

## 3. 保存先ディレクトリ / ファイル名規約

- 保存ディレクトリ: `data/param_store/`
- 保存ファイル名（例）:
  - `data/param_store/param_store__main.json`
  - `data/param_store/param_store__sketch__251214.json`
- ルール:
  - `param_store__{script_key}.json`
  - `script_key` は必ずスクリプト名（stem）を含むこと（要件）。

## 4. どのデータを永続化するか

- `ParamStore.to_json()` / `ParamStore.from_json()` をそのまま利用する。
  - 既に `states/meta/labels/ordinals/effect_steps/chain_ordinals` を保持しているため、GUI 表示順や CC 割当も復元対象に含められる。

## 5. 読み込み・保存タイミング

- **読み込み**: `run()` の `ParamStore()` 生成前に、既定パスの JSON が存在すれば `ParamStore.from_json()` で復元して利用する。
- **保存**: `run()` の `finally`（ループ終了後、window close の後始末に入る前後どちらかで固定）で `store.to_json()` をファイルへ書き出す。

## 6. 実装方針（責務の置き場所）

### 6.1 新規モジュール案（推奨）

- 新規: `src/parameters/persistence.py`（または `store_io.py`）
  - `default_param_store_path(draw: Callable[..., Any]) -> Path`
  - `load_param_store(path: Path) -> ParamStore`
  - `save_param_store(store: ParamStore, path: Path) -> None`
- `src/api/run.py` は「ロードして渡す / finally でセーブする」だけに寄せる（run の肥大化を避ける）。

### 6.2 `run()` の公開 API 変更（候補）

- 候補 A（最小・自動）: 引数追加なしで **常に** `data/param_store/...` に保存/復元する。
  - 期待: “起動するだけで前回復帰” の体験が最短。
  - 懸念: 一時的に保存したくないケースが出たときに逃げ道が無い。
- 候補 B（扱いやすい）: `run(..., parameter_persistence: bool = True)` を追加する。；これで。
  - `False` のときはロード/セーブしない。
- 候補 C（拡張）: `parameter_persistence_key: str | None = None` を追加し、手動で保存先を切り替え可能にする。

※今回の要件だけなら B までで十分。C は必要が出てから。

## 7. テスト方針（pytest）

- `tests/parameters/test_param_store_persistence.py`（新規）:
  - `ParamStore` の簡単な状態を作って `save → load` し、主要フィールドが復元できることを確認。
  - `default_param_store_path()` が
    - ファイル名にスクリプト stem を含む
    - `data/param_store/` 配下になる
    - 区切り/置換ルールが期待通り
      を満たすことを確認（tmpdir を使う）。

## 8. 手動確認（スモーク）

- `python main.py` で起動 → GUI で値を変更 → ウィンドウを閉じる
- 再度 `python main.py` → GUI で前回値に復帰していることを確認
- `sketch/251214.py` でも同様に確認（スクリプトごとに別ファイルになることを確認）

## 9. オープン事項（要確認）

- JSON が壊れていた場合の挙動:
  - 例外で落としてパスを提示（単純）
  - もしくは「無視して新規ストアで起動」（利便性） ；こちらで
    どちらを採るか決める。
- 保存する粒度:
  - 終了時のみ（今回の最小）；こちらで
  - GUI 変更検知時の自動保存（クラッシュ耐性は上がるが、実装は増える）

## 10. 実装チェックリスト（実装フェーズで消し込み）

- [ ] 保存先ディレクトリを `data/param_store/` に確定
- [ ] `script_key` の正規化ルール（相対パス or stem）を確定；stem で
- [ ] `src/parameters/persistence.py` を追加（path 算出 / load / save）
- [ ] `src/api/run.py` にロード/セーブを組み込み
- [ ] `pytest` の単体テストを追加（roundtrip + path ルール）
- [ ] `main.py` と `sketch/` のスモーク手順で復元を確認
