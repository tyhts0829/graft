# PyPI 環境での polyhedron データ配置の改善チェックリスト（2025-12-28）

目的: PyPI から新規環境へインストールした `grafix` で `G.polyhedron()` が落ちる問題を解消し、`sketch/readme.py` を含むスケッチが「追加設定なしで」動く状態にする。

背景（今回の事象）:

- `grafix.core.primitives.polyhedron` が `Path(__file__).parent / "regular_polyhedron"` の `.npz`（例: `dodecahedron_vertices_list.npz`）を読み込む設計。
- インストール先（`.../site-packages/grafix/core/primitives/regular_polyhedron/`）に `.npz` が含まれておらず、`FileNotFoundError` → `RealizeError` になる。
- `sketch/readme.py` は `G.polyhedron()` を呼ぶため、プレビュー/実行で落ちる。

方針（今回の決定）:

- [x] packaging を直して `.npz` を wheel/sdist に確実に含める（PyPI 配布でも必ず入る状態にする）
- [ ] 追加で、`polyhedron` 側のリソース取得を `importlib.resources` に寄せる（必要になったら検討）

非目的:

- データの生成ロジックを変える
- 互換ラッパー/シムで「欠落時に別パスを探す」挙動を増やす（破壊的変更 OK 方針に反するため）

## 0) 事前に決める（あなたの確認が必要）

- [x] PyPI 配布（wheel/sdist）でも `.npz` がインストールされることを最優先にする
- [ ] `importlib.resources` への移行は今回やらない（必要なら別チケット）

## 1) 受け入れ条件（完了の定義）

- [x] wheel 展開物（インストール相当）で `grafix/core/primitives/regular_polyhedron/*.npz` が存在する
- [x] wheel 展開物（インストール相当）で `realize(G.polyhedron())` が例外なく通る
- [ ] wheel インストールのみの環境で `python ./sketch/readme.py` が（polyhedron データ欠落理由で）落ちない
- [x] 配布物（wheel/sdist）の中に `regular_polyhedron/*.npz` が含まれていることを機械的に確認できる

## 2) 即時回避（開発時のワークアラウンド）

- [ ] インストール済み `site-packages` を使わず、`PYTHONPATH=src python ./sketch/readme.py` で repo のデータを使う
- [ ] もしくは `pip install -e .`（editable）で `src/` 側を使う

## 3) packaging（wheel/sdist へ確実に入れる）

- [x] `pyproject.toml` の `[tool.setuptools.package-data]` に `.npz` を追加（wheel 対策）
  - 追加案: `grafix = ["core/primitives/regular_polyhedron/*.npz", ...]`
- [x] `MANIFEST.in` にも `.npz` を追加（sdist 対策）
  - 追加案: `recursive-include src/grafix/core/primitives/regular_polyhedron *.npz`
- [ ] `.DS_Store` 等が配布物に入らないことを確認（必要なら `.gitignore` / MANIFEST 側で除外）

## 4) 検証（配布物の内容チェック）

- [x] wheel を作る（`setuptools.build_meta.build_wheel` で `dist/` に生成）
- [x] wheel の中身を確認する（`python -m zipfile -l dist/*.whl | rg regular_polyhedron`）
- [x] sdist の中身を確認する（`tar -tf dist/*.tar.gz | rg regular_polyhedron`）

## 5) 検証（実行テスト）

- [ ] クリーン環境に wheel を入れて `realize(G.polyhedron())` が通ることを確認
  - 依存を取りに行かないために `--no-deps` を使う等、手順を決める
- [ ] 同じ環境で `python ./sketch/readme.py` の起動（polyhedron 理由で落ちないこと）を確認

## 6) テスト（最小の安全柵）

- [x] `polyhedron` が参照する `.npz` の存在をユニットテストで検査（repo 実行での単純な存在確認）
- [ ] 可能なら「wheel を作って中身を検査する」軽量テストを追加（CI がある/作るなら）

## 7) ドキュメント（再発防止）

- [ ] README に「polyhedron は同梱データを使う」旨と、欠落時の典型原因（配布物の package-data 設定）を短く追記
- [ ] 開発者向けに「wheel の中身確認コマンド」をメモ（上の 4) をコピペで実行できる形に）

## 追加で事前確認したほうがいい点 / 追加提案（気づいたら追記）

- [ ] 今後 `.npz` 以外のデータ（例: `.npy`, `.json`）を増やす予定があるなら、package-data の方針を先に固定する
- [ ] 現状 wheel に `src/grafix/resource/.DS_Store` が紛れ込む（`python -m zipfile -l dist/*.whl | rg .DS_Store`）。不要なら削除/除外方針を決める
