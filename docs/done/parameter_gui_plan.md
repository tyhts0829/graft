# parameter_gui_plan.md

どこで: `docs/parameter_gui_plan.md`。対象コードは `src/parameters/*`, `src/api/*.py`, `src/app/*`, `src/core/geometry.py`, `src/core/*registry.py` などパラメータ経路に関わる部分（MIDI は `src/midi/` に将来分離）。
何を: `parameter_spec.md` の 8–9 章に沿って Parameter GUI のバックエンドと GUI 土台を実装する計画をまとめる（MIDI/CC の実装は今回の範囲外）。
なぜ: 動的に発見したパラメータを GUI から安全に上書きできる基盤を整え、後続の UI 実装や MIDI 連携を段階的に載せられるようにするため。

## 0. ゴールとスコープ
- `G` / `E` 呼び出しで ParameterKey と meta を付与し、draw 単位でパラメータを動的発見・スナップショット化できる。
- ParamState（override/ui_min/ui_max/ui_value 等）を管理する ParamStore を実装し、GUI からの書き込み・読み出しを提供する。
- 画面更新ごとに param_snapshot / frame_params を contextvars で固定し、Geometry 生成時に base→GUI override 優先で値を解決する（CC はダミー/未実装でよい）。
- GUI 側が使う 3 列モデル（ラベル/コントロール/ui_min-ui_max-cc）に供給するデータ整形 API を用意する（UI 実装自体は別タスクで可）。
- MIDI/CC 信号の入力処理や mido 連携は今回含めない。CC 番号を保持しても値は常に 0 として扱うか、未割当扱いにする。

## 1. 現状整理（2025-12-10 時点）
- `G` / `E` は単純に Geometry を生成するだけで、callsite 情報や meta を持たない。
- `Geometry.create` は default_step=1e-6 で正規化するのみで、パラメータ解決フックがない。
- `run` と `render_scene` はフレームごとのパラメータコンテキストを意識していない。
- ParamStore/GUI/MIDI 関連モジュールは未実装。

## 2. 設計方針（Phase 1: GUI override のみ）

### 用語リファレンス
- コンテキスト（parameter_context）: 1 フレームの間だけ有効な論理スコープ。`contextvars` で `param_snapshot`（読み取り専用のパラメータ状態）と `frame_params`（当該フレームのパラメータ記録バッファ）、`cc_snapshot`（MIDI CC 値のスナップショット・今回は空/None）を束ね、同一フレーム内で値がぶれないよう固定する。
- ParameterKey / site_id: GUI 行を一意に識別するキー。`(op, site_id, arg)` で構成し、`site_id` は `"{filename}:{co_firstlineno}:{f_lasti}"` 形式で呼び出し箇所を表す。
- base_value: ユーザーコードが `G` / `E` に渡した元の引数値（デフォルト引数が使われた場合はその値）。
- ui_value: GUI で手動設定された値。`override=True` のときにのみ候補として使われる。
- effective_value: 「CC > GUI override > base」の優先順位で決め、型検証・量子化後に Geometry 生成へ渡す最終値。
- 正規化（canonicalize/normalize）: ParamMeta に従い値の型をそろえ、必要に応じて step 量子化を行い、Geometry.create に渡す前の決定値を作る処理。署名計算と実計算に同一の値を使うための前段（UI レンジによるクランプは行わない）。
- ParamMeta: UI 型・ui_min/ui_max・step などのメタ情報（ui_min/ui_max はスライダーの初期レンジであり実値をクランプしない）。**組み込み primitive/effect では必須**（未定義なら例外）。ユーザーが自作 primitive/effect を登録する場合のみ、未指定なら推定フォールバックで補う。
- ParamState: 各 ParameterKey に紐づく状態（override, ui_value, ui_min, ui_max, cc）。GUI の保存・復元単位。ui_min/ui_max は UI レンジとしてのみ利用する。
- ParamStore: `dict[ParameterKey, ParamState]` を保持する永続ストア。序数 `ordinal`（op ごとに初出順）も管理し、JSON で保存/復元可能。
- ordinal: 同一 op 内で最初に観測した順に 1,2,3… を付与し、GUI の行ラベルに使う。
- frame_params（フレーム内バッファ）: `draw(t)` 実行中に G/E が呼ばれるたび、ParameterKey と base/ParamMeta を記録し、値解決後に effective と source（`"base"` / `"gui"` / `"cc"`）を追記する一時ログ。フレーム終了後に ParamStore へマージする。
- マージ: frame_params のログを ParamStore に反映する処理。未登録の ParameterKey は新規に ParamState を作成し、`override=False`・`ui_value=base_value`（正規化後）・`ui_min/ui_max=ParamMeta.ui_min/ui_max` で初期化し、既存キーは上書きせず保持する。GUI 側はこの結果をもとに行を生成・更新する。
- cc_snapshot: フレーム開始時点の MIDI CC 値スナップショット。Phase 1 では空/Noneで、CC が割り当てられても実際の値は使用しない（将来拡張用）。
- cc_value: `ParamState.cc` が指す CC 番号に対して `cc_snapshot` から取り出した 0..1 の値。Phase 1 では未使用だが将来の CC 制御で effective_value を決める入力になる。

### モジュール配置（決定）
- `src/parameters/`: パラメータ解決の中核（context, key, meta, state, store, frame_params, resolver, viewmodel）。  
- `src/midi/`: MIDI CC 入力やマッピング（今回のスコープ外、将来追加）。  
- `src/app/parameter_gui.py`: GUI イベントハンドラや ParamStore との橋渡し（DearPyGui 実装は後続）。  
- `src/api/primitives.py` / `src/api/effects.py`: Geometry 生成前の解決フックを呼び出す。  
- `src/api/run.py`: フレームごとに parameter_context を開始・終了し、frame_params を ParamStore にマージする。
- **ParameterKey & site_id**: `sys._getframe(1)` から `filename`, `co_firstlineno`, `f_lasti` を取り出し `"{filename}:{co_firstlineno}:{f_lasti}"` 形式で site_id を生成。`G.<name>` 呼び出しと `EffectBuilder` チェーンの各ステップ追加時に保持し、`Geometry.create` 呼び出し時に key を渡せるようにする。
- **ParamMeta**: registry に meta をオプション登録できるよう拡張（`@primitive(meta=...)` / `@effect(meta=...)` など）。未指定は base 値から推定するフォールバックを `meta.infer()` にまとめる。ただし組み込み primitive/effect では必須。
- **ParamState/ParamStore**: `dataclass ParamState`（override/ui_value/ui_min/ui_max/cc）。`ParamStore` は `dict[ParameterKey, ParamState]` を管理し、JSON で保存/復元できる API を持たせる。
- **frame_params**: draw 開始時に生成し contextvar に載せ、G/E で `record(key, base, effective, source, meta)` を push するだけのシンプルなバッファにする。draw 終了後に ParamStore へマージする。
- **コンテキスト固定**: `with parameter_context(frame_no, store, cc_snapshot=None)` のようなコンテキストマネージャを用意し、`param_snapshot` と `frame_params` を contextvars にセット。`cc_snapshot` はダミー dict を渡すか None を許容（MIDI 後回し）。
- **値解決**: `resolve_arg(key, base_value, meta, snapshot)` で「CC > override > base」優先を実装。ただし Phase 1 は CC 未対応のため `cc` は常に未割当扱いとし、override True のときだけ GUI 値を採用する。量子化/型検証/クランプは meta に従い、`Geometry.create` に渡す前に正規化済み値を作る。param_steps を meta.step から渡して canonicalize と計算を一致させる。
- **GUI 供給 API**: `ParameterViewModel` を返す関数を用意（ラベル/arg/display_name/ui_min/ui_max/ui_value/override/cc/ordinal）。ordinal は op ごとに site_id 初出順で `ParamStore` が保持する。
- **例外処理**: GUI 由来の不正値は clamp または override 無効化で握り、警告イベントを返す hook を用意する（ロギングは後続 UI で表示）。

### フレーム内の処理フロー（Phase 1 想定）
1. フレーム開始  
   - ParamStore から読み取り専用の `param_snapshot` を生成し contextvars にセット。  
   - 空の `frame_params` バッファを作り contextvars にセット。  
   - `cc_snapshot` は空/None をセット（Phase 1 は CC 未使用）。
2. G/E 呼び出し時  
   - base と ParamMeta を `frame_params` に記録。  
   - `param_snapshot` を参照して「CC > override > base」で値を解決し、effective と source を同じレコードに追記。  
   - その effective を Geometry.create に渡し、署名・計算に使用する。描画に用いる値はこの時点で確定し、ParamStore には依存しない。  
3. フレーム終了後  
   - `frame_params` を ParamStore にマージ。新規キーは ParamState を初期化（override=False, ui_value=base の正規化値, ui_min/ui_max=ParamMeta 既定, cc=None）、既存キーは保持（必要に応じて meta を更新）。  
   - 更新された ParamStore は次フレームの `param_snapshot` 生成と GUI 表示に利用される。  

補足: ParamStore は「永続ストア／GUI 用デフォルトの保管場所」であり、描画で使う実値はフレーム開始時にスナップショット化した `param_snapshot` と呼び出し時の解決結果で完結する。ParamStore を「ログ」と呼ばないのは、過去の履歴を積むのではなく最新状態を保持するため。

## 3. タスク分解（チェックリスト）
- [x] `src/parameters/` パッケージ新設（`__init__.py` + contextvar 定義）。
- [x] `key.py`: ParameterKey 型と site_id 取得ヘルパを実装。EffectBuilder が step_site_id を保持するよう `src/api/effects.py` を改修。
- [x] `meta.py`: ParamMeta 定義と推定ロジック。primitive/effect デコレータで meta 登録を受け付けるよう `primitive_registry` / `effect_registry` を拡張。
- [x] `state.py` / `store.py`: ParamState と ParamStore（JSON load/save、ordinal 管理）を実装。
- [x] `frame_params.py`: FrameParamRecord/frame_params バッファ（meta を含む）と ParamStore へのマージ処理を実装。
- [x] `resolver.py`: base→override 優先で値を決め、meta.step を `Geometry.create` に反映させるフックを提供。CC は未実装扱いでスキップ。
- [x] `src/api/primitives.py` / `src/api/effects.py`: Geometry 生成前に `resolve_params(op, params, meta, param_snapshot, frame_params)` を呼ぶようにし、ParameterKey を各引数に紐づけて記録。
- [x] `src/api/run.py`: フレーム開始時に `parameter_context(frame_no, store, cc_snapshot=None)` をセットし、終了後に `frame_params` を store へマージするフックを追加。フレーム番号管理を run 内部に持たせる。
- [ ] `src/app/parameter_gui.py`（骨組み）: ParamStore を受け取り GUI へ渡すための `get_rows()` / `apply_ui_update()` のような薄い I/O 層を定義（DearPyGui 実装は TODO に留める）。
- [x] テスト: `tests/parameters/test_site_id.py`, `tests/parameters/test_resolver.py`, `tests/parameters/test_frame_params.py` を追加し、以下を検証（MIDI はスキップ）:
  - site_id の安定性（同一行で一致、行移動で変化）。
  - 優先度と量子化（override > base、step 量子化、UI レンジによるクランプなし、型不一致フォールバック）。
  - frame_params ↔ ParamStore のマージ（新規キー初期化、既存キー保持、meta 更新の扱い）。
  - contextvars の隔離（フレーム間で snapshot/バッファが漏れない、スレッド分離）。
  - ParamStore の JSON 保存/読込で ordinal/override/ui_value/ui_min/ui_max/cc が保持されること。
  - ダミー primitive を通じて resolve→Geometry.create に meta.step が反映されること。

## 4. 後続/非ゴール
- MIDI/CC 入力の購読と `cc_snapshot` 更新（mido 統合、スレッド安全キュー）。
- DearPyGui を使った実際のウィジェット配置・イベントループ統合。
- HUD/ログ出力や無効入力の警告表示。
- 複数プロファイル保存・ロード UI、ホットリロード連携。

## 5. リスク・要確認
- site_id 生成のコストが多量の G/E 呼び出しでボトルネックにならないか（必要なら LRU キャッシュを検討）。
- contextvars と pyglet イベントループのスレッドモデル整合（描画スレッドが 1 本なら問題なし）。
- meta 推定のレンジ/step が不適切だと GUI 体験を損なう可能性があるため、テストで代表値を押さえる。
- Geometry.create の量子化幅を meta 経由で変える際、既存ジオメトリ ID と互換性が崩れる点を周知（キャッシュ破棄を想定）。
