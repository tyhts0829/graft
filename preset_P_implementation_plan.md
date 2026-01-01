# どこで: Grafix リポジトリ（設計メモ / 実装チェックリスト）。
# 何を: preset に `P.<name>` でアクセスできる公開 API（PresetNamespace）を導入する計画。
# なぜ: `@preset` で登録した「再利用単位」を、G/E と同じ感覚で呼び出せるようにするため。

# P（PresetNamespace）導入: 実装計画

## ゴール

- `from grafix import P`（または `from grafix.api import P`）で `P` を使える。
- `@preset` で登録した preset を `P.<name>(...)` で呼び出せる。
- Parameter GUI / 永続化の既存挙動（`preset_registry` を参照している箇所）が壊れない。

## 非ゴール（今回やらない）

- 補完/型の完全対応（ユーザー定義 preset を静的に列挙するのは困難）。
- “import を不要にする” こと（autoload 側の責務）。
- 互換ラッパー（破壊的変更が必要なら素直に変更する）。

## 公開 API 案（最小）

- `P.<name>(**kwargs)`:
  - `<name>` は preset の公開名（基本は関数名）。
  - 実体は `@preset` が返す wrapper（= 既存の GUI 連携つき関数）を呼ぶだけ。
- 例:
  - `@preset(...)` で `def logo(...): ...` を定義
  - `P.logo(scale=2.0, ...)` で呼ぶ

## 仕様を先に決めたい点（要確認）

- `@preset(op=...)` を残す？
  - 残す場合、`P.<name>` はどの op に解決する想定にする？（`preset.<name>` 固定か、op 任意か）
- `P` の名前解決は「関数名のみ」でよい？
  - 例: `@preset(op="preset.my_logo") def logo(...): ...` のとき `P.logo` と `P.my_logo` のどっちを正にするか。
- Parameter GUI の snippet 出力:
  - 現状は `logo(...)` のように “素の関数呼び出し” を生成する。
  - `P.logo(...)` を生成する方針に変える？（P を導入するなら揃えたいが、既存テスト/UXに影響）

## 実装チェックリスト

- [ ] `preset_registry` と別に「呼び出し可能な preset 本体」を保持するレジストリを用意する（例: `preset_func_registry: dict[str, Callable[..., Any]]`）
- [ ] `@preset` デコレータで、GUI 用 spec 登録（既存）に加えて callable 登録も行う
  - [ ] `src/grafix/api/preset.py` 内の `ParamSpec("P")` が新しい公開変数 `P` と衝突するのでリネームする（例: `_PSpec`）
- [ ] `P` 名前空間（PresetNamespace）を追加する
  - [ ] `src/grafix/api/presets.py`（新規）に `PresetNamespace` + `P = PresetNamespace()` を置く
  - [ ] `__getattr__` で未登録なら `AttributeError`（G/E と同じ）
  - [ ] `_` 始まりは拒否（G/E と同じ）
- [ ] `grafix.api` / `grafix` ルートから `P` を公開する
  - [ ] `src/grafix/api/__init__.py` の `__all__` に追加
  - [ ] `src/grafix/__init__.py` の `__all__` に追加
- [ ] 型スタブ同期
  - [ ] `tools/gen_g_stubs.py` を更新して `src/grafix/api/__init__.pyi` に `P` を含める
  - [ ] `tests/stubs/test_api_stub_sync.py` が通る状態にする
- [ ] テスト追加/更新
  - [ ] `tests/api/` に `P.logo(...)` で `ParamStore` 連携が動く最小テストを追加
  - [ ] snippet を `P.<name>` へ変更するなら `tests/interactive/parameter_gui/test_parameter_gui_snippet.py` を更新
- [ ] ドキュメント更新
  - [ ] `README.md` の “Optional features” / “Extending” に `P` の説明と例を追加

## 追加で気づいた点（提案）

- `preset_registry` が “op -> spec” のみなので、`P.<name>` 実現には「name -> callable」マップが別途必要。
  - ここを `preset_registry` に統合するか、別レジストリにするかで実装の単純さが変わる（統合の方がシンプル）。

