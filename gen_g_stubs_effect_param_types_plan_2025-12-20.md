# `tools/gen_g_stubs.py` の effect 引数型を「実装シグネチャ由来」にする計画（案 A）（2025-12-20）

## 目的

- `tools/gen_g_stubs.py` の `_override_type_for_effect_param()` が `fill` だけを特別扱いしている状態を解消する。
- 今後、他の effect でも「scalar + Sequence」の受け入れを増やしていく前提で、型反映を“仕組み化”する。
- 既存 API の実行時挙動は変えず、スタブ生成（`src/grafix/api/__init__.pyi`）の型だけを正しく追従させる。

## 現状

- stub 生成の型は基本 `ParamMeta.kind -> _type_for_kind()` で決めている。
- `fill` だけは実装が `Sequence` を受けるため、生成側で `_override_type_for_effect_param()` により型を上書きしている。

## 方針（案 A）

effect 実装関数（`grafix.core.effects.<name>.<name>`）の **型アノテーション** を「スタブの型の正」として扱う。

- stub 生成は各 effect の実装関数を import 済み（docstring 用）なので、同じ `impl` から `inspect.signature()` で param の annotation を取得できる。
- `fill` のような「meta は scalar / 実装は Sequence も許可」を、**実装側のシグネチャに書いた瞬間に stub に反映**させる。

## スコープ

In:

- `tools/gen_g_stubs.py` の effect 引数型決定ロジック
- `src/grafix/api/__init__.pyi` の再生成（内容差分が出る場合のみ）
- 同期テスト `tests/stubs/test_api_stub_sync.py` の通過（= 生成結果と一致）

Out:

- `ParamMeta` の拡張（accepts_sequence フラグ等）
- GUI/parameter resolver の挙動変更
- effect 実装の受け入れ仕様そのもの変更（今回は型反映のみ）

## 実装方針（具体）

### 1) 型決定関数を “仕組み化” する

- `effect_name/param_name` を受け、以下の優先順位で stub の型文字列を決める:
  1. `impl` の annotation（文字列）を取得できるならそれ（例: `float | Sequence[float]`）
  2. 取得できない場合は従来どおり `ParamMeta.kind -> _type_for_kind()`

補足:

- `bypass` は meta にはあるが実装シグネチャに無いので、自然に (2) へフォールバックする想定。
- 既存 stub の見た目（`Vec3` など）を維持したい場合は「annotation が meta と等価（例: `tuple[float, float, float]`）なら meta 側表現を優先」するかを決める。

### 2) `fill` の特別扱いを削除する

- `_override_type_for_effect_param()` を削除し、上記の仕組みに一本化する。
- `fill` のシグネチャは既に `float | Sequence[float]` 等になっているため、同等の stub が生成されることが期待。

## 作業チェックリスト

### P0: 生成側の実装

- [ ] `tools/gen_g_stubs.py` に「impl の annotation から stub 型文字列を得る」ヘルパを追加する
  - `inspect.signature(impl).parameters[param].annotation` を使う（`from __future__ import annotations` 前提で文字列が得られる）
  - `inspect._empty` の場合は `None` 扱い
- [ ] `_render_effect_builder_protocol()` / `_render_e_protocol()` の型決定を新ヘルパ経由に置換する（`_override_type_for_effect_param()` 呼び出しを排除）
- [ ] `_override_type_for_effect_param()` を削除する（`fill` 特別扱いの撤去）

### P1: 出力確認と同期

- [ ] `python -m tools.gen_g_stubs` を実行し、`src/grafix/api/__init__.pyi` を更新する
- [ ] 同期テストを実行する: `PYTHONPATH=src pytest -q tests/stubs/test_api_stub_sync.py`

### P2: 仕上げ（最低限）

- [ ] lint: `ruff check tools/gen_g_stubs.py`
- [ ] （必要なら）型注釈の運用ルールを `tools/gen_g_stubs.py` 冒頭に 2〜3 行で明記する
  - 例: 「effect の public param は stub で解決可能な型だけを書く（`Sequence`/builtins など）」。

## Done（受け入れ条件）

- [ ] `fill` を含め、effect の `Sequence` 受け入れが stub に自動反映される（`fill` の特別扱いなし）
- [ ] `PYTHONPATH=src pytest -q tests/stubs/test_api_stub_sync.py` が通る
- [ ] `ruff check tools/gen_g_stubs.py` が通る

## リスク / 考慮点

- effect 側アノテーションに “stub 側で import していない名前” が入ると、生成 stub の名前解決が壊れる可能性がある。
  - 対策案: 「effect の型アノテーションは `builtins + Sequence + tuple[...]` などに寄せる」運用に寄せる（まずはこれで十分）。
- meta と実装シグネチャが食い違う場合の扱い（どちらを正とするか）を決める必要がある。

## 事前確認したいこと（あなたに質問）

- [ ] stub の `Vec3` 表現は維持したい？（= annotation が `tuple[float, float, float]` でも meta 由来の `Vec3` を優先する）；はい
- [ ] 「annotation に未知の型名が出たらどうするか」は運用で縛る方針で OK？（まずは自動 import 収集はしない）；はい
