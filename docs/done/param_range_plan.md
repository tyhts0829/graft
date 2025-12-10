# param_range_plan.md

どこで: `docs/param_range_plan.md`。対象コードは `src/parameters/resolver.py`, `src/parameters/meta.py`, `src/parameters/frame_params.py`, `src/parameters/store.py`, `src/api/primitives.py`, `src/api/effects.py`, `src/primitives/*`, `src/effects/*`, `tests/parameters/*`。
何を: ParamMeta.ui_min/ui_max を「GUI スライダーの推奨レンジ」に限定し、描画値のクランプはしない方針へ修正する計画をまとめる。
なぜ: ui_min/ui_max（旧 min/max）を実値クランプに使うと、canvas サイズ依存の座標や振幅が想定外に潰れ、UI 用レンジ指定が描画を壊すため。

## 0. ゴール

- ParamMeta.ui_min/ui_max は UI スライダーの初期レンジとしてのみ使用する。
- 値解決（effective）は ui_min/ui_max でクランプしない（opt-in フラグも持たない）。
- 既存メタ定義（circle など）を「スライダー推奨レンジ」に見直し、描画を制限しない。
- テストを追加し、ui_min/ui_max が描画値をクランプしないことを担保する。

## 1. 方針

- resolver から clamp 処理を外し、量子化のみ維持。clamp オプションは導入しない。
- ParamMeta の ui_min/ui_max を明示し、「UI 用」であることを明確にする（旧 min/max を置き換え）。
- UI スライダー生成（将来の GUI 実装用）では ui_min/ui_max をレンジに使う。
- 既存 meta の数値レンジは「初期スライダー目安」として広げる（例: cx/cy 無制限、r は 0..任意大の実値可）。

## 2. タスク分解（チェックリスト）

- [x] `resolver.py`: clamp を撤去し、量子化のみ維持。
- [x] `meta.py`: ui_min/ui_max を UI 用であることを明記（clamp フラグは持たない）。
- [x] `frame_params.py` / `store.py`: meta の扱いを ui_min/ui_max に合わせる。
- [x] `primitive_registry.py` / `effect_registry.py`: meta の新フィールドを受け取り、渡し方を更新。
- [x] `api/primitives.py` / `api/effects.py`: resolve_params 呼び出しに meta の新フィールドを渡せる形にする（インターフェースはそのまま）。
- [x] 既存メタ定義の見直し: `circle`, `scale` などで ui_min/ui_max を「推奨レンジ」に設定。
- [ ] ドキュメント更新: `parameter_gui_plan.md` に ui_min/ui_max の意味変更を追記。

## 3. リスク・留意点

- clamp を外すことで極端な値が入ると downstream で NaN/inf が発生する可能性がある。NaN/inf は Geometry.create で既に reject するため、ここではそのまま通す。
- UI でのスライダー初期レンジが広すぎると操作しづらい。プリセット値を適切に設定する。
- meta フィールド名変更に伴う既存コード破断に注意（リファクタ範囲を明確化する）。

## 4. 完了定義

- clamp がデフォルト無効になり、ui_min/ui_max は UI 用であることがコードと docstring で明示されている。
- サンプル (main.py) で円グリッドが期待通り複数表示されることを手動確認。
- 追加したテストが通過し、既存 tests/parameters がすべてグリーンである。
