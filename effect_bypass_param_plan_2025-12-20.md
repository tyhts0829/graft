# Effect: bypass パラメータ追加計画（2025-12-20）

## 目的

- effect を GUI から一時的に無効化できるようにする（bypass）。
- `bypass=True` のとき、入力された図形（RealizedGeometry）を何も加工せず返す（no-op）。

## 対象範囲（今回）

- `src/grafix/core/effect_registry.py` の `@effect` デコレータ（登録・wrapper）。
- effect の meta/defaults（GUI / stubs の元）への `bypass` 追加。
- 影響テスト: parameters / stubs / realize の最小限。

## 仕様（提案）

- 追加される引数名: `bypass`（bool, default False）。
- bypass の挙動:
  - `bypass=False`: 既存どおり effect を適用する。
  - `bypass=True`:
    - `inputs` が 1 つ以上: `inputs[0]` をそのまま返す。
    - `inputs` が空: 空ジオメトリを返す（要確認）。
- GUI への露出:
  - meta がある effect（= GUI に出る effect）にのみ `bypass` を自動追加する。
  - meta 無しのユーザー定義 effect は、従来どおり GUI/`ParamStore.snapshot()` に出さない（既存テスト維持）。

## 実装方針（シンプル）

- 実装位置: `src/grafix/core/effect_registry.py` の `effect()`。
- `meta` がある場合のみ、登録用 meta/defaults に `bypass` を差し込む。
  - meta: `{"bypass": ParamMeta(kind="bool"), **meta}`
  - defaults: 既存 `_defaults_from_signature(f, meta)` で得た辞書に `bypass=False` を追加
- wrapper 側で `bypass` を吸収する（effect 実装関数に bypass を要求しない）。
  - `params = dict(args)` → `bypass = bool(params.pop("bypass", False))`
  - `if bypass: return inputs[0] ...`
  - `return f(inputs, **params)`

## 実装チェックリスト

### P0: 仕様確認（先に合意）

- [ ] `inputs` が空のときの bypass の返り値を決める（空ジオメトリ / 例外 / 既存処理）。；空ジオメトリ
- [ ] `inputs` が複数の effect（将来）で bypass が返す対象を決める（基本は `inputs[0]` で良い？）。；inputs をそのまま帰す
- [ ] meta 無し effect は GUI に出さない方針で OK か確認する。；ok

### P1: 実装

- [ ] `effect_registry.effect` で `bypass` を「meta/defaults に自動追加」（meta がある場合のみ）する。
- [ ] `effect_registry.effect` の wrapper で `bypass` を pop し、True なら no-op return する。
- [ ] `bypass` という予約名の衝突（meta に既にある等）の扱いを決めて反映する（raise / 上書き）。

### P2: テスト更新・追加

- [ ] `tests/core/parameters/test_defaults_autopopulate.py` の期待値に `bypass` を追加する（scale の args）。
- [ ] bypass の動作テストを追加する（例: `tests/core/test_effect_bypass.py`）
  - 例: `g = G.polygon(); out = realize(E.scale(bypass=True, scale=(2,2,2))(g))`
  - 期待: `out.coords` / `out.offsets` が入力と完全一致（同一インスタンスを期待するかは要相談）。
- [ ] `tests/core/parameters/test_user_defined_no_meta_is_not_in_gui.py` が維持されることを確認する（meta 無し effect が snapshot に出ない）。

### P3: スタブ同期

- [ ] `python -m tools.gen_g_stubs` を実行し、`src/grafix/api/__init__.pyi` を更新する。
- [ ] `PYTHONPATH=src pytest -q tests/stubs/test_api_stub_sync.py` で同期を確認する。

### P4: 検証（最小コマンド）

- [ ] `PYTHONPATH=src pytest -q tests/core/parameters/test_defaults_autopopulate.py`
- [ ] `PYTHONPATH=src pytest -q tests/core/parameters/test_user_defined_no_meta_is_not_in_gui.py`
- [ ] `PYTHONPATH=src pytest -q tests/core/test_effect_bypass.py`（追加した場合）
- [ ] `ruff check src/grafix/core/effect_registry.py`
- [ ] `mypy src/grafix`（必要なら）

## Done の定義（受け入れ条件）

- [ ] Parameter GUI で各 effect に `bypass` が表示され、True で確実に no-op になる。
- [ ] bypass を指定しても各 effect 実装関数に引数追加が不要（TypeError が出ない）。
- [ ] 既存の「meta 無し user-defined は GUI に出ない」仕様が維持される。
- [ ] stubs 同期テストが通る。

## 事前確認したいこと（あなたに質問）

- [ ] bypass=True で「戻り値は入力と同一インスタンス」を期待する？（性能/キャッシュの観点）；実装がシンプルな方でいいよ。
- [ ] bypass=True のとき、「effect ノード自体を DAG から省略」したい？（API 層での最適化が必要になる）；No
