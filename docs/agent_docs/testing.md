<!--
どこで: `docs/agent_docs/testing.md`。
何を: Grafix のテスト運用（pytest・再現性・markers・スタブ同期）をまとめる。
なぜ: ルート `AGENTS.md` から詳細を分離し、必要なときだけ参照できるようにするため。
-->

# テスト規約

## 基本

- `pytest` を使う。
- `tests/test_*.py` は対象モジュールと対応させる。
- 乱数は固定し再現性を確保する。

## スタブ

- 公開 API を変更したらスタブを再生成し、スタブ同期テストを更新する。
  - 例: `python -m grafix stub`
  - 例: `pytest -q tests/stubs/test_api_stub_sync.py`

## markers 実行例

- unit: `pytest -q -m "not integration and not e2e"`
- multiprocessing/subprocess/resource lifecycle: `pytest -q -m integration`
- 公開 CLI/application round trip: `pytest -q -m e2e`

performance は pytest marker ではなく、決定的 benchmark CLI を使う。

- smoke: `python -m grafix benchmark run --suite smoke --profile smoke`
- full/manual: `python -m grafix benchmark run --suite all --profile long --mode warm`
