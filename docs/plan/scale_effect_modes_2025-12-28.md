# scale effect モード追加チェックリスト（2025-12-28）

目的: `src/grafix/core/effects/scale.py` に「全体スケール」「各 line を中心維持で短く」「各閉曲線を中心維持で小さく」の 3 モードを追加する。

背景:

- 現状の `scale` は「入力全体」を 1 つの中心（auto_center なら平均座標 / それ以外は pivot）で拡大縮小するのみ。
- 複数のポリライン（線）/閉曲線（ループ）が混在する場合、全体スケールだと各要素の“位置関係”も変わってしまう。

方針（今回の決定案）:

- `scale` に `mode` パラメータ（choice）を追加する（デフォルトは既存互換）。
- `mode` により「中心の取り方」と「どのポリラインに適用するか」を切り替える。
- effect 間依存は禁止（`scale.py` 単体で完結）。共通ロジックが必要なら `src/grafix/core/effects/util.py` に置く。

非目的:

- GUI での条件付き表示（mode により `pivot` を隠す等）の実装
- 「閉曲線中心」を面積重心などへ高度化（まずは頂点平均で十分）
- 既存プリセット/永続化データの移行（破壊的変更を避ける設計にする）

## 0) 事前に決める（あなたの確認が必要）

- [ ] `mode` の名前（choices）を確定する（案: `"all" | "each_line" | "each_closed"`）；all, by_line, by_face
- [ ] `"each_line"` の対象を「開ポリラインのみ」とし、閉曲線はそのまま返す（対象外）方針でよい；はい
- [ ] `"each_closed"` の対象を「閉曲線のみ」とし、開ポリラインはそのまま返す（対象外）方針でよい；はい
- [ ] 「閉曲線判定」は `np.allclose(v[0], v[-1], atol=1e-6, rtol=0)` とする（`weave` と同等）；はい
- [ ] 「中心座標」は以下でよい
  - `"all"`: 既存どおり（auto_center=True なら全頂点平均 / False なら pivot）；はい
  - `"each_line"`: 各ポリラインの頂点平均；はい
  - `"each_closed"`: 各閉曲線の頂点平均（重複を避けるため `v[:-1]` を平均、変換自体は全点に適用）；はい
- [ ] `"each_*"` モードでは `auto_center/pivot` は無視し、常に「各要素の中心」を使う（docstring に明記）；はい
- [ ] `scale_meta["scale"].ui_min` を 1.0 → 0.0（または 0.01）へ変更して「縮小」を UI から選びやすくする；はい

## 1) 受け入れ条件（完了の定義）

- [ ] `"all"` モードの挙動が現状テストと一致する（既存互換）
- [ ] `"each_line"` で、開ポリラインは中心座標を保ったまま縮小/拡大される（ポリラインごとに独立）
- [ ] `"each_closed"` で、閉曲線は中心座標を保ったまま縮小/拡大される（閉曲線ごとに独立）
- [ ] ポリラインの offsets 構造が保持される（入力と同じ offsets を返す）
- [ ] `PYTHONPATH=src pytest -q tests/core/effects/test_scale.py`
- [ ] `PYTHONPATH=src pytest -q tests/interactive/parameter_gui/test_parameter_gui_param_order.py`（`mode` 追加に伴う順序更新を含む）
- [ ] `python -m tools.gen_g_stubs` 後にスタブ同期テストが通る（既存の `tests/stubs/`）
- [ ] `mypy src/grafix`（任意だが推奨）
- [ ] `ruff check .`（環境に ruff がある場合のみ）

## 2) 変更箇所（ファイル単位）

- [ ] `src/grafix/core/effects/scale.py`
  - [ ] `scale_meta` に `mode: choice` を追加
  - [ ] `scale(..., mode: str = "all", ...)` を追加し、モード分岐を実装
  - [ ] docstring を更新（各 mode の意味 / 既存互換 / `each_*` で `pivot` が無視される等）
  - [ ] UI レンジ調整（`scale` の ui_min）
- [ ] `tests/core/effects/test_scale.py`
  - [ ] `"each_line"` のテスト追加（複数ポリラインで中心維持を検証）
  - [ ] `"each_closed"` のテスト追加（閉曲線で中心維持・閉ループ維持を検証）
- [ ] `tests/interactive/parameter_gui/test_parameter_gui_param_order.py`
  - [ ] `scale` の署名順に `mode` を追加した期待順へ更新（例: `bypass, mode, auto_center, pivot, scale`）
- [ ] `src/grafix/api/__init__.pyi`（自動生成）
  - [ ] `E.scale(..., mode: str = ..., ...)` が反映されることを確認（手編集せず再生成）

## 3) 手順（実装順）

- [ ] 事前確認: `git status --porcelain` で依頼範囲外の差分/未追跡を把握（触らない）
- [ ] `scale.py` の現状仕様とテストを読み、追加モードの仕様を確定（上記 0) をあなたと合意）
- [ ] `scale.py` に `mode` を追加して実装（全体 / 各 line / 各閉曲線）
- [ ] core テスト追加（`tests/core/effects/test_scale.py`）
- [ ] GUI 順序テスト更新（`tests/interactive/parameter_gui/test_parameter_gui_param_order.py`）
- [ ] スタブ再生成: `python -m tools.gen_g_stubs`
- [ ] 最小確認: 追加したテストを対象に `pytest -q` を実行
- [ ] 任意: `mypy` / `ruff`

## 4) 実行コマンド（ローカル確認）

- [ ] `PYTHONPATH=src pytest -q tests/core/effects/test_scale.py`
- [ ] `PYTHONPATH=src pytest -q tests/interactive/parameter_gui/test_parameter_gui_param_order.py`
- [ ] `python -m tools.gen_g_stubs`
- [ ] `PYTHONPATH=src pytest -q tests/stubs/test_g_stub_sync.py`
- [ ] `mypy src/grafix`
- [ ] `ruff check .`

## 追加で事前確認したほうがいい点 / 追加提案（気づいたら追記）

- [ ] `"each_line"` の「line」は「各ポリライン」か「各線分（隣接 2 点）」か、どちらの意味か最終確認したい
- [ ] `"each_closed"` の中心を「頂点平均」以外（面積重心/バウンディングボックス中心）へしたい要望があるか確認したい
