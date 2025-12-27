# parameter_gui: Effect/Primitive パラメータ並び順改善 plan（2025-12-27）

## 背景（現状）

- 現状の `parameter_gui` は、最終的な行順を `src/grafix/interactive/parameter_gui/store_bridge.py` の `_order_rows_for_display()` で決めている。
- primitive/effect の「ブロック内（同一呼び出し内）」のパラメータ順は、いまは `arg` の辞書順（文字列ソート）になっている。
  - primitive: `sorted(block_rows, key=lambda r: str(r.arg))`
  - effect: `step_index` → `arg` の辞書順（`_step_sort_key`）

## 目的（こうしたい）

- effect の各ステップ内の並び順を:
  1) `bypass`（`@effect` デコレータ共通の予約パラメータ）
  2) それ以外は **effect 関数の引数定義順（シグネチャ順）**
  にする。
- primitive の各呼び出し内の並び順を:
  - `bypass` は無いので、**primitive 関数の引数定義順（シグネチャ順）**にする。

## 非目的（やらない）

- ブロック自体（primitive/effect/other の並び、effect チェーン順、style の固定順など）の仕様変更。
- `other` ブロックの arg 並び順の変更（必要になったら別途）。

## 方針（実装案）

### 1) 「引数定義順」をレジストリに保存する

`store_bridge` 側で毎回 `inspect.signature()` するのではなく、登録時（デコレータ適用時）に一度だけ計算してレジストリに保持する。

- `src/grafix/core/effect_registry.py`
  - `EffectRegistry` に `op -> tuple[str, ...]` の引数順情報を持たせる（例: `_param_order: dict[str, tuple[str, ...]]`）。
  - `@effect(meta=...)` の `decorator()` 内で `inspect.signature(f)` から **キーワード引数の定義順**を取り出す。
  - UI に出す対象は `meta` で管理されている引数だけなので、シグネチャ順を `meta.keys()` の集合でフィルタする。
  - effect は `bypass` がシグネチャに無いので、UI 順は `("bypass", *sig_order_filtered)` で確定する。
  - `effect_registry.get_param_order(op)` のような参照 API を追加する（戻りは `tuple[str, ...]`）。

- `src/grafix/core/primitive_registry.py`
  - `PrimitiveRegistry` も同様に `op -> tuple[str, ...]` を保持する。
  - UI に出す対象は `meta` 引数だけなので、シグネチャ順を `meta.keys()` の集合でフィルタしたものを保存する。
  - `primitive_registry.get_param_order(op)` のような参照 API を追加する。

### 2) Parameter GUI のソートキーを「arg名」→「レジストリ順」へ置換する

`src/grafix/interactive/parameter_gui/store_bridge.py` の `_order_rows_for_display()` 内で、ブロック内の並び替えロジックを変更する。

- primitive ブロック内:
  - `primitive_registry.get_param_order(op)` で `arg -> index` を作り、
    `key=(index, arg)` のように並べる（未知 arg は末尾へ）。
- effect チェーン内:
  - `_step_sort_key` を `(step_index, arg)` → `(step_index, param_index, arg)` に変更する。
  - `param_index` は `effect_registry.get_param_order(r.op)` 由来（`bypass` が常に最小 index）。

### 3) テストで仕様を固定する

既存テストは「ブロック間の順序」中心なので、今回の「ブロック内の引数順」を新規テストで固定する。

- 追加: `tests/interactive/parameter_gui/test_parameter_gui_param_order.py`
  - primitive: 例として `polygon`（`n_sides, phase, center, scale`）の `ParameterRow` をわざとシャッフルして入力し、出力がシグネチャ順になることを確認。
  - effect: 例として `scale`（`bypass, auto_center, pivot, scale`）の `ParameterRow` をシャッフルして入力し、出力が `bypass` → シグネチャ順になることを確認。
  - effect は `step_info_by_site` を与えて同一チェーン同一 step として扱わせる（`step_index` 0 の中での並び順だけを見る）。

## 受け入れ条件（Definition of Done）

- parameter_gui 上で:
  - effect ステップ内の先頭が常に `bypass` になる。
  - 2つめ以降が effect 関数の引数定義順になる。
  - primitive 呼び出し内が primitive 関数の引数定義順になる。
- 追加したテストが通る。
- 少なくとも関連範囲の既存テスト（`tests/interactive/parameter_gui`）が通る。

## 未確定点（先に確認したい）

- `meta` に含まれるがシグネチャに無い/順序が取れないケースは現状エラーになり得るが、今回も「組み込みは meta 必須＆整合前提」で進めて良い？（過度に防御的にはしない方針）
- シグネチャ/メタに存在しない未知 `arg` が UI 行に混じった場合の扱い:
  - 案: 末尾に寄せて `arg` 辞書順で安定化（UI が壊れない範囲の最小限）。

## 作業チェックリスト

- [ ] 仕様確認: 上の「未確定点」を確定する
- [ ] 実装: `src/grafix/core/effect_registry.py` に `op -> param_order` を保持・参照する仕組みを追加
- [ ] 実装: `src/grafix/core/primitive_registry.py` に `op -> param_order` を保持・参照する仕組みを追加
- [ ] 実装: `src/grafix/interactive/parameter_gui/store_bridge.py` のブロック内ソートを `param_order` ベースに変更
- [ ] テスト: `tests/interactive/parameter_gui/test_parameter_gui_param_order.py` を追加
- [ ] 検証: `PYTHONPATH=src pytest -q tests/interactive/parameter_gui`
- [ ] 検証: `ruff check`（対象限定）と `mypy src/grafix`（必要なら対象限定）

