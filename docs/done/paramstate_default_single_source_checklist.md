# どこで: `docs/paramstate_default_single_source_checklist.md`。
# 何を: `override` の初期値を「引数が明示かどうか」で決め、かつ実装を単一の規範へ寄せる改善計画。
# なぜ: 既定値の更新漏れリスクを下げつつ、「明示的に渡した値はコード優先」「省略した値は GUI 優先」を両立するため。

## 背景（現状）

- `override` の既定（False）が以下のように複数箇所に現れている。
  - データモデルの既定: `src/parameters/state.py`
  - 生成側での明示: `src/parameters/store.py`（`ensure_state`）
  - 生成側での明示: `src/parameters/resolver.py`（snapshot に無い場合のフォールバック state）
- 現状は値が一致しているため動作は問題ないが、既定値を変更したくなった際に修正箇所が増える。
- さらに、現状は「引数を省略した場合」でも override が False で初期化されるため、GUI 値が反映されるには手動で override を ON にする必要がある。

## ゴール

- draw 関数内でユーザーが **明示的に引数を渡したパラメータ**は `override=False`（コード優先）で初期化する。
- draw 関数内でユーザーが **省略したパラメータ（自動補完された default）**は `override=True`（GUI 優先）で初期化する。
- この初期化ポリシーの実装は **1 箇所**に集約する（更新漏れを起こさない）。
- 既存 state がある場合は、ユーザー操作を尊重して勝手に override を反転させない（初回生成時のみ適用）。

## 方針（採用）

- 規範（単一の真実）は「override 初期化ポリシー関数」とし、`ParamStore` 側に集約する（呼び出し側に散らさない）。
- 具体案:
  - `FrameParamRecord` に「その引数がユーザーから明示的に渡されたか」を持たせる（例: `explicit: bool`）。
  - `resolve_params()` は `explicit_args`（ユーザーが渡した kwargs のキー集合）を受け取り、各 record に `explicit` を記録する。
  - `ParamStore.store_frame_params()` は「新規 state のときだけ」`explicit` に応じて override を初期化する。
    - explicit=True（明示）→ override=False
    - explicit=False（省略/補完）→ override=True

## 注意（既知のトレードオフ）

- 省略引数は override=ON で初期化されるため、`angle=t` のような「毎フレーム変わる base」を省略すると固定化しやすい。
  - ただし、この場合はそもそも「明示的に渡す」運用が自然なので、ポリシーで回避できる（明示なら override=OFF）。

## チェックリスト

- [x] 影響範囲の棚卸し
  - [x] `ParamState(` / `override=` / `FrameParamRecord(` の利用箇所を検索し、どこが初期化ポリシーに関与するか分類する
- [x] record に「明示/省略」情報を載せる
  - [x] `src/parameters/frame_params.py` の `FrameParamRecord` に `explicit: bool` を追加
  - [x] `src/parameters/frame_params.py` の `FrameParamsBuffer.record()` に `explicit` を追加
- [x] `resolve_params` が明示/省略を判定して記録する
  - [x] `src/parameters/resolver.py` の `resolve_params(..., explicit_args=...)` を追加（省略時は「全て明示」とみなして後方互換の挙動に寄せる）
  - [x] API 層（`src/api/primitives.py` / `src/api/effects.py`）から `explicit_args=set(params.keys())` を渡す
- [x] ParamStore 側で初期 override を 1 箇所で決める
  - [x] `src/parameters/store.py` の `ensure_state` に「初期 override（任意）」を受け取れる形を追加（例: `initial_override: bool | None`）
  - [x] `src/parameters/store.py` の `store_frame_params` で「新規 state のときだけ」`explicit` に応じて `initial_override` を指定する
  - [x] `src/parameters/store.py` の `ensure_state` / `src/parameters/resolver.py` のフォールバックで `override=False` を直書きしない（ポリシーへ集約）
- [x] JSON（任意）
  - [x] `from_json` は保存値を優先し、`override` 欠落時は `ParamState` の既定に従う（hardcode しない）
- [x] テスト（最小）
  - [x] `pytest -q tests/parameters` が通ることを確認する
  - [x] 新規テスト `tests/parameters/test_default_override_policy.py`（新規）
    - [x] `G.circle()`（省略）で `r/cx/cy/segments` の override が True になる
    - [x] `G.circle(cx=1.0)`（明示）で `cx` だけ override が False、他は True になる
    - [x] 既存 state がある場合は勝手に反転しない（例: 一度 GUI で切替後、次フレームも維持）
- [x] ドキュメント
  - [x] 近接ドキュメントに「明示/省略で override 初期値が変わる」ことを短く明記する（冗長なら省略）

## 完了定義

- 「明示/省略」の初期 override ポリシーを変更したくなった場合、修正箇所が 1 箇所で済む状態になっている。
- `tests/parameters` が green。
