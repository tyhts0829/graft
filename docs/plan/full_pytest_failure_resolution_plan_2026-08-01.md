# Full pytest failure 解消計画

## 目的

現行 workspace の full pytest で再現した2件の失敗と、clean checkoutで追加再現した
stub同期テストの環境依存を解消し、ローカル環境とCI相当環境の双方で決定的に通過する状態にする。

## 調査済みの失敗

- `tests/api/test_runner_parameter_recovery.py`
  - GUI構築失敗テストの `DrawWindow` fakeが、現行の
    `capture_service._export_owned` 契約へ追従していない。
- `tests/sketch/test_active_sketch_entrypoints.py`
  - active sketchが53件へ増えた一方、監査済みinventoryの期待値が52件のままになっている。
- `tests/stubs/test_api_stub_sync.py`
  - ignoredな `.grafix/config.yaml` の有無で生成結果が変わる。
  - installed stubである `src/grafix/api/__init__.pyi` にproject-local presetが混入している。

## 実装方針

1. GUI lifecycle testのfakeだけを現行capture契約へ合わせる。
   - productionのthumbnail ownership配線は変更しない。
   - `capture_service` にcallableな `_export_owned` を持たせる。
2. active sketch inventoryのexact期待値を52件から53件へ更新する。
   - inventory回帰検知を維持するため、非空判定や下限判定には弱めない。
3. installed stubの同期条件を同梱default configへ固定する。
   - stub同期CLIへ `src/grafix/resource/default_config.yaml` を明示する。
   - 同じ条件で `src/grafix/api/__init__.pyi` を再生成し、project-local presetを除外する。
   - project-local typingは既存設計どおり `typings/grafix/api/__init__.pyi` の責務とする。

## 実施項目

- [x] full pytestと個別テストで失敗を再現する。
- [x] 各失敗の導入履歴と責務を確認する。
- [x] GUI lifecycle testの `DrawWindow` fakeを修正する。
- [x] active sketch inventoryの期待値を53件へ更新する。
- [x] stub同期テストへ同梱default configを明示する。
- [x] installed API stubを同梱default configで再生成する。
- [x] 変更対象へruffを実行する。
- [x] 3件のfocused testを実行する。
- [x] clean checkout相当でstub同期テストを実行する。
- [x] full pytestを実行する。
- [x] 最終差分と作業ツリーを確認する。

## 検証結果

- 変更対象ruff: pass
- focused test: 3 passed
- clean checkout相当のstub同期テスト: 1 passed
- full pytest: 4120 passed

## 検証コマンド

```bash
PYTHONPATH=src ruff check \
  tests/api/test_runner_parameter_recovery.py \
  tests/sketch/test_active_sketch_entrypoints.py \
  tests/stubs/test_api_stub_sync.py

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src pytest -q -p no:cacheprovider \
  tests/api/test_runner_parameter_recovery.py::test_gui_construction_failure_closes_completed_draw_system_and_midi_once \
  tests/sketch/test_active_sketch_entrypoints.py::test_active_sketch_inventory_is_not_empty \
  tests/stubs/test_api_stub_sync.py

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src pytest -q -p no:cacheprovider
```

## 対象外

- productionのcapture APIやthumbnail ownership設計の変更。
- active sketch inventory testの期待パス集合方式への拡張。
- project-local presetや `.grafix/config.yaml` のGit管理化。
- 依頼外ファイルの整理、互換ラッパー、依存追加。
