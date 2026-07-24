# Repository Guidelines (AGENTS.md)

本ファイルは、エージェント/人間が共通で参照する最小ガイド。運用の詳細は `docs/agent_docs/` を参照する。

## TL;DR

- Grafix は Python の creative coding / pen plotter 向けツールキット（macOS-first）。
- 本体は `src/grafix/`（import 名は `grafix`）。設計メモは `README.md` と `architecture.md`。
- 必要十分な堅牢さとし、複雑化を避け、可読性・シンプルさ・美しさを優先。過度に防御的な実装は絶対にしない。
- 破壊的変更でも構わないので美しいシンプルな実装を目指すこと。実装改善に伴い互換ラッパー、シムなどは実装しない。
- 作業開始は `git status --porcelain`。複数のコーディングエージェントが並列で作業していますので、依頼範囲外の差分/未追跡が生じることがありますが、それは触らないでください。
- 実装タスクは、実装前にアクションを細分化した新規 `.md` を作り、私にそれでいいか確認すること。その後、私の返答に基づいて実装し、完了アイテムをチェックしていき、何が完了し、何が完了していないかを常に明確にすること。ただし作品制作と既存作品の調整では計画 `.md` の作成・事前確認を省略し、直ちに実行する。
- Codex による作品制作では、確定した最終 PNG を `data/output/png/codex_generated/` にも必ず保存する。ファイル名は run ID などを使って衝突を避ける。
- 回答は日本語。

## WHY（目的）

- 線（ポリライン列）を生成し、effect をチェーンして変形し、リアルタイムにプレビューするための小さなフレームワーク。
- 詳細: `README.md` / `architecture.md`

## WHAT（リポ地図）

- `src/grafix/`: ライブラリ本体（公開 API はここから）
- `tests/`: pytest
- `sketch/`: サンプル/プリセット
- `docs/`: 設計・レビュー・計画（運用詳細は `docs/agent_docs/`）
- `tools/`: 開発補助 CLI
- `typings/`: 型スタブ/補助

## Build

- src レイアウト: 本体パッケージは `src/grafix/`（import 名は `grafix`）
- `pip install -e .` / `pip install -e ".[dev]"`（依存取得を伴う場合は Ask-first）

## Test

- インストール無し: `PYTHONPATH=src pytest -q`
- インストール後: `pytest -q`

## Style

- ruff: `ruff check .`
- mypy: `mypy src/grafix`

## Safety & Permissions（許可境界）

- Allow: 読取/一覧、対象限定の lint/type/test、スタブ生成のドライラン/差分確認
- Ask-first: 依存追加/更新（ネットワーク）、破壊的操作、依頼外差分の `git restore/reset/add`、未追跡/依頼外ファイルの削除・移動・上書き、長時間実行/CI・スナップショット更新、`git push`/リリース
- 複数エージェントが並列編集する前提で、依頼範囲外の整理/巻き戻しはしない

## PR

- 変更は依頼範囲に限定し、不要な互換ラッパー/シムは作らない（破壊的変更も可）
- 破壊的操作や依存追加は Safety の Ask-first に従う
- `git commit` / `git push` は依頼がある場合のみ

## Docs（詳細）

- 公開 API は NumPy スタイル docstring + 型ヒント（日本語、絵文字不可）。詳細: `docs/agent_docs/documentation.md`
- テスト詳細（markers / stubs 等）: `docs/agent_docs/testing.md`
- AGENTS のメタ方針（このファイルの書き方/運用）: `docs/agent_docs/agents_meta.md`
