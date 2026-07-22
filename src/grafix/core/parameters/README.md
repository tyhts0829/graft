<!--
どこで: `src/grafix/core/parameters/README.md`。
何を: `core/parameters` の “1 ファイルだけ読むならこれ” ミニガイド。
なぜ: param 解決（base/GUI/CC）と永続化の流れを最短で把握できるようにするため。
-->

# `grafix.core.parameters` ミニガイド（読む順とデータフロー）

このディレクトリは、`draw(t)` 実行中に「引数の最終値（effective）を決める」ための仕組みと、
その結果を GUI/永続化へ繋ぐためのストアを提供する。

## まず押さえる 5 つ（最短）

1. ストア本体: `src/grafix/core/parameters/store.py`（`ParamStore`）
2. フレーム境界: `src/grafix/core/parameters/context.py`（`parameter_context`）
3. 値解決: `src/grafix/core/parameters/resolver.py`（`resolve_params`）
4. 観測バッファ: `src/grafix/core/parameters/frame_params.py`（`FrameParamsBuffer`）
5. マージ（永続化）: `src/grafix/core/parameters/merge_ops.py`（`merge_frame_params`）

## データフロー（1 フレーム）

流れは次の 1 本だけを覚えればよい:

`store_snapshot -> parameter_context -> resolve_params -> frame_params -> merge`

対応する実体は以下。

### 1) `store_snapshot(store)`（スナップショット固定）

- 実装: `src/grafix/core/parameters/snapshot_ops.py`
- 役割: `ParamStore` から「読み取り専用のスナップショット」を生成する。
- `ParamStore.revision` が同じ間は外側の read-only mapping を再利用する。

### 2) `parameter_context(store, cc_snapshot)`（フレーム境界の固定）

- 実装: `src/grafix/core/parameters/context.py`
- 役割:
  - `store_snapshot(store)` を contextvar に固定（draw 中の参照が決定的になる）
  - `FrameParamsBuffer()` を作って「このフレームで観測した引数」を蓄積する
  - MIDI CC スナップショット（任意）を固定する

### 3) `resolve_params(...)`（base/GUI/CC から effective を決める）

- 実装: `src/grafix/core/parameters/resolver.py`
- 役割:
  - `ParameterKey(op, site_id, arg)` で GUI 行を識別する（`src/grafix/core/parameters/key.py`）
  - スナップショットに state があれば GUI/CC を反映し、なければ base を採用する
  - 量子化（署名安定化）はここで一元化する（`_quantize`）
  - 観測結果を `FrameParamsBuffer.record(...)` に積む（次の merge の入力）

`resolve_params` は通常、API 層から呼ばれる:

- `src/grafix/api/_param_resolution.py`（`resolve_api_params`）

### 4) `FrameParamsBuffer`（観測の一時置き場）

- 実装: `src/grafix/core/parameters/frame_params.py`
- 役割:
  - parameter の (key, base, meta, effective, source, explicit) を蓄積する
  - effect chain は完全な code topology を `FrameEffectChainRecord` として別に蓄積する
  - label の観測もここへ集める（`FrameParamsBuffer.set_label`）

### 5) `merge_frame_params(store, records)`（フレーム終了時に永続化）

- 実装: `src/grafix/core/parameters/merge_ops.py`
- 呼び出し元: `src/grafix/core/parameters/context.py`（`parameter_context` の正常終了時）
- 役割:
  - 観測されたキーを `ParamStore` に登録し、UI 値/override などの初期ポリシーを適用する
  - label は `src/grafix/core/parameters/labels_ops.py` の `merge_frame_labels` で保存する

## 重要な補足

- parameter kind は `bool/int/float/str/font/choice/vec3/rgb` の 8 種だけで、
  `ParamMeta`・UI 値・MIDI CC は `validation.py` の共通契約で検証する。
- UI の型違い、非有限値、無効な choice は暗黙変換せず拒否する。保存済みの stale choice は
  GUI 上で unavailable として残すが、明示的な再選択まで effective 値には採用しない。
- scalar MIDI CC は `float/int/choice`、3 要素 tuple は `vec3` だけに割り当てられ、
  CC 番号は厳密な int の `0..127` とする。
- worker（multiprocessing）では `parameter_context_from_snapshot(...)` を使う:
  - 実装: `src/grafix/core/parameters/context.py`
  - frame task は revision だけを持ち、変更時だけ bounded control queue で snapshot 本体を受け取る
  - 役割: `ParamStore` を持たずに、適用済み revision の snapshot + 観測だけを扱う
- `site_id` は「呼び出し箇所 ID」で、GUI 行の安定性に直結する:
  - 生成: `grafix.core.parameters.caller_site_id`（入口は `src/grafix/core/parameters/__init__.py` 側）
  - G/E/L/P の `key=` で semantic site の明示的な安定 ID を指定できる
  - loop/comprehension の個別 group は `instance_key=i`、意図的な共有 group は
    `shared=True` を使う（両者は同時指定できない）
- 折りたたみ状態は `CollapsedHeaderKey` の
  `style/primitive/preset/effect_chain` タグで識別し、ParamStore v4 では同じ tagged record を
  main store と variation snapshot に保存する。

## Undo / Redo と Snapshot A/B の境界

- `adjustment_snapshot.py` の `ParameterAdjustmentSnapshot` は
  ParamStore 全体の過去コピーではなく、次の
  **GUI-owned 調整値**だけを保存する:
  - `override` / `ui_value`
  - MIDI CC 割当
  - GUI が調整した `ui_min` / `ui_max`
  - 既存 header の折りたたみ状態
  - effect 順と、その互換性を判定する topology signature
- 復元は whole-store 置換ではなく、現在も存在する key への merge である。
  Snapshot 保存後に draw が発見した parameter、label、ordinal、
  effect chain、explicit 情報、runtime 観測値は削除・巻き戻ししない。
- code reload 後は、同じ `ParameterKey` と `meta.kind` を保つ parameter
  にだけ保存済み GUI 値を適用する。削除された key や kind が
  変わった key は安全にスキップする。安定的な再適用が必要な呼び出しには
  G/E/L/P の `key=` を指定する。
- 現在と同じ Snapshot の Load は no-op で、revision や Undo depth を増やさない。
