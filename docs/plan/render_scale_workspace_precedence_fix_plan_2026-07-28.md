# render_scale と WorkspaceState の優先順位修正計画（2026-07-28）

- 状態: **実装中（2026-07-28 承認済み）**
- 対象: interactive preview の初期サイズと保存済み WorkspaceState の競合
- 計画作成時 HEAD: `eed82b4`
- 計画作成時 branch: `main`
- 再現対象:
  `sketch/agent_loop/runs/run_20260727_225706_n1/final/sketch.py`

本計画は、コードで明示した `render_scale` が保存済み preview size によって黙って
無効化される問題を修正する。production code、test、公開文書は、本計画への明示承認後に変更する。

## 1. 現象と原因

対象 sketch は `canvas_size=(148, 210)`、`render_scale=8.0` なので、preview の自然な
要求サイズは `1184 x 1680` になる。しかし、次の WorkspaceState には以前の
`592 x 840` が保存されている。

`data/output/workspace/agent_loop/runs/run_20260727_225706_n1/final/sketch.json`

起動時には次の順で処理される。

1. `canvas_size * render_scale` から preview window を作る。
2. `WorkspaceWindowController` が保存済み `preview_rect` を読み込む。
3. 保存済み width / height を `window.set_size()` で適用する。

このため明示的なコード指定より暗黙の永続状態が優先される。また、上書きを画面やログで
説明しないため、`render_scale` の変更が無視されたように見える。

## 2. 採用する契約

`render_scale` の「コード管理」と「Workspace管理」を引数の有無で明示的に分ける。

```python
run(draw, render_scale=8.0)  # コード管理: 毎回 8.0 を優先
run(draw)                    # Workspace管理: 保存済みサイズを復元
run(draw, render_scale=None) # 同上
```

- 公開 API を `render_scale: float | None = None` とする。
- 数値を指定した場合:
  - `canvas_size * render_scale` を現在の要求 preview size とする。
  - 保存済み WorkspaceState からは位置、Inspector の矩形と表示状態、UI scale を復元する。
  - 保存済み preview の width / height は復元しない。
  - 要求サイズが画面内に収まらない場合だけ、既存の安全領域へアスペクト比を保って縮小する。
- `None` または省略した場合:
  - 保存済み preview size を従来どおり復元する。
  - WorkspaceState がなければ実効 `render_scale=1.0` を初期値にする。
- 手動 resize の保存自体は維持する。次回それを使うかどうかだけを上記契約で決める。
- PNG/SVG/G-code の export サイズと `export.png.scale` の契約は変更しない。
- WorkspaceState v1 の JSON schema は変更せず、migration や互換 shim は追加しない。

明示数値と省略を区別するため、`render_scale != 1.0` のような値ベース判定は行わない。
これにより `render_scale=1.0` も正しくコード管理として扱える。

## 3. 対象ファイル

### production

- [ ] `src/grafix/api/runner.py`
  - public signature と NumPy style docstring を更新する。
- [ ] `src/grafix/api/_runner_application.py`
  - `None` と明示数値を分離し、実効 scale と preview size policy を構成する。
- [ ] `src/grafix/interactive/runtime/workspace_window_controller.py`
  - 保存状態のうち preview size だけを条件付きで採用する。
  - 明示サイズの画面内縮小では preview のアスペクト比を保つ。
- [ ] 必要な場合のみ `src/grafix/interactive/runtime/window_layout.py`
  - 既存の pure layout helper で表現できない場合に限り、最小変更する。

### tests

- [ ] `tests/api/test_runner_runtime_validation.py`
- [ ] `tests/api/test_runner_window_layout.py`
- [ ] 必要な場合のみ `tests/api/test_runner_authoring_composition.py`
- [ ] 必要な場合のみ `tests/sketch/test_active_sketch_entrypoints.py`

### docs

- [ ] `README.md`
- [ ] 必要な場合のみ `docs/developer_guide.md`

## 4. 実装フェーズ

### Phase 0 — baseline と失敗テスト

- [x] `render_scale=8.0` の要求値 `1184 x 1680` を確認した。
- [x] 保存済み値 `592 x 840` に上書きされる原因を特定した。
- [x] 関連 window layout test の現行成功を確認した。
- [x] 実装開始時に `git status --porcelain` と対象ファイルの並行差分を再確認する。
- [ ] 保存済み `592 x 840` と明示要求 `1184 x 1680` を使う回帰テストを追加し、修正前に失敗させる。

### Phase 1 — API と policy の分離

- [ ] `render_scale=None` を Workspace管理、数値をコード管理として正規化する。
- [ ] DrawWindowSystem には常に検証済みの正の `float` を渡す。
- [ ] bool、文字列、0以下、NaN、無限大を従来どおり副作用前に拒否する。
- [ ] public/private docstring に優先順位と画面内縮小を明記する。

### Phase 2 — Workspace restore の修正

- [ ] コード管理時は保存済み preview の位置だけを採用し、サイズは現在の要求値を採用する。
- [ ] Workspace管理時は位置とサイズの双方を復元する。
- [ ] Inspector rect、visibility、UI scale の復元を維持する。
- [ ] 明示要求が画面より大きい場合、アスペクト比を維持したまま安全領域へ収める。
- [ ] GUI 有効・無効の双方で同じ優先順位にする。

### Phase 3 — 回帰テスト

- [ ] 保存 `592 x 840` + 明示 `1184 x 1680` では、十分大きい画面上で `1184 x 1680` を採用する。
- [ ] 同じ保存状態 + `render_scale=None` では `592 x 840` を復元する。
- [ ] WorkspaceState がない場合、`None` は 1.0 倍、明示数値はその倍率を採用する。
- [ ] 明示 `render_scale=1.0` も保存サイズより優先する。
- [ ] 画面超過時の縮小がアスペクト比を維持する。
- [ ] 保存位置、Inspector 状態、UI scale、終了時 persist が退行しない。

### Phase 4 — 文書と検証

- [ ] README の interactive example 周辺に `render_scale` と WorkspaceState の関係を追記する。
- [ ] 対象 test、`ruff check`、`mypy src/grafix` を実行する。
- [ ] 影響範囲を確認後、`PYTHONPATH=src pytest -q` を実行する。
- [ ] 実機で対象 sketch を起動し、保存済み WorkspaceState があってもコード指定が採用されることを確認する。
- [ ] 完了項目を本書でチェックし、未完了事項を明記する。

## 5. 完了条件

- `render_scale=8.0` が保存済み `592 x 840` に黙って戻されない。
- 明示数値は常に保存 preview size より優先される。
- 省略または `None` なら手動 resize の復元を選べる。
- 画面制約による縮小と WorkspaceState による上書きを混同しない。
- WorkspaceState v1、Inspector、UI scale、export の既存契約を壊さない。
- 対象 test、lint、type check、full test suite が成功する。

## 6. 非対象

- preview window と offscreen render framebuffer の全面的な分離
- preview の zoom / pan / 1:1 表示機能
- WorkspaceState schema の更新や既存 JSON の一括変換
- export 解像度や capture manifest の変更
- 対象 sketch 固有の WorkspaceState ファイル削除による回避
