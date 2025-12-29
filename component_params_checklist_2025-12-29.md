# `@group`（公開引数だけGUI + 内部自動mute）+ GUIスニペット出力 チェックリスト（2025-12-29）

## ゴール

- `logo()` のような“コンポジット形状関数”を他スケッチから再利用できる。
- `logo()` を呼んでも **内部の G/E パラメータは GUI に出さず**、`logo` の公開引数だけが GUI に出る。
- 1つのスケッチ内で同一コンポーネントを複数回呼んでも **衝突せず**、GUI 上で区別できる。
- GUI で調整した値を **スニペットとして出力**でき、永続化（ParamStore）に依存しない形でコードへ貼り付けられる。

## 非ゴール

- 既存の `@primitive`（RealizedGeometry を生成する低レベル primitive）へコンポーネントを統合する。
- スケッチの `.py` を自動編集する（本件は「GUIでスニペット表示」まで）。
- 内部パラメータ（G/E の細部）を将来 GUI で調整する用途をサポートする（必要なら別途）。

## 方針（ユーザーAPI）

### 1) `@group` デコレータ（公開引数だけGUI）

- 新API: `grafix.group` デコレータを追加。
- `@group(meta=..., op=...)` を付けた関数は「コンポーネント」として振る舞う:
  - **公開パラメータ**: `meta` に含まれる引数だけ（= GUI に出るのはこれだけ）。
  - **内部**: 関数本体内の `G.*` / `E.*` は自動的に mute され、GUI/永続化に出ない。
- `meta` は明示指定（推奨）。GUI の ui_min/ui_max をここで決められる。
- 複数回呼び出し（同一行/ループ）を分離するために `key=` を予約引数として用意する（GUI には出さない）。
- GUI ヘッダ表示名として `name=` を予約引数として用意する（GUI には出さない）。

（利用例）

```py
from grafix import E, G, group
from grafix.core.parameters.meta import ParamMeta

logo_meta = {
    "center": ParamMeta(kind="vec3", ui_min=0.0, ui_max=100.0),
    "scale": ParamMeta(kind="float", ui_min=0.0, ui_max=4.0),
}

@group(meta=logo_meta)
def logo(*, center=(0, 0, 0), scale=1.0, name=None, key=None):
    square = G.polygon(...)
    ...
    return E.affine(delta=center, scale=(scale, scale, scale))(square + ...)
```

### 2) mute は `@group` に内包（内部を観測しない）

- `@group` が関数本体の実行を自動で mute する。
- mute 中に抑止するもの:
  - `resolve_params()` の record 追加（= GUI/永続化に出ない）
  - `G(name=...)` / `E(name=...)` / `L(name=...)` の label 保存（= label だけが JSON に残る事故を防ぐ）

### 3) Snippet 出力（困りごと2）は Parameter GUI のボタンで行う

- スケッチ側のコードは「`@group` を付ける」以外に工夫を入れない。
- Parameter GUI の group ヘッダに `Snippet` ボタンを置く。
- 押すと “貼り付け用の Python 呼び出し形” を即表示する（必要ならコピー）。

（出力例）

```py
logo(center=(50.0, 50.0, 0.0), scale=1.25)
```

## 実装チェックリスト

### A. 仕様確定（最初に決める）

- [ ] component の `op` 命名規則を決める（推奨: `component.<func_name>`）
- [ ] `name=` と `key=` を予約引数として標準化する（GUI 非公開）
- [ ] 複数呼び出しの識別子 `key=` の型と string 化ルールを決める
- [ ] GUI 上の見え方を決める（Component 専用セクションを作るか）
- [ ] Snippet の採用値を決める（`effective` で出す / `override=True` のみで出す）

### B. パラメータ観測の Mute（基盤）

- [ ] `src/grafix/core/parameters/context.py` に “観測有効フラグ” の contextvar を追加
- [ ] `current_param_recording_enabled()` と `parameter_recording_muted()`（仮）を追加
- [ ] `src/grafix/api/_param_resolution.py` で以下をフラグに従って抑止
  - [ ] `set_api_label()`（label 保存）
  - [ ] `resolve_api_params()`（resolve_params 呼び出し）
- [ ] 既存挙動の互換性を確認（mute を使わない限り挙動は同じ）

### C. `@group` デコレータ（コンポーネント化）

- [ ] `src/grafix/api/group.py`（仮）を追加し、`group` デコレータを実装する
- [ ] `op` は既定で `component.<func_name>` とし、任意で上書きできるようにする
- [ ] `site_id` を安定生成（`caller_site_id()` + `key` を合成）
- [ ] `meta` に含まれる引数だけ `resolve_params(op=..., site_id=...)` で解決・record する
- [ ] `name=` を ParamStore label として保存（このラベルは mute の外で設定する）
- [ ] 関数本体は自動で mute して実行する（内部 G/E の record/label を抑止）
- [ ] `grafix/__init__.py` から `group` を export（公開 API）

### D. GUI 表示（必要なら）

- [ ] Component を GUI で “まとまり” として見せる（ヘッダ表示/折りたたみ）
  - [ ] `component.*` を primitive 同等にヘッダ表示できるようにする（label をヘッダに使う）
  - [ ] `_order_rows_for_display()` の分類に component を追加（style→component→primitive→effect→other）
  - [ ] 既存 primitive/effect の並び順が崩れないことを確認

### E. GUI Snippet ボタン（困りごと2）

- [ ] Parameter GUI の group ヘッダに `Snippet` ボタンを追加する（component グループのみでOK）
- [ ] Snippet 文字列生成（純粋関数）を追加する
  - [ ] 対象は「component グループ内の行（arg）」のみ
  - [ ] 取得値は `store._runtime_ref().last_effective_by_key` を優先する（GUI/CC/base を反映した“今の値”）
  - [ ] fallback として `state.ui_value` を使えるようにする
  - [ ] 出力は `logo(center=..., scale=...)` の呼び出し形（`component.` プレフィックスは除去）
- [ ] 出力UI（どれか）
  - [ ] 方式1: `input_text_multiline` のポップアップで表示（ユーザーが手でコピー）
  - [ ] 方式2: クリップボードへコピー（imgui のAPIが使える場合のみ）

### F. テスト

- [ ] Mute 中は record/label が保存されないこと（ParamStore の states/meta/labels が増えない）
- [ ] Component が公開引数だけを record すること（内部 G/E が出ない）
- [ ] `key=` で同一行の複数インスタンスが分離できること
- [ ] Snippet が component の引数だけ出力すること（順序は安定）

### G. ドキュメント

- [ ] `README.md` or `docs/` に “コンポーネントの作り方” を追記
- [ ] `sketch/readme2.py` を例として更新（logo を P + mute に切り替える）
- [ ] 既存の parameter_persistence の説明に「スニペットで焼き込み → persistence off」が可能な旨を追記

## 事前に確認したいこと（返答ください）

- 1) component の `op` は `component.<func_name>`（推奨）でOK？
- 2) `name=` と `key=` は “group関数の予約引数” として統一してOK？（GUI には出さない）
- 3) Snippet はどの値で出す？
  - a) `effective`（CC/override/base を反映した“今の値”を丸ごと焼き込み）
  - b) `override=True` の ui_value だけ（GUIで触った分だけ焼き込み）
- 4) Snippet のUIはどれが好み？
  - a) ポップアップ表示（手コピー）
  - b) クリップボードに直接コピー（可能なら）
