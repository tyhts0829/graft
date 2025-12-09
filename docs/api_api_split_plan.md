# api_api_split_plan.md

どこで: `docs/api_api_split_plan.md`。対象: `src/api/api.py` を中心に `src/api/__init__.py`, `tests/api/test_layer_helper.py` など API 依存箇所。
何を: G/E/L を一つのモジュールに同居させている構成を責務単位で分割し、再エクスポート経由で後方互換を保つ計画をまとめる。
なぜ: 名前空間ごとの関心を分けて見通しを良くし、primitive/effect/layer の追加・拡張時の変更波及を小さくするため。

## 0. 現状整理 (2025-12-09)

- `src/api/api.py` に `PrimitiveNamespace`/`EffectBuilder`/`EffectNamespace`/`LayerHelper` と公開ハンドル `G`, `E`, `L` が同居している。
- primitive/effect の登録はレジストリ経由だが、`__getattr__` の例外メッセージや Geometry 生成ロジックがモジュール内に直書きされ再利用しづらい。
- `tests/api/test_layer_helper.py` などは `src.api.api` から直接 `L` を import しており、分割時に互換レイヤが必要。

## 1. 目標

- primitive/effect/layer を役割別のモジュールに分け、各モジュールが単一責務のファサードを提供する。
- 既存の import (`from src.api.api import G, E, L`) を壊さず、新規推奨 import を `src.api.primitives` などに明示する。
- ドキュメントとテストの参照パスを整理し、今後の API 拡張時に各レイヤが独立して進化できるようにする。

## 2. 分割方針（提案）

- `src/api/primitives.py`: `PrimitiveNamespace`, `G` を配置し、primitive 関連ヘルパに限定。
- `src/api/effects.py`: `EffectBuilder`, `EffectNamespace`, `E` を配置し、effect パイプラインの責務に限定。
- `src/api/layers.py`: `LayerHelper`, `L` を配置し、Geometry を Layer 化する責務に限定。
- `src/api/api.py`: 上記を import して再エクスポートする互換シムに縮小。将来的な廃止方針は後続で判断。
- `src/api/__init__.py`: 新モジュールからの re-export に切り替え、公開 API の入口を整理。
- テスト/ドキュメントは新パスを推奨しつつ、旧パスもカバレッジで担保する。
- 型エイリアス専用ファイル（例: `types.py`）は作成せず、必要な型はシグネチャに直接書く。

## 3. タスク分解チェックリスト

- [ ] 現行 API の挙動確認メモを追加（例外メッセージ、TypeError/ValueError 条件）。
- [ ] 新モジュール雛形を作成（各ファイルにヘッダ、NumPy スタイル docstring、`__all__` を整備）。
- [ ] `PrimitiveNamespace` を `primitives.py` へ移動し、型エイリアスを使わずシグネチャに直接型を記述する。
- [ ] `EffectBuilder`/`EffectNamespace` を `effects.py` へ移動し、型エイリアスを設けず現行挙動を保つテストを追加。
- [ ] `LayerHelper` を `layers.py` へ移動し、`__call__` の入力正規化とバリデーション挙動を保持するテストを追加。
- [ ] `src/api/api.py` を薄い互換シムへ縮小（新モジュールから import、docstring 更新）。
- [ ] `src/api/__init__.py` を新モジュール re-export に更新し、`__all__` と docstring を確認。
- [ ] 既存テストを新パスに追従させるか互換 import でカバーするか方針を決め、必要なら新規テストを追加。
- [ ] ドキュメント（README/spec/plan）に新しい import パスを追記。
- [ ] 影響調査: 他モジュールが `EffectBuilder` などへ直接依存していないか `rg` で確認し、必要に応じて import を修正。
- [ ] 最小限のテスト実行で動作確認（例: `pytest -q tests/api/test_layer_helper.py`）。

## 4. 決めたいこと・質問

- 互換目的で `src/api/api.py` を残す期間: 恒久的に re-export するか、段階的廃止を目指すか？；今回の変更で完全に削除します。
- 新モジュール名は複数形 (`primitives.py`, `effects.py`, `layers.py`) で揃える案でよいか？ 単数形や `namespace_*.py` の方が良いか？；複数形でいいよ。
- `EffectBuilder`/`GeometryFactory` などの型エイリアスを共通 `types.py` へ置くか、それぞれのモジュールに閉じておくか？；型エイリアスは作らず `types.py` も用意しない（型は必要箇所へ直接記述）。
- 新推奨 import パスを README/spec にどの程度まで記載するか？（ユーザー向け API の公開レベル）

## 5. リスク/配慮点

- 動的な `__getattr__` ベースの API はレジストリ import 順序依存があるため、分割時に初期化漏れが起きないよう import 手順を明記する。
- モジュール分割に伴う循環参照の可能性（特にレジストリ読み込み時）。`__init__.py` の import 順序を意識して回避する。
- 互換シムを残した場合、両パスの docstring 更新やテスト冗長化に注意し、どのパスを優先サポートするか明確にする。

## 6. 完了判定

- `src/api/api.py` の責務が互換レイヤに限定され、各役割別モジュールで `G`/`E`/`L` が定義される。
- テストが新旧 import パスをカバーし、挙動と例外メッセージが現状と一致する。
- ドキュメントに新パスが記載され、後方互換の取り扱い方針が合意されている。
