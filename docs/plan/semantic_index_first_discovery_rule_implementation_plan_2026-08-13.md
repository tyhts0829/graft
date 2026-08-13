# Semantic index first 探索規則の追加計画（2026-08-13）

- 状態: **完了**
- 対象: root `AGENTS.md` のsource discovery規則だけ

## 目的

coding agentが広範囲のsource本文を読む前にsemantic indexで候補を絞り、今回追加したmetadataを
実際のcontext節約へつなげる。

## 変更

- [x] root `AGENTS.md` に短い `Source discovery` 節を追加する。
- [x] scoped index、header確認後の選択的読込、exact symbol検索時の`rg`優先を明記する。
- [x] headerがないfileを探索対象外と誤認しない注意を明記する。
- [x] manifestのcommit、自動hook、skill、追加toolは導入しない。

## 検証

- [x] 既存AGENTS規則と矛盾せず、追加が簡潔であることを確認する。
- [x] `python3 tools/semantic_index.py src/grafix/core` が引き続き成功することを確認する。
- [x] `git diff --check` を実行する。

検証結果: core semantic index 84 entries、path順、`git diff --check`成功。
