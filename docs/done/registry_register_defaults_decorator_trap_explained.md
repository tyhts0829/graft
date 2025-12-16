# どこで: `docs/memo/registry_register_defaults_decorator_trap_explained.md`。
# 何を: `PrimitiveRegistry.register()` / `EffectRegistry.register()` の「デコレータとして使った場合」に `defaults` が保存されない挙動を、背景・影響・再現例つきで解説する。
# なぜ: `defaults` は Parameter GUI の「引数省略でも行を出す」機能の要であり、登録 API の罠を理解しないとユーザー定義 primitive/effect が静かに不便になるため。

# `register(..., defaults=...)` をデコレータとして使うと `defaults` が捨てられる、とは何か

## 更新（このメモの現状）

- 2025-12-15 時点で、登録経路を `@primitive` / `@effect` に一本化し、`PrimitiveRegistry.register()` / `EffectRegistry.register()` の公開・デコレータ経路は廃止した。
- そのため **この「defaults が捨てられる罠」は構造的に発生しない**（参考: `docs/memo/decorator_only_registration_refactor_plan.md`）。

## 結論（いちばん短い説明）

`PrimitiveRegistry.register()` と `EffectRegistry.register()` は「関数として呼んでもいいし、デコレータとしても使える」という二刀流の形になっているが、**デコレータとして使った場合だけ `defaults` 引数が “次の register 呼び出し” に引き継がれていない**。

そのため、

- `register(name, func=..., defaults=...)`（関数として登録）では `defaults` が保存されるが
- `@register(name, defaults=...)`（デコレータとして登録）では `defaults` が保存されない

という **API の不一致** が起きる。

---

## まず前提: register を「関数」と「デコレータ」で使う 2 パターン

Python ではよく次のように「同じ `register()` を 2 通りで使える」API を作ることがある。

### パターンA: 関数として登録（`func` を直接渡す）

```python
registry.register("op_name", func, meta=..., defaults=...)
```

この形は `register()` が “今この場で登録する”。

### パターンB: デコレータとして登録（`func` を後から受け取る）

```python
@registry.register("op_name", meta=..., defaults=...)
def func(...):
    ...
```

この形は `register()` がいったん「decorator 関数」を返し、その decorator が `func` を受け取ったタイミングで “登録する”。

一般に、パターンBは内部的に

```python
def register(..., func=None, **opts):
    if func is None:
        def decorator(f):
            return register(..., func=f, **opts)
        return decorator
    ...
```

のように **「受け取ったオプション（opts）を次の `register()` 呼び出しにそのまま渡す」** ことで、パターンAとBの挙動を一致させるのが定石。

---

## このリポで実際に起きていること（コード上の根本原因）

### Primitive 側（`src/core/primitive_registry.py`）

- 該当箇所: `PrimitiveRegistry.register()` の `func is None` 分岐
- 具体的には、内部 decorator が `self.register(...)` を呼ぶときに **`defaults=defaults` を渡していない**

（行番号は手元のスナップショットでは `src/core/primitive_registry.py:58-64` 付近）

### Effect 側（`src/core/effect_registry.py`）

Primitive と同じ問題が、Effect 側にもある。

（行番号は手元のスナップショットでは `src/core/effect_registry.py:61-67` 付近）

---

## 「defaults が捨てられる」とは、具体的に何が起きるのか

`register()` の内部は最終的に

- `self._items[name] = func`
- `self._meta[name] = meta`（meta があれば）
- `self._defaults[name] = defaults`（defaults があれば）

という形で保持する。

ところがデコレータ経由だと、

1. 1回目の `register(name, func=None, defaults=...)` は「decorator 関数」を返して終わる
2. decorator が `func` を受け取ったとき、**2回目の `register(name, func, ...)` が呼ばれる**
3. その 2回目の呼び出しに **defaults が渡っていない** ので、`defaults is not None` が成立せず `self._defaults[name]` が埋まらない

という流れになる。

結果として `registry.get_defaults(name)` が `{}` を返し、`defaults` を期待していた機能が動かない。

---

## 最小の再現例（「期待」と「実際」）

### PrimitiveRegistry の例

「デコレータとして register したい」ユーザーが、こう書くのは自然:

```python
from src.core.primitive_registry import PrimitiveRegistry

r = PrimitiveRegistry()

@r.register("myprim", defaults={"x": 1.0})
def _impl(args):
    ...
```

期待するのは `r.get_defaults("myprim") == {"x": 1.0}` だが、現状は `defaults` が保存されないため `{}` になる。

EffectRegistry も同様。

---

## 何が困る？（この repo での `defaults` の役割）

このリポでは `defaults` が、Parameter GUI の “引数省略でも行が出る” 挙動に直結している。

### どこで使っているか（API 層）

- `src/api/primitives.py` は `primitive_registry.get_defaults(op)` を取り出して、ユーザーが省略した kwargs を補完する。
  - `base_params = dict(defaults); base_params.update(params)` の形
- `src/api/effects.py` も `effect_registry.get_defaults(op)` を使って同様の補完を行う。

この補完により、

- `G.circle()` のように kwargs が空でも `r/cx/cy/segments` などが “観測” される
- `G.circle(r=2.0)` のように一部だけ指定しても、未指定の引数も GUI 上に出る

が実現できている（背景は `docs/done/parameter_gui_no_args_defaults_checklist.md` に整理されている）。

### `defaults` が無いとどうなるか（症状）

`defaults` が保存されていない op を `G/E` 経由で使うと、典型的に次が起きる:

- **省略した引数が GUI に出ない**
  - 例: `G.myprim()` で GUI が空、または `G.myprim(x=...)` で `x` しか出ない
- 省略引数に対する「初期 override ポリシー（省略は override=True）」も働きにくい
  - そもそも “省略した引数が観測されない” ので、ポリシーを適用する対象が存在しない

つまり「動くけど UI が貧弱になる/気づきにくい不具合」になりやすい。

---

## なぜ「直ちに壊れていない」のか（今回の指摘の注釈の意味）

この repo の組み込み primitive/effect は、基本的に次の “別経路” で登録している:

- `@primitive(meta=...)`（`src/core/primitive_registry.py` 内の関数デコレータ）
- `@effect(meta=...)`（`src/core/effect_registry.py` 内の関数デコレータ）

この `@primitive` / `@effect` は

1. 関数のシグネチャから **meta 対象引数の default 値を抽出**
2. wrapper を作り
3. `primitive_registry.register(f.__name__, wrapper, meta=..., defaults=...)` のように **func を直接渡す形（パターンA）で register する**

ので、今回の “デコレータとしての register 経路（パターンB）” を踏まない。

そのため、組み込み primitive/effect では問題が顕在化していない。

補足:
- `tests/parameters/test_defaults_autopopulate.py` も `@primitive` / `@effect` を使っているため、この罠を踏むケースはテストに含まれていない。

---

## なぜ「罠」なのか（API と期待のズレ）

`PrimitiveRegistry.register()` / `EffectRegistry.register()` の docstring には

- 「関数またはデコレータとして使用可能」

と書かれているため、ユーザーは「同じ引数を渡せば同じように登録される」と期待する。

しかし現状は **デコレータ形式にした瞬間だけ `defaults` が消える**。

この種の不一致は、次の理由で罠になりやすい:

- `defaults` が無くても基本動作は “一応” 成立し、すぐには壊れない
- 影響が GUI の使い勝手（表示される行）として出るため、原因に辿り着きにくい
- 組み込みが正常なので「自分の登録の仕方が悪いのか？」となりやすい

---

## どう直すのが筋か（改善案：実装は別タスク）

### 改善案A: decorator 経路で `defaults` を引き継ぐ（最小修正）

`func is None` 分岐の decorator 内で

- `self.register(name, f, overwrite=overwrite, meta=meta, defaults=defaults)`

のように **defaults をそのまま渡す**。

これでパターンA/Bの挙動が一致する。

### 改善案B: `register(..., defaults=...)` から decorator 形態を外す（割り切り）

「register は関数登録のみ」として、デコレータ形態は `@primitive` / `@effect` に一本化する。

ただしこの場合は docstring と API 契約の整理が必要。

---

## まとめ

- `defaults` はこの repo の Parameter GUI において “省略引数も行を出す” ための重要情報。
- `register()` をデコレータとして使う経路だけ `defaults` が引き継がれておらず、保存されない。
- 組み込みは別の `@primitive/@effect` 経路を使うため今は壊れていないが、API としては不一致で “罠” になり得る。
