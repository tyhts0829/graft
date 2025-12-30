# `@component`（公開引数だけ GUI + 内部自動 mute）チェックリスト（2025-12-29）

## ゴール

- `logo()` のような“コンポジット形状関数”を他スケッチから再利用できる。
- `logo()` を呼んでも **内部の G/E パラメータは GUI に出さず**、`logo` の公開引数だけが GUI に出る。
- 1 つのスケッチ内で同一コンポーネントを複数回呼んでも **衝突せず**、GUI 上で区別できる。
- （別計画）GUI で調整した値をスニペット出力して貼れるようにする（本ファイルの範囲外）。

## 非ゴール

- 既存の `@primitive`（RealizedGeometry を生成する低レベル primitive）へコンポーネントを統合する。
- スケッチの `.py` を自動編集する。
- 内部パラメータ（G/E の細部）を将来 GUI で調整する用途をサポートする（必要なら別途）。

## 方針（ユーザー API）

### 1) `@component` デコレータ（公開引数だけ GUI）

- 新 API: `grafix.component` デコレータを追加。
- `@component(meta=..., op=...)` を付けた関数は「コンポーネント」として振る舞う:
  - **公開パラメータ**: `meta` に含まれる引数だけ（= GUI に出るのはこれだけ）。
  - **内部**: 関数本体内の `G.*` / `E.*` は自動的に mute され、GUI/永続化に出ない。
- `meta` は明示指定（推奨）。GUI の ui_min/ui_max をここで決められる。
- 複数回呼び出し（同一行/ループ）を分離するために `key=` を予約引数として用意する（GUI には出さない）。
- GUI ヘッダ表示名として `name=` を予約引数として用意する（GUI には出さない）。

（利用例）

```py
from grafix import E, G, component
from grafix.core.parameters.meta import ParamMeta

logo_meta = {
    "center": ParamMeta(kind="vec3", ui_min=0.0, ui_max=100.0),
    "scale": ParamMeta(kind="float", ui_min=0.0, ui_max=4.0),
}

@component(meta=logo_meta)
def logo(*, center=(0, 0, 0), scale=1.0, name=None, key=None):
    square = G.polygon(...)
    ...
    return E.affine(delta=center, scale=(scale, scale, scale))(square + ...)
```

### 2) mute は `@component` に内包（内部を観測しない）

- `@component` が関数本体の実行を自動で mute する。
- mute 中に抑止するもの:
  - `resolve_params()` の record 追加（= GUI/永続化に出ない）
  - `G(name=...)` / `E(name=...)` / `L(name=...)` の label 保存（= label だけが JSON に残る事故を防ぐ）

## 実装チェックリスト

### A. 仕様確定（最初に決める）

- [x] component の `op` 命名規則を決める（`component.<func_name>`）
- [x] `name=` と `key=` を予約引数として標準化する（GUI 非公開）
- [x] `key=` の型/文字列化を決める（`str|int|None` / site_id は `base|key`）
- [ ] GUI 上の見え方を決める（Component 専用セクションを作るか）

### B. パラメータ観測の Mute（基盤）

- [x] `src/grafix/core/parameters/context.py` に “観測有効フラグ” の contextvar を追加
- [x] `current_param_recording_enabled()` と `parameter_recording_muted()` を追加
- [x] `src/grafix/api/_param_resolution.py` で以下をフラグに従って抑止
  - [x] `set_api_label()`（label 保存）
  - [x] `resolve_api_params()`（resolve_params 呼び出し）
- [x] 既存挙動の互換性を確認（既存テストで担保）

### C. `@component` デコレータ（コンポーネント化）

- [x] `src/grafix/api/component.py` を追加し、`component` デコレータを実装する
- [x] `op` は既定で `component.<func_name>` とし、任意で上書きできるようにする
- [x] `site_id` を安定生成（`caller_site_id()` + `key` を合成）
- [x] `meta` に含まれる引数だけ `resolve_params(op=..., site_id=...)` で解決・record する
- [x] `name=` を ParamStore label として保存（このラベルは mute の外で設定する）
- [x] 関数本体は自動で mute して実行する（内部 G/E の record/label を抑止）
- [x] `grafix/__init__.py` から `component` を export（公開 API）

### D. GUI 表示（必要なら）

- [x] Component を GUI で “まとまり” として見せる（ヘッダ表示/折りたたみ）
  - [x] `component.*` を primitive 同等にヘッダ表示できるようにする（label をヘッダに使う）
  - [x] `_order_rows_for_display()` の分類に component を追加（style→component→primitive→effect→other）
  - [x] 既存 primitive/effect の並び順が崩れないことを確認（既存テストで担保）

### F. テスト

- [x] Mute 中は record/label が保存されないこと（内部 primitive/effect が出ない/label が残らない）
- [x] Component が公開引数だけを record すること（内部 G/E が出ない）
- [x] `key=` で同一行の複数インスタンスが分離できること

### G. ドキュメント

- [ ] `README.md` or `docs/` に “コンポーネントの作り方” を追記
- [ ] `sketch/readme2.py` を例として更新（logo を `@component` に切り替える）

## 仕様確定（確定済み）

- component の `op`: `component.<func_name>`
- `name=` / `key=`: component 関数の予約引数（GUI 非公開）
- `meta`: 当面は明示指定必須

## 関連

- Code 出力の独立計画: `param_snippet_checklist_2025-12-29.md`
