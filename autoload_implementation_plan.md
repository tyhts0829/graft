# どこで: Grafix リポジトリ（設計メモ / 実装チェックリスト）。
# 何を: user 定義の primitive/effect/preset をまとめて import する `autoload()` を導入する計画。
# なぜ: 「使う前に import して登録が必要」を 1 箇所（1行）に寄せ、スケッチの UX を上げるため。

# autoload() 導入: 実装計画

## ゴール

- `from grafix import autoload`（または `from grafix.api import autoload`）で関数を提供する。
- スケッチ先頭で `autoload()` を 1 回呼べば、ユーザー定義 `@primitive/@effect/@preset` が使える状態になる。
- 仕組みが単純で、失敗時の原因が追いやすい（暗黙探索より “設定されたモジュールを import” を優先）。

## 非ゴール（今回やらない）

- `G.foo` 参照時に勝手に探索 import するような遅延ロード（魔法が強くデバッグが辛い）。
- 複雑なプロジェクトルート推測（最初は CWD 基準で十分）。

## UX 案（最小）

### 方式A: 明示モジュール列

- `autoload("user_primitives", "user_effects", "user_presets")` のように、モジュール名を渡す。
- 一番シンプルで、挙動が予測しやすい。

### 方式B: 規約ファイル（引数なし autoload）

- `autoload()` は `.grafix/autoload_modules.txt`（新規）を探し、1行=1モジュールで import する。
- 例（`.grafix/autoload_modules.txt`）:
  - `myproj.user_primitives`
  - `myproj.user_effects`
  - `myproj.user_presets`

> 初期実装は A を必須、B は “あれば使う” でよい（実装が小さい）。

## 仕様を先に決めたい点（要確認）

- `autoload()` の探索基準:
  - CWD（`Path.cwd()`）固定で良い？（`python sketch/foo.py` をリポルートで実行する前提）
- `.grafix/autoload_modules.txt` が無い場合:
  - 何もしない（silent）で良い？それとも例外？
- `run(draw)` の内部で自動的に `autoload()` する？
  - “書かなくてよい” は強いが、import 副作用が見えにくくなる（明示1行を推したい）。

## 実装チェックリスト

- [ ] `src/grafix/api/autoload.py`（新規）に `autoload()` を実装する
  - [ ] `importlib.import_module()` で “指定モジュールを import するだけ” にする
  - [ ] `.grafix/autoload_modules.txt` が存在すれば読み取り、空行/`#` コメント行を無視して import する
- [ ] `src/grafix/api/__init__.py` から `autoload` を再エクスポートする（`__all__` 更新）
- [ ] `src/grafix/__init__.py` から `autoload` を再エクスポートする（`__all__` 更新）
- [ ] 型スタブ同期
  - [ ] `tools/gen_g_stubs.py` を更新して `autoload` を `src/grafix/api/__init__.pyi` に含める
  - [ ] `tests/stubs/test_api_stub_sync.py` が通る状態にする
- [ ] テスト追加
  - [ ] `tests/api/` に “一時モジュールを作って autoload で import される” 最小テストを追加する
    - 例: tmpdir に `user_primitives.py` を生成し、`@primitive` で登録→ `autoload("user_primitives")`→ `G.user_prim` が解決すること
- [ ] ドキュメント更新
  - [ ] `README.md` の “Extending” セクションに `autoload()` の推奨パターンを追記する

## 追加で気づいた点（提案）

- autoload と `P` を組み合わせると、スケッチ側の “import 行数” を実質 1–2 行に寄せられる:
  - `from grafix import G, E, P, autoload`
  - `autoload()`
  - `P.logo(...)` / `G.myprim(...)` / `E.myeff(...)(...)`

