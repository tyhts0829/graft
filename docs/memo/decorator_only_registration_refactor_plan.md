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

- 上記以外の登録方法（`PrimitiveRegistry.register(...)` / `EffectRegistry.register(...)` / `register_primitive` 等）を削除 or 内部専用化し、**公開 API と実装の両方を簡素化**する。
- 既存の “罠” を根絶する:
  - `register(..., defaults=...)` をデコレータとして使うと `defaults` が引き継がれず捨てられる問題（詳細は `docs/memo/registry_register_defaults_decorator_trap_explained.md`）。

## 非ゴール

- 互換ラッパー/シムの提供（古い登録方法を残す等）はしない。
- パッケージング（`api` と `src.api` の二重 import 問題）の全面解消は別トピック（ただし影響はメモする）。

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
- 既存どおり meta も同じデコレータで任意指定可（これは「登録方法」は増えない/デコレータ1本のまま）
  - `@primitive(meta={...})`
  - `@effect(meta={...})`

### 2) registry の “直接 register” は外へ出さない

実装上は「辞書へ登録する処理」は必要なので残すが、公開 API としては見えない形へ寄せる。

## 方針（実装をどう簡素化するか）

### A案（最小変更で罠を消す / 推奨）

- `PrimitiveRegistry.register()` / `EffectRegistry.register()` から **decorator 機能（`func=None`）を削除**し、
  “関数としての登録” のみに限定する（= `func` 必須にする、または `_register()` として private 化）。
  - これにより `defaults` の取り回し不備が原理的に消える（decorator 経路が存在しない）。
- `register_primitive/get_primitive/register_effect/get_effect` は未使用なので削除。
- ユーザー向け導線として `src/api/__init__.py` から `primitive/effect` を re-export する。

### B案（もっと削る / 追加検討）

- `PrimitiveRegistry/EffectRegistry` クラス自体をやめ、モジュール内の `dict` + 関数に置き換える。
  - 利点: さらに単純（属性が見える/隠れるが明確）
  - 欠点: 参照箇所（`primitive_registry.get_meta()` 等）に影響が出て変更範囲が広がる

まずは A案で十分シンプルにできる見込み。

## 変更範囲（予定）

### 実装

- `src/api/__init__.py`
  - `primitive` / `effect` を export（`__all__` 更新）
- `src/core/primitive_registry.py`
  - `PrimitiveRegistry.register()` の decorator 機能削除（`func=None` 分岐を削る）
  - `register_primitive` / `get_primitive` の削除
  - docstring/コメントの整理（「register をデコレータとして使える」等の文言を削除）
- `src/core/effect_registry.py`
  - 上と同様

### テスト

最小で以下を追加/更新（方針確認後に確定）:

- 「ユーザー導線」を保証するテスト
  - `from api import primitive, effect` が動くこと
  - ただし pytest 実行時は通常 `api` が import できない（`src` を `sys.path` に入れていない）ため、
    テスト内で `sys.path` を一時追加するか、`src.api` 経由を許すかを決める必要がある

## 事前に確認したいこと（質問）

1) `api` import について
   - pytest の世界では `from api import ...` はそのままだと失敗する（`src` ディレクトリが `sys.path` に無い）前提。
   - ここは:
     - (a) テスト内で `sys.path` を一時追加して “ユーザー導線そのもの” をテストする
     - (b) “ユーザー導線は main.py のような `sys.path.append(\"src\")` が前提” と割り切り、テストは `from src.api import ...` に留める
   - どちらで行くのが好み？

2) meta を省略した `@primitive` / `@effect` の扱い（GUI まで期待するか）
   - 現状は meta が無いと `defaults` が保存されず、`G/E` 側の “引数省略の補完” が効かないため GUI 行が出にくい。
   - ここは:
     - (a) **meta 無し登録は「登録だけできる（GUI 省略引数は出ない）」** と割り切る（最も単純）
     - (b) meta が無くても、関数シグネチャから “安全な default” を抽出して `defaults` として保存し、GUI を出せるようにする（便利だが仕様が増える）
   - どちらに寄せる？

## 実装チェックリスト（このファイルは計画用）

- [ ] `src/api/__init__.py` で `primitive/effect` を re-export（`__all__` 更新）
- [ ] `src/core/primitive_registry.py` の `PrimitiveRegistry.register()` から decorator 機能を削除（`func` 必須化 or private 化）
- [ ] `src/core/effect_registry.py` も同様に修正
- [ ] `src/core/*_registry.py` から `register_*` / `get_*` の未使用ヘルパを削除
- [ ] ドキュメント更新
  - [ ] `docs/memo/registry_register_defaults_decorator_trap_explained.md` に「この refactor で罠を消す」旨を追記（任意）
  - [ ] README or spec に「ユーザー定義は `from api import primitive/effect`」の例を追記（必要なら）
- [ ] テスト方針確定（上の質問 1/2 の回答に従う）
  - [ ] 必要なら `api` import 導線のテストを追加
  - [ ] 既存テストが green であることを確認

