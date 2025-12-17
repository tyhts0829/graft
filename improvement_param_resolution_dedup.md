# パラメータ解決ロジック（G/E）の重複解消：実装計画

作成日: 2025-12-17

## 背景（現状）

- `G`（Primitive）と `E`（Effect）で、以下の処理がほぼ同形で重複している。
  - `defaults` で省略引数を補完
  - ユーザ kwargs で上書き
  - `parameter_context` 内なら `resolve_params(...)` を呼び、観測レコードを積む
  - `name="..."` 指定時に `ParamStore.set_label(...)` を行う（ストア無しなら例外）
- Effect は追加で `chain_id` / `step_index` を `resolve_params` に渡す必要があるため、完全共通化は難しいが、
  「defaults 補完→resolve_params 呼び出し（条件分岐込み）」と「label 設定」の最小単位は共通化できる。

## 目的（ゴール）

- `G` と `E` に散らばる重複ロジックを 1 箇所に寄せる。
- 振る舞い（解決値・観測・ラベル付け・例外条件）を変えずに、将来の仕様変更漏れを防ぐ。

## 非目的（やらない）

- ParamStore/FrameParams/GUI の仕様変更
- 公開 API の変更（`G.circle(...)` / `E.scale(...)(g)` 等の呼び出し形は維持）
- 依存追加

## 方針（最小の共通化）

API 層（`src/graft/api/`）に内部ヘルパ 2 個だけ追加し、`primitives.py` / `effects.py` から呼ぶ。

### 追加するヘルパ案

1) `set_api_label(op, site_id, label)`  
`label is None` なら何もしない。そうでなければ `current_param_store()` を取得し、無ければ現行と同じ例外メッセージで `RuntimeError`。あれば `store.set_label(...)`。

2) `resolve_api_params(op, site_id, user_params, defaults, meta, *, chain_id=None, step_index=None)`  
`explicit_args = set(user_params.keys())` を取り、`base_params = dict(defaults); base_params.update(user_params)` を作る。  
`current_frame_params() is not None and meta` の場合のみ `resolve_params(...)` を呼ぶ（Effect の場合は `chain_id/step_index` も渡す）。それ以外は `base_params` を返す。

※ `meta`/`defaults` の取得（レジストリ参照）は現行どおり各ファイルで行い、ヘルパは「共通の組み立て」だけを担う。

## 実装チェックリスト（TODO）

- [ ] 共通化対象の最小範囲を確定（label も含めるか、params 解決だけにするか）
- [ ] `src/graft/api/_param_resolution.py`（仮）を新規作成し、上記ヘルパ 2 個を実装
- [ ] `src/graft/api/primitives.py` をヘルパ呼び出しに置換（挙動同一のまま）
- [ ] `src/graft/api/effects.py` をヘルパ呼び出しに置換（`chain_id/step_index` は維持）
- [ ] 既存テストで回帰確認（まずは対象限定）
  - [ ] `PYTHONPATH=src pytest -q tests/core/parameters/test_defaults_autopopulate.py`
  - [ ] `PYTHONPATH=src pytest -q tests/core/parameters/test_label_namespace.py`
  - [ ] `PYTHONPATH=src pytest -q tests/interactive/parameter_gui/test_parameter_gui_labeling_phase2.py`
- [ ] 静的チェック（対象限定）
  - [ ] `ruff check src/graft/api/primitives.py src/graft/api/effects.py src/graft/api/_param_resolution.py`
  - [ ] `mypy src/graft`

## 事前確認（あなたに確認したいこと）

1. ヘルパの共通化範囲は「params 解決 + label 設定」まで含めてよい？（params だけに絞る案も可）
2. ヘルパの置き場所/名前は `src/graft/api/_param_resolution.py` でよい？（別名希望があれば合わせる）
3. `E(name="...")` のラベル付けは現状どおり「各ステップに同じラベルを付ける」を維持でよい？

## 追加で気づいたこと（提案/懸念が出たら追記）

- （空欄：実装中に追記）

