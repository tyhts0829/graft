# どこで: `src/grafix/interactive/parameter_gui/store_bridge.py` / `src/grafix/core/parameters/merge_ops.py` / `src/grafix/core/parameters/runtime.py`。
# 何を: Parameter GUI で CC 割当（`cc_key`）を解除したときに、パラメータが元の `ui_value` へ戻らないよう「解除時点の effective 値」を `ui_value` に焼き込む。
# なぜ: resolver は `cc_key` が無いと `ui_value`（+ override）へフォールバックするため、解除操作で値がジャンプする。

## 現状の観察

- UI 側（`table.py`）で CC ボタンを押すと `cc_key=None`（または vec3 成分が `None`）になり、`changed=True` で返る。
- store 反映（`store_bridge._apply_updated_rows_to_store()`）は `update_state_from_ui(..., ui_value=after.ui_value, cc_key=after.cc_key)` を呼ぶ。
  - `cc_key` だけが変わるケースでは `after.ui_value` は「元の GUI 値」のまま。
- 結果として `cc_key` 解除後は resolver が `ui_value` を採用し、値が元に戻る（ジャンプ）。

## 方針（採用案）

- **解除イベント（CC が “減った”）の瞬間に、その時点の `effective` を `ui_value` に焼き込む（bake/commit）。**
- 併せて **`override=True` にする**（explicit パラメータ等で `override=False` のままだと base に戻ってしまうため）。
- `effective` の算出は UI 側で再実装せず、**フレームで実際に使われた `effective` を ParamStore の runtime にキャッシュ**してそれを利用する。
  - 理由: CC→値の写像（min/max、量子化、vec3 の成分ルール）が resolver に集約されているため、UI 側で重複実装するとズレや保守コストが増える。

## 仕様（焼き込みの条件）

- 対象: `before.cc_key` と `after.cc_key` が異なる更新のうち、**CC の集合が減るケースのみ**。
  - 例: `12 -> None`、`(10, 11, 12) -> (10, None, 12)`、`(10, 11, 12) -> None`
- 非対象: **再アサイン（入れ替え）**。例: `12 -> 64`（CC が減って増える）では bake しない。
- 焼き込み値: `runtime.last_effective_by_key[key]`（無ければ bake をスキップして現状維持）。
- 焼き込み操作（1 回の state 更新で完結させる）:
  - `update_state_from_ui(store, key, baked_effective, meta=..., override=True, cc_key=after.cc_key)`

## 実装メモ

- `ParamStoreRuntime` に **非永続**のキャッシュを追加する:
  - `last_effective_by_key: dict[ParameterKey, object]`
- `merge_frame_params()` で `rec.effective is not None` のときだけ `runtime.last_effective_by_key[rec.key] = rec.effective` を更新する。
  - テスト等で `effective=None` の `FrameParamRecord` が来るケースがあるため（現状も許容されている）。
- `store_bridge._apply_updated_rows_to_store()` で cc_key の差分を見て bake 条件を判定する。
  - 判定は `cc_numbers(cc_key)` の set 比較（removed / added）でシンプルに書く。

## チェックリスト

- [x] 事前確認: 「解除時に effective を ui_value へ焼き込み、override=True にする」方針で進めてよい
- [x] `src/grafix/core/parameters/runtime.py` に `last_effective_by_key` を追加（非永続）
- [x] `src/grafix/core/parameters/merge_ops.py` で `effective` を runtime に記録（`None` はスキップ）
- [x] `src/grafix/interactive/parameter_gui/store_bridge.py` で `cc_key` 解除検知 → bake を実装
- [x] テスト追加: scalar 解除で bake される（`override` が False でも True になる）
- [x] テスト追加: vec3 の 1 成分解除で bake される（解除成分が保持され、残り CC は継続）
- [x] テスト追加: 再アサイン（例: `12 -> 64`）では bake されない
- [x] 手動確認: Parameter GUI 上で CC 解除しても値がジャンプしない
- [x] 最低限の確認: `PYTHONPATH=src pytest -q tests/core/parameters tests/interactive/parameter_gui`
