# Component Params（公開引数だけGUI）+ Mute（内部を観測しない）+ Snippet 出力 チェックリスト（2025-12-29）

## ゴール

- `logo()` のような“コンポジット形状関数”を他スケッチから再利用できる。
- `logo()` を呼んでも **内部の G/E パラメータは GUI に出さず**、`logo` の公開引数だけが GUI に出る。
- 1つのスケッチ内で同一コンポーネントを複数回呼んでも **衝突せず**、GUI 上で区別できる。
- GUI で調整した値を **スニペットとして出力**でき、永続化（ParamStore）に依存しない形でコードへ貼り付けられる。

## 非ゴール

- 既存の `@primitive`（RealizedGeometry を生成する低レベル primitive）へコンポーネントを統合する。
- スケッチの `.py` を自動編集する（本件は「スニペット出力」まで）。
- 内部パラメータ（G/E の細部）を将来 GUI で調整する用途をサポートする（必要なら別途）。

## 仕様案（ユーザーAPI）

### 1) Component の公開パラメータ解決

- 新API: `grafix.P`（仮）または `grafix.params` を追加。
  - `P.group(op_name, *, name=None, key=None, meta=..., defaults=..., user_params=...) -> dict`
  - `key` は「同じ呼び出し行から複数回生成する」用途の衝突回避キー（React の key 的なもの）。
  - `name` は GUI ヘッダ表示名（任意）。無ければ `op_name` を表示。

（利用例）

```py
from grafix import E, G, P

logo_meta = {
    "center": P.meta.vec3(ui_min=0, ui_max=100),
    "scale":  P.meta.float(ui_min=0, ui_max=4),
}

def logo(*, center=(0, 0, 0), scale=1.0, name=None, key=None):
    p = P.group(
        "logo",
        name=name,
        key=key,
        meta=logo_meta,
        defaults={"center": (0, 0, 0), "scale": 1.0},
        user_params={"center": center, "scale": scale},
    )
    with P.mute():
        ...  # 内部の G/E はここで組む（GUI に出ない）
    return E.affine(delta=p["center"], scale=(p["scale"],) * 3)(...)
```

### 2) Mute（内部を観測しない）

- 新API: `P.mute()`（context manager）
- `with P.mute():` の間は以下を抑止する:
  - `resolve_params()` の record 追加（= GUI/永続化に出ない）
  - `G(name=...)` / `E(name=...)` の label 保存（= label だけが JSON に残る事故を防ぐ）

### 3) Snippet 出力（困りごと2）

- 新API（最小）:
  - `P.snippet_from_store(store_or_path, *, op=None, name=None, only_overrides=True) -> str`
  - 既定は「override=True の項目だけ」を Python kwargs 形式で出力（安全に貼れる）。
- 追加オプション（必要なら）:
  - クリップボード連携（依存追加なしならやらない）
  - `op` 指定なしの場合は “全グループ” をまとめて出力

（出力例）

```py
# from {output_root}/param_store/readme2.json
logo__A = dict(center=(50.0, 50.0, 0.0), scale=1.25)
logo__B = dict(center=(20.0, 50.0, 0.0), scale=0.8)
```

## 実装チェックリスト

### A. 仕様確定（最初に決める）

- [ ] component の `op` 命名規則を決める（例: `"component.logo"` / `"logo"`）
- [ ] GUI 上の見え方を決める（Component を Primitive と同列に見せる or “Other” 扱い）
- [ ] 複数呼び出しの識別子を決める（`key=` の型と string 化ルール）
- [ ] Snippet の “グループ選択” 方法を決める（`name=` 優先 / `op+ordinal` / 全出力）

### B. パラメータ観測の Mute（基盤）

- [ ] `src/grafix/core/parameters/context.py` に “観測有効フラグ” の contextvar を追加
- [ ] `current_param_recording_enabled()` と `parameter_recording_muted()`（仮）を追加
- [ ] `src/grafix/api/_param_resolution.py` で以下をフラグに従って抑止
  - [ ] `set_api_label()`（label 保存）
  - [ ] `resolve_api_params()`（resolve_params 呼び出し）
- [ ] 既存挙動の互換性を確認（mute を使わない限り挙動は同じ）

### C. Component の公開パラメータ解決 API（P.group）

- [ ] `src/grafix/api/params.py`（仮）を追加し、`P` を公開する
- [ ] site_id を安定生成（`caller_site_id()` + `op` + `key` を合成）
- [ ] `resolve_params(op=..., site_id=...)` を呼び、公開引数だけ record する
- [ ] `name=` を ParamStore label として保存（mute 中は抑止）
- [ ] `grafix/__init__.py` から `P` を export（公開 API）

### D. GUI 表示（必要なら）

- [ ] Component を GUI で “まとまり” として見せる（ヘッダ表示/折りたたみ）
  - [ ] `primitive_header_display_names_from_snapshot()` の対象 op 判定を拡張（component を含める）
  - [ ] `_order_rows_for_display()` の分類に component を追加（style→component→primitive→effect→other など）
  - [ ] 既存 primitive/effect の並び順が崩れないことを確認

### E. Snippet 出力（困りごと2）

- [ ] `src/grafix/api/params_snippet.py`（仮）に純粋関数を実装
  - [ ] ParamStore（または JSON path）から group（op, site_id）ごとに抽出
  - [ ] `only_overrides=True` で `state.override==True` のみ対象
  - [ ] ラベル（name）を持つ場合は出力名に反映（例: `logo__Title`）
  - [ ] 値は `repr()` ベースで Python リテラル化（tuple/float/int/str/bool）
- [ ] 入口を1つ追加（どれか）
  - [ ] 方式1: `tools/emit_param_snippet.py`（JSON path を渡して出力）
  - [ ] 方式2: `grafix.P.snippet(...)` としてユーザーが REPL/スケッチから呼べる

### F. テスト

- [ ] Mute 中は record/label が保存されないこと（ParamStore の states/meta/labels が増えない）
- [ ] Component が公開引数だけを record すること（内部 G/E が出ない）
- [ ] `key=` で同一行の複数インスタンスが分離できること
- [ ] Snippet が override=True のみ出力すること（順序は安定）

### G. ドキュメント

- [ ] `README.md` or `docs/` に “コンポーネントの作り方” を追記
- [ ] `sketch/readme2.py` を例として更新（logo を P + mute に切り替える）
- [ ] 既存の parameter_persistence の説明に「スニペットで焼き込み → persistence off」が可能な旨を追記

## 事前に確認したいこと（返答ください）

- 1) Component の `op` 表示は `"logo"` のままが良い？ それとも `"component.logo"` のように明示したい？
- 2) GUI の並びに Component 専用セクションが欲しい？（最初は “Other” 扱いでも良い？）
- 3) Snippet の出力形はどれが好み？
  - a) `dict(...)` 変数
  - b) `logo(center=..., scale=...)` の呼び出し形
  - c) 両方

