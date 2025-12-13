# どこで: `docs/parameter_gui_no_args_defaults_checklist.md`。
# 何を: `G.circle()` のように「引数なし」で呼んでも Parameter GUI にスライダー等が出るようにする改善計画。
# なぜ: 現状は「ユーザーが明示的に渡した kwargs のみ」しか ParamStore に観測されず、GUI が空になり得るため。

## 背景（現状の原因）

- GUI が表示する行は `ParamStore.snapshot()` に入っているものだけ。
- `snapshot` はフレーム内の `resolve_params()` の記録（FrameParamsBuffer）を `store.store_frame_params()` が取り込むことで増える。
- しかし `G.circle()` のように kwargs が空だと `resolve_params()` のループが回らず、記録が 0 件になり、結果として GUI が空になる。

## ゴール

- `python main.py` の `draw()` が `G.circle()` のように引数なしでも、GUI に `r/cx/cy/segments` 等が表示される。
- 表示された値は次フレーム以降の描画へ反映される（既存の ParamStore/override 仕様を維持）。

## 非ゴール

- Layer の color/thickness など「Layer スタイル」を ParamStore に載せる（別タスク）。
- “Style/Layer/label header” セクションの UI 実装（別タスク）。

## 方針（採用）

### meta を持つ op について「安全なデフォルト引数」を自動で観測する

- 前提: 組み込み primitive/effect は meta を持つ（GUI 対象を meta で明示できる）。
- 実装イメージ:
  - registry（primitive/effect）に「関数シグネチャ由来のデフォルト値」を保持する。
  - API 層（`G`/`E`）で、ユーザーが省略した引数をデフォルト値で補完してから `resolve_params()` に渡す。
  - GUI は `store.snapshot()` から従来どおり描画（追加の特別扱いを増やさない）。

## 契約（確定）

- [x] meta に含まれる引数のデフォルト値として `None` は禁止する
  - `inspect.signature` で抽出した default が `None` の場合は登録時に例外とし、実装側で default を数値/タプル等に置き換える。
  - 例: `src/effects/scale.py` の `s/sx/sy=None` は廃止する（`None` を使わないシグネチャへ整理）。

## 実装チェックリスト

- [ ] 対象の洗い出し
  - [ ] 組み込み primitive/effect の meta と関数シグネチャ（default 値）を一覧化
  - [ ] `None` default を含む引数を特定し、契約違反として修正対象にする（現状は `scale` のみ想定）
- [ ] registry に default 情報を保持
  - [ ] `src/core/primitive_registry.py` に `defaults` 保存と `get_defaults(op)` を追加
  - [ ] `src/core/effect_registry.py` に `defaults` 保存と `get_defaults(op)` を追加
  - [ ] デコレータ（`primitive`/`effect`）で、元関数の `inspect.signature` から default 値を抽出して登録
  - [ ] meta に含まれる引数の default が `None` の場合は `ValueError`（早期検知）
- [ ] API 層で「省略引数の補完」を入れる（meta がある op のみ）
  - [ ] `src/api/primitives.py`: `factory(**params)` で `defaults` を `params` にマージしてから `resolve_params()` を呼ぶ
  - [ ] `src/api/effects.py`: `EffectBuilder` の解決前に `defaults` を `params` にマージしてから `resolve_params()` を呼ぶ
- [ ] 組み込み effect の契約違反を解消する
  - [ ] `src/effects/scale.py` の `None` default を廃止し、meta と整合する default を持つ形に整理する
- [ ] テスト追加（最小）
  - [ ] `tests/parameters/test_defaults_autopopulate.py`（新規）
    - [ ] `parameter_context` 内で `G.circle()` を呼ぶと `store.snapshot()` に `r/cx/cy/segments` が入る
    - [ ] GUI 表示対象は meta があるキーのみ、という既存仕様は維持される
    - [ ] meta に含まれる引数の default が `None` の場合に登録で失敗する（契約のテスト）
- [ ] ドキュメント更新
  - [ ] `docs/parameter_gui_phase3_checklist.md` に「引数省略でも GUI が埋まる」ことを反映
  - [ ] `parameter_spec.md` など、必要なら注意点（override の挙動）を追記

## 完了定義

- `main.py` の `draw()` が `G.circle()` でも、GUI にパラメータ行が表示される。
- 既存テストが通り、追加テストが green。
