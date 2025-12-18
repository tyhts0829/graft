# gen_g_stubs 実装計画（2025-12-18）

目的: `grafix.api` の動的名前空間 `G` / `E` に対して、IDE 補完が効く型スタブを「1 回の実行」で自動生成できるようにする。

対象: 旧ツール `tools/from_previous_project/gen_g_stubs.py` を現プロジェクト仕様（`grafix.api` + `primitive_registry` / `effect_registry`）に合わせて作り直す。

成果物（案）:

- 生成ツール: `tools/gen_g_stubs.py`（`python -m tools.gen_g_stubs` で実行）
- 生成スタブ: `src/grafix/api/__init__.pyi`（`from grafix.api import G, E` の補完改善）
- （任意）同期テスト: `tests/stubs/test_api_stub_sync.py`

非目標:

- 完璧な型推論（必要十分で良い）
- `from_previous_project/` 系の primitive/effect の網羅（未移行・依存不足があり得る）
- ユーザー定義 primitive/effect の自動検出（必要なら「生成前に import して registry 登録」が基本）

## 0) 事前に決める（あなたの確認が必要）

- [x] 出力先は `src/grafix/api/__init__.pyi`（`typings/` ではなく、公開 API モジュールに同居）
- [x] 生成対象は「`grafix.api.primitives` / `grafix.api.effects` の import 副作用で registry に登録されるもの」に限定する（全探索 import はしない）
  - 追加: Layer helper（`grafix.api.L`）もスタブに含める（IDE 補完）
  - 理由: `grafix.core.*` を全探索 import すると `from_previous_project/` が混ざって ImportError になる可能性がある
- [x] 署名の厳密さ: 既知引数の末尾に `**_params: Any` は付けない（typo を拾いやすくする）
- [x] スタブに docstring（ツールチップ用）を埋め込む
- [x] 旧ファイル `tools/from_previous_project/gen_g_stubs.py` は放置（削除/移動しない）

## 1) 生成するスタブの仕様（案）

方針: `grafix.api` の「実行時は動的だが、IDE では静的に見せたい」部分だけを `.pyi` で補う。

- `G`（primitive）:
  - `class _G(Protocol)` を生成し、登録済み primitive 名ごとにメソッドを列挙
  - 例: `def sphere(self, *, radius: float = ...) -> Geometry: ...`
  - `G: _G` として公開
  - `G(name="...")` のラベル用途のため `def __call__(self, name: str | None = None) -> _G: ...` も含める
- `E`（effect）:
  - `class _EffectBuilder(Protocol)` を生成し、登録済み effect 名ごとのチェーン用メソッドを列挙
  - `def __call__(self, geometry: Geometry) -> Geometry: ...` を含める
  - `class _E(Protocol)` を生成し、登録済み effect 名ごとの「ビルダ生成メソッド」を列挙（戻り値は `_EffectBuilder`）
  - `E: _E` として公開
  - `E(name="...")` のラベル用途のため `def __call__(self, name: str | None = None) -> _E: ...` も含める
- `L`（Layer helper）:
  - `grafix.api.__init__.pyi` は `__init__.py` を覆うため、`L` もスタブ側で型を持って再公開する
  - `grafix.api.layers.LayerHelper` のシグネチャに一致させる（`L(...) -> list[Layer]`）
- 型の決め方（シンプル案）:
  - `ParamMeta.kind` から型を決める（不足時は `Any`）
  - `vec3` は見通し改善のため `Vec3: TypeAlias = tuple[float, float, float]` をスタブ内に定義して使う
  - `choice` は `str` 扱い（`Literal[...]` まではやらない）
- 署名の形:
  - すべて keyword-only（先頭に `*` を付与）
  - default は安全に `= ...` で固定（実値を書かず、API 面を安定化）
  - 並びは `sorted(names)` で常に安定
- docstring の方針:
  - 可能なら実装関数の docstring から summary/引数説明を抽出して貼る
  - 取れない場合は `ParamMeta` の kind/range/choices を短く列挙して代替する
- スタブ先頭に以下を入れる:
  - 自動生成コメント
  - 再生成コマンド（`python -m tools.gen_g_stubs`）

## 2) `tools/gen_g_stubs.py` 実装方針（案）

- [ ] `tools/gen_g_stubs.py` を新規作成（旧ツールは参照用に残す）
- [ ] 未インストールでも 1 コマンドで動くように、`repo_root/src` を `sys.path` に追加
- [ ] `grafix.api.primitives` / `grafix.api.effects` を import して registry を初期化
- [ ] `grafix.api.layers` を import して `L` の公開シグネチャをスタブへ反映
- [ ] `grafix.core.primitive_registry.primitive_registry` から primitive 名を列挙
- [ ] `grafix.core.effect_registry.effect_registry` から effect 名を列挙
- [ ] `get_meta(name)` のキー順（定義順）を維持して引数順を作る（`dict` は挿入順保持）
- [ ] `ParamMeta.kind -> 型文字列` を変換してシグネチャを組み立てる
- [ ] docstring を生成（実装 docstring から抽出 or `ParamMeta` から合成）
- [ ] 生成文字列 API:
  - [ ] `generate_stubs_str() -> str`（テスト/差分確認用）
  - [ ] `main() -> None`（ファイル書き込み）
- [ ] 出力パス: `src/grafix/api/__init__.pyi`
- [ ] `grafix.api.__init__.py` の `__all__` と整合する export を `__init__.pyi` に含める（`G/E/L/run/Export/primitive/effect`）

## 3) テスト（任意だが推奨）

目的: public API が増減した時にスタブの更新漏れを確実に検知する。

- [ ] `tests/stubs/test_api_stub_sync.py` を追加
  - `tools.gen_g_stubs.generate_stubs_str()` と `src/grafix/api/__init__.pyi` の完全一致を検証
- [ ] 実行: `PYTHONPATH=src pytest -q tests/stubs/test_api_stub_sync.py`

## 4) 運用（最小）

- [ ] 再生成コマンドを `README.md` か `TODO.md` に 1 行追加
- [ ] primitive/effect の追加・削除時は「スタブ再生成 → 同期テスト更新（必要なら）」をルール化

## 5) 追加で確認したいこと（気づき）

- [ ] 現状 `src/grafix/api/primitives.py` が `circle` を import していないため、`G.circle()` が未登録の可能性がある。
  - スタブ生成は「registry をソース」にするため、ここが未登録ならスタブにも出ない。
  - 期待が「組み込みは全部 G に生える」なら、API 側の import リスト整理が先に必要。
- [ ] `src/grafix/core/effects/from_previous_project/` は `common` 依存などが見えるため、全探索 import は危険。public API 起点で生成するのが安全。
