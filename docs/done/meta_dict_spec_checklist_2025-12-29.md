# meta を dict で書けるようにする改善計画（2025-12-29）

## 背景

`@component(meta=...)` などで GUI 用メタ情報を定義する際、現状は `ParamMeta` を import する必要がある。
スケッチ側で `grafix.core...` を import するのは公開 API として美しくない。

一方で内部実装としては `ParamMeta`（dataclass）を持っていると、型・可読性・実装の単純さが保てる。

## 目的（ゴール）

- ユーザーコードで `ParamMeta` import が不要（`meta` を dict リテラルで書ける）。
- 内部表現は引き続き `ParamMeta` に統一する（API 入力だけ柔らかくする）。
- `component` のほか `primitive/effect` でも同じ書き味に揃える（任意だが推奨）。

## 非ゴール

- `ParamMeta` 自体を廃止して内部まで dict にする。
- snippet 生成など、meta 以外の機能追加。
- 過度なスキーマ検証（必要十分なバリデーションに留める）。

## 方針（決定）

- 公開 API が受け取る meta の value を「`ParamMeta` または dict（= spec）」にする。
- 入口で dict を `ParamMeta` に変換してから既存処理へ渡す（内部は `ParamMeta` のまま）。
- dict spec の最低要件は `kind` 必須、`ui_min/ui_max/choices` は任意とする。

例（ユーザー側）

```py
meta = {
  "center": {"kind": "vec3", "ui_min": 0.0, "ui_max": 100.0},
  "scale":  {"kind": "float", "ui_min": 0.0, "ui_max": 4.0},
}
```

## チェックリスト

- [x] 1. dict spec から `ParamMeta` へ変換する関数を追加する
  - [x] 置き場所を決める（`src/grafix/core/parameters/meta_spec.py`）
  - [x] `meta_from_spec(spec) -> ParamMeta` を実装する（`kind` 必須）
  - [x] `meta_dict_from_user(meta: ...) -> dict[str, ParamMeta]` のように一括変換を用意する
- [x] 2. `@component` が dict spec を受け付ける
  - [x] `component(meta=...)` の型を `Mapping[str, ParamMeta | Mapping[str, object]]` 相当にする
  - [x] 入口で `dict[str, ParamMeta]` に正規化してから既存ロジックへ渡す
  - [x] `component_registry` へ登録する meta も正規化後のものに統一する
- [x] 3. `@primitive` / `@effect` も同じ入力形式に揃える
  - [x] `primitive(meta=...)` / `effect(meta=...)` の入口で同様に正規化する
  - [x] `choices` を `tuple[str, ...]` へ正規化する（指定時）
- [x] 4. 公開 import を整理する（見た目の改善）
  - [x] 例のスケッチ（`sketch/readme2.py`）を dict spec へ移行し、`grafix.core...` import を削除する
  - [x] `ParamMeta` は再エクスポートしない（方針どおり）
- [x] 5. 型スタブ/テストを更新する
  - [x] stubs: `tests/stubs/test_api_stub_sync.py` が通ることを確認する（`src/grafix/api/__init__.pyi` 変更は不要）
  - [x] `tests/api/test_component.py` に「dict spec を渡しても動く」テストを追加する
  - [x] `primitive/effect` も dict spec で動くテストを追加する（`tests/core/parameters/test_defaults_autopopulate.py`）
- [x] 6. 検証
  - [x] `PYTHONPATH=src pytest -q tests/api/test_component.py`
  - [x] `PYTHONPATH=src pytest -q tests/interactive/parameter_gui`
  - [x] `PYTHONPATH=src pytest -q tests/stubs/test_api_stub_sync.py`

## 事前確認（この計画で進めて良い？）

- [x] Q1. dict spec 対応は `component` だけで良い？それとも `primitive/effect` も揃える？（推奨は揃える）；そろえる
- [x] Q2. dict spec の許容キーは `kind/ui_min/ui_max/choices` のみに絞る？（未知キーはエラーにする/無視する）；はい（未知キーはエラー）
- [x] Q3. `ParamMeta` の再エクスポート（`from grafix import ParamMeta`）も入れる？（dict 派でも補完用に便利）；いいえ
