# どこで: `docs/memo/decorator_only_registration_refactor_plan.md`。
# 何を: primitive/effect の登録方法を `@primitive` / `@effect`（`from api import ...`）に一本化するための実装改善計画をまとめる。
# なぜ: 登録 API を最小化して理解コストと罠（defaults が捨てられる等）を無くし、内部実装も単純化するため。

# decorator 登録のみ（`@primitive` / `@effect`）に一本化する改善計画

## 目的（ゴール）

- ユーザーが **次の形だけ**で primitive/effect を登録できるようにする。

```python
from api import primitive, effect

@primitive
def my_prim(...):
    ...

@effect
def my_eff(...):
    ...
```

- 上記以外の登録方法（`PrimitiveRegistry.register(...)` / `EffectRegistry.register(...)` / `register_primitive` 等）を削除し、**公開 API と実装の両方を簡素化**する。
- 既存の “罠” を根絶する:
  - `register(..., defaults=...)` をデコレータとして使うと `defaults` が引き継がれず捨てられる問題（詳細は `docs/memo/registry_register_defaults_decorator_trap_explained.md`）。

## 非ゴール

- 互換ラッパー/シムの提供（古い登録方法を残す等）はしない。
- パッケージング（`api` と `src.api` の二重 import 問題）の全面解消は別トピック（ただし影響はメモする）。

## 決定事項（この前提で進める）

### 登録経路

- **ユーザー向け/内部向けともに** 登録は `@primitive` / `@effect` のみ。
  - registry のメソッドや `register_*`/`get_*` のような関数経由では登録しない（実装から削除する）。

### meta の扱い（仕様）

- **組み込み primitive/effect（`src/primitives/*`, `src/effects/*`）は meta 必須**。
  - `@primitive` / `@effect` の登録時点で `meta is None` を検出したら例外にする（「書き忘れ」を即時に落とす）。
- **ユーザー定義は meta 任意**。
  - `meta` を与えない場合、その op は **Parameter GUI に表示しない**（= GUI/CC/override の対象外）。
  - `meta` を与えた場合のみ GUI 管理対象（表示/override/CC/既定引数の自動観測）になる。

### meta 推定の扱い（簡素化）

- `infer_meta_from_value()` による **meta 推定は廃止**する（= “meta が無いなら GUI へは出さない” を徹底）。

## デメリット / トレードオフ（想定される不利益）

- **動的登録がやりにくい**: 実行時に `if/for` で選んだ関数を “名前付き op として” 登録する用途は弱くなる（デコレータ前提）。
- **op 名の自由度が下がる**: 基本は `__name__` 固定になるため、別名で登録したい場合は「関数名を変える」以外の手段が減る（`name=` を足すと API が増えるので今回はやらない想定）。
- **グローバルレジストリの汚染が起きやすい**: テスト/REPL で一時 op を量産すると、同名衝突や残骸が起きる。必要なら “レジストリ初期化/クリア” の仕組みが別途要る。
- **ユーザー meta 省略は GUI 非対応**: 省略時に `infer_meta_from_value` で “それっぽく GUI を出す” ことはしない（仕様）。

## 現状整理（なぜやる必要があるか）

### 現状の登録経路が複数ある

- コアのデコレータ: `src/core/primitive_registry.py:primitive` / `src/core/effect_registry.py:effect`
- registry のメソッド: `PrimitiveRegistry.register()` / `EffectRegistry.register()`
  - しかも `func=None` のとき decorator を返す “二刀流”
- 追加のヘルパ: `register_primitive` / `get_primitive` / `register_effect` / `get_effect`

このうちユーザー向けに残したいのは `@primitive` / `@effect` のみ、という方針にする。

### 罠（defaults が捨てられる）

`*.register(name, func=None, defaults=...)` の “decorator 経路” が `defaults` を次段へ渡していないため、
デコレータとして使うと `defaults` が保存されない（= `get_defaults()` が空になる）。

組み込みは `@primitive(meta=...)` / `@effect(meta=...)` が直接 `register(name, func, defaults=...)` を呼ぶため顕在化しにくいが、
API としては不一致で “罠” になる。

## 目指す公開 API（確定案）

### 1) “ユーザーが触る” 登録 API は `api.primitive` / `api.effect` のみ

- `from api import primitive, effect` を正式な導線にする
- op 名は原則 **関数名**（`f.__name__`）
- meta を付けたい場合は同じデコレータにオプションとして渡す（登録方法は増やさない）
  - `@primitive(meta={...})`（組み込みは必須、ユーザーは任意）
  - `@effect(meta={...})`（同上）

## 方針（実装をどう簡素化するか）

### 採用案（decorator-only を徹底）

- `PrimitiveRegistry.register()` / `EffectRegistry.register()` を削除する（= “直接登録” の入り口を閉じる）。
  - デコレータ `primitive/effect` の内部からだけ登録できるようにし、API の不一致を根絶する。
  - これにより `defaults` の “decorator 経路で捨てられる” 問題は構造的に消える（その経路自体が無くなる）。
- `register_primitive/get_primitive/register_effect/get_effect` を削除する。
- `src/api/__init__.py` で `primitive/effect` を re-export して “ユーザー導線” を固定する。
- Parameter GUI に関する仕様を単純化する:
  - `meta` が無い op は resolver/FrameParamsBuffer に載せない（= GUI 非表示）。
  - `meta` がある op のみ、既存どおり `defaults` を使って “省略引数の観測” を行う。

## 変更範囲（予定）

### 実装

- `src/api/__init__.py`
  - `primitive` / `effect` を export（`__all__` 更新）
- `src/core/primitive_registry.py`
  - `PrimitiveRegistry.register()` 自体の削除（直接登録 API を廃止）
  - `register_primitive` / `get_primitive` の削除
  - docstring/コメントの整理（「register をデコレータとして使える」等の文言を削除）
  - 組み込み（`src.primitives.*`）は `meta is None` を例外にするチェックを追加
- `src/core/effect_registry.py`
  - 上と同様
- `src/api/primitives.py` / `src/api/effects.py`
  - op に `meta` が無い場合は `resolve_params()` を使わず、ParamStore を観測しない（= GUI 非表示仕様）
- `src/parameters/resolver.py` / `src/parameters/meta.py` / `src/parameters/__init__.py`
  - `infer_meta_from_value()` とその利用を削除（meta 推定の廃止）

### テスト

最小で以下を追加/更新:

- 「ユーザー導線」を保証するテスト
  - `from api import primitive, effect` が動くこと
  - ただし pytest 実行時は通常 `api` が import できない（`src` を `sys.path` に入れていない）ため、
    テスト内で `sys.path` を一時追加して import を確認する（ユーザー導線を固定したいのでこちらを採用する）。
- 「meta 無しユーザー定義は GUI 非表示」のテスト
  - `@primitive` / `@effect` で meta を渡さず登録 → `parameter_context` 下で呼んでも `store.snapshot()` に行が増えないこと

## 実装チェックリスト（このファイルは計画用）

- [x] `src/api/__init__.py` で `primitive/effect` を re-export（`__all__` 更新）
- [x] `src/core/primitive_registry.py` の `PrimitiveRegistry.register()` を削除（直接登録 API を廃止）
- [x] `src/core/effect_registry.py` も同様に修正
- [x] `src/core/*_registry.py` から `register_*` / `get_*` の未使用ヘルパを削除
- [x] 組み込み primitive/effect で meta 無し登録を例外にする（`__module__` 等で判定）
- [x] `src/api/primitives.py` / `src/api/effects.py` を “meta がある op のみ GUI 観測する” 仕様へ変更
- [x] `infer_meta_from_value()` を削除し、resolver 側の meta 推定分岐も削除
- [ ] ドキュメント更新
  - [x] `docs/memo/registry_register_defaults_decorator_trap_explained.md` に「この refactor で罠を消す」旨を追記（任意）
  - [x] `architecture.md` の `infer_meta_from_value` 記述を更新
  - [x] `README.md` に「ユーザー定義は `from api import primitive/effect`」の例を追記
- [x] テストを追加/更新
  - [x] `from api import primitive/effect` を確認するテスト（テスト内で一時 `sys.path` 追加）
  - [x] meta 無しユーザー定義が GUI 非表示であることを確認するテスト
  - [x] 既存テストが green であることを確認
