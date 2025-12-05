コンセプト
A Python-based creative coding framework with an audio mindset.

- api

  - G: 登録済みプリミティブを呼び出す
  - E: 登録済みエフェクトを呼び出す
  - L: G を引数にとり、その線の太さと色を指定できる
  - run: draw 関数を第一引数に取る。その draw 関数は毎フレーム描画される。使用する CPU の数、キャンバスサイズ、描画スケール、デフォルトの線の色、背景色を指定できる。
  - cc: PC に繋がれた midi コントローラーを自動で検知し、接続され、その cc データ。cc[0]といった場合、cc 番号 0 の float 値を取り出せる。
  - LFO: lfo ファクトリ。LFO(wave="sine") → lfo が生成される。lfo は時間 t, amp, freq, phase を引数に取り、float0.0-1.0 を返す。
  - ENV: envelope ファクトリ。ENV() → env が生成される。env は時間 t,ブール, タプル adsr を引数に取り、float0.0-1.0 を返す。
  - geometry: ユーザーが Geometry を定義するためのデコレータ
  - effect: ユーザーが Effect を定義するためのデコレータ
  - lfo: ユーザーが LFO を定義するためのデコレータ
  - env: ユーザーが ENV を定義するためのデコレータ

- draw 関数

  - t: float 時間を引数に取る
  - Geometry, Layer, またはその混合リストを返す。Geometry は裏でデフォルトの Layer に包まれる。
  - ユーザーは draw(t: float)内で、api.G: Geometry: プリミティブ形状や、やそれに作用する api.E: Effect: Geometry 加工関数を用いて描画を行う。
    api からインポートできる@geometry, @effect デコレータでユーザー定義が可能。

- Geometry

  - 描画される線の情報を格納する。
    - 属性
      - coords (N, 3) float32: すべての点列を連結した配列
      - offsets (M+1, ) int32: 各ポリラインの開始 index
      - id: 生成時に付与される。キャッシュ用。
    - 詳細
      - +演算子をサポートし、一つの Geometry に統合できる。
      - すべてのプリミティブは大きさが 1 程度である。

- Effect

  - Geometry を第一引数にとる。
  - それ以外に任意の型の引数をとる。
  - 関数であり、加工された Geometry を返す。かならず新しいインスタンスを返す。
  - 各 Effect モジュールは独立しており、他の Effect を import したりしない。
  - Geometry の id と、引数の値（第一引数以外に Geometry が来る場合はその id）から id を持つ。
    - 引数が float なら小数点以下 3 桁で量子化する。
    - それらの情報でキャッシュを行う。

すべての Geomerty と Effect は lazy である。描画の直前に実体化される。

- Layer

  - Geometry を第一引数にとる。
  - 線の色と太さを引数で指定できる。
  - G-code は Layer ごとに別ファイルで保存される。
  - Geometry の id を見て、描画が変わらなければ、IBO キャッシュ など描画までのプロセスで効率化を図る。

- GUI

  - draw 関数内で呼ばれているすべての Geometry と Effect の引数(パラメータ)をスライダーから操作できるようにする。
  - draw 関数内で引数が指定されていた場合はその値で描画されるが、GUI から操作された時点で制御が GUI に移る。

- Keyboard shortcut
  - P でスクリーンショットを保存
  - V で動画を保存
  - G でその瞬間の描画の G-code を保存
  - H で描画ウィンドウに HUD をトグル表示。CPU 使用率、vertices 数、line 数、キャッシュ状態、メモリ使用量を文字とメーターで表示。
  - S で現在の描画パラメータを保存する。次に同名のスケッチを起動したときに、その状態を復帰する。

1. 目的とスコープ

- 目的
  - 時間とコントローラ入力に応じて「線描ベースの幾何」を生成・変形し、リアルタイムに描画・録画・エクスポートするためのフ
    レームワーク。
- 前提
  - 公開入口は Python パッケージ api（src/api/**init**.py）と、典型的なスクリプト実行 (python main.py)。
  - ここで列挙する「外部から観察可能な挙動」はリライト後も維持する（互換性の対象）。
  - DB は無く、永続化はファイルベースのみ（JSON/YAML/画像/G-code/動画）。

———

2. 外部仕様（入出力と振る舞いの固定点）

2.1 Python API（api パッケージ）

- 公開シンボル（from api import ...）
  - G: 形状ファクトリ（遅延仕様を返す）。G.<name>(\*\*params) で登録済み geometry を呼び出す。
    - 未登録名 → AttributeError。
    - 返り値は「遅延幾何仕様」(LazyGeometry 相当) であり、幾何の実体は終端で評価される。
  - E: エフェクト・パイプラインファクトリ（src/api/effects.py:1）。
    - E.<effect>(\*\*params) → その単一ステップを持つパイプラインビルダ。
    - E.pipeline.<effect1>(...).<effect2>(...).build() → パイプライン定義。
    - builder(g) または builder.build()(g) → 遅延仕様に plan を積む（または一括実行）。
  - L: レイヤー構築ヘルパ（src/api/layers.py:1）。
    - L(geometry, color=..., thickness=..., name=..., meta=...) → 1 レイヤー。
    - L.of([g1, g2], color=..., ...) → それぞれを同条件でレイヤー化。
    - 返り値はレンダラが理解する「レイヤー列」。
  - cc: 現在フレームの MIDI CC スナップショット（src/api/cc.py:1）。
    - cc[i] → 0.0..1.0 の float。未定義は 0.0。
    - cc.raw() → dict[int, float] の浅いコピー。
  - C: 現在のカラーパレットビュー（src/api/palette.py:1）。
    - len(C) → 色数（パレット未設定時 0）。
    - C[i] → (r,g,b,a) in 0.0..1.0。範囲外 → IndexError。
    - C.colors_rgba() / C.hex() / C.export(fmt) で一括取得やエクスポート。
  - lfo: LFO ファクトリ（src/api/lfo.py:1）。
    - lfo(wave=..., freq=..., period=..., lo=..., hi=..., ...) → **call**(t: float) -> float なオブジェクト。
    - 波形種別やパラメータが不正な場合は ValueError。
  - geometry, effect: ユーザー拡張のデコレータ（src/geometrys/registry.py:1, src/effects/registry.py:1）。
    - 関数以外に付けると TypeError。
    - 登録後は G.<name> / E.<name> で利用可能。
  - run, run_sketch: スケッチ実行ランナー（src/api/sketch.py:1）。
    - run は run_sketch のエイリアス。
    - シグネチャ・挙動は run_sketch に準拠。
  - Geometry: 幾何値オブジェクト（src/engine/core/geometry.py:1）。
    - ユーザーが直接使うことを前提とした API（from_lines, concat, 座標取得など）を維持する。
- G の仕様（観察可能な点）
  - G.<geometry>(\*\*params):
    - 引数は形状実装が期待する「実値」（mm, 度数, カウントなど）。
    - 返り値は「遅延仕様」。+ などの演算子や .scale/.translate 等で幾何値を組み合わせられる（現行互換）。
    - G.from_lines(lines) → 任意のポリライン列から Geometry を生成。
      - lines は list/tuple/ndarray の混在可。異常な形式は ValueError。
    - G.empty() → 空幾何（頂点 0, offsets=[0]）。
  - 量子化・署名
    - キャッシュ鍵のために、パラメータは **param_meta**['step'] または PXD_PIPELINE_QUANT_STEP に基づき量子化される。
    - 実際の geometry 呼び出しに渡される実値は「ランタイム解決値」で、不変条件:
      - RangeHint は GUI 表示上のみのクランプ。geometry 実装にはクランプ済み実値が渡る。
- E の仕様
  - E.affine().fill().displace(...).mirror() のようにチェーン可能。
  - builder.cache(maxsize=None|0|N) でパイプライン出力キャッシュ（署名ベース）を有効化。
    - 0 → 無効、None → 無制限、N>0 → LRU サイズ N。
    - 既定値は設定/環境変数から解決（PXD_COMPILED_CACHE_MAXSIZE 等）。
  - Pipeline.**call**(g):
    - Geometry / 遅延仕様いずれも受け取り、plan を追加した遅延仕様か、直ちに評価した Geometry を返す（realize() の有無
      で決まる）。
    - 入力が不正型の場合は TypeError 近似の例外を送出（現実装準拠）。
  - Effect ごとのパラメータも **param_meta** による量子化ルールに従う。
- cc の仕様
  - フレームごとにランタイムからスナップショットが差し替えられる。
  - cc[i] は例外を出さず常に float を返す（入力が非数値/範囲外でも 0.0 になる）。
  - repr(cc) は cc{1: 0.123456, ...} のような読みやすい形式。
- C（パレット） - パレットが未設定・壊れている場合のふるまい: - len(C) → 0。 - C[i] → IndexError。 - colors_rgba() / hex() / export() → 空リスト。

  2.2 スケッチ実行 API (run_sketch / run)

- user_draw の契約
  - シグネチャ: user_draw(t: float) -> Geometry | LazyGeometry | Layer | Sequence[...]。
  - 戻り値の許容形:
    - 単一 Geometry または遅延仕様。
    - 単一 Layer。
    - 上記の list/tuple（混在可）。
  - CC は from api import cc; cc[i] で参照する（user_draw(t, cc) のような引数では渡さない）。
  - ユーザーコードから見える不変条件:
    - user_draw は「与えられた t と cc スナップショット」に対して決定的であることが期待される（キャッシュ・録画の
      前提）。
- run_sketch(...) の主要引数と挙動（src/api/sketch.py:1）
  - canvas_size: "A5" などのプリセット名 or (width_mm, height_mm)。
    - 無効なプリセット名は ValueError（現実装に準拠）。
  - render_scale: >0 の float。
    - <=0 の場合は ValueError。
    - ウィンドウ解像度は canvas_size(mm) \* render_scale (px/mm) を丸めた整数。
  - fps:
    - None → configs/default.yaml / config.yaml から canvas_controller.fps を読み、未設定時は 60。
    - 最終的に >=1 にクランプ。
  - line_thickness, line_color, background:
    - API 引数が優先され、指定無しのときは設定ファイルの既定を使用。
    - 色は "#RRGGBB", (0..1), (0..255) などを受け付け、RGBA に正規化（util.color.normalize_color 規約）。
  - workers:
    - > =0 にクランプ。0 → マルチプロセスを使わずメインプロセス内で評価。
  - use_midi:
    - True で MIDI 初期化を試みる。依存欠如やポート未接続時は「警告ログ + Null 実装」で継続。InvalidPortError は HUD/
      ログに出るがアプリは落とさない。
  - use_parameter_gui:
    - True で Dear PyGui ベースの Parameter GUI を起動。False で一切起動しない。
  - init_only:
    - True の場合、重い依存（pyglet / ModernGL 等）の初期化・ウィンドウ生成を行わず、設定解決・MIDI/GUI 初期化の成功可
      否だけを確認して即終了。
- キーボードショートカット（on_key_press の観察仕様） - ESC: スケッチ終了。Parameter GUI/MIDI/GL をクリーンアップしてウィンドウを閉じる。 - P: 画面そのまま PNG として保存（HUD 含む）。 - Shift+P: 高品質 PNG（HUD なし）。オフスクリーン描画でラインのみ保存。 - G: 現在フレームの Geometry を G-code 変換してバックグラウンドで保存。 - Shift+G: G-code エクスポート中断（ジョブキャンセル）。 - V: Video 録画開始/停止トグル。 - 開始: 画面そのまま（HUD 含む）を MP4 に保存。 - 停止: MP4 ファイルパスを HUD に表示。 - Shift+V: 品質最優先モード（HUD なし録画: FBO 経由）で録画を開始。録画中はウィンドウへの描画を抑え、HUD に「品質最優
  先モード」を表示。停止時に通常モードへ戻す。 - H: HUD の表示 ON/OFF をトグル（Parameter GUI があれば runner.show_hud を override）。

  2.3 GUI（HUD & Parameter GUI）

- HUD（src/engine/ui/hud/overlay.py:1 前提）
  - 常に FPS / 頂点数 / CPU / MEM 等のメトリクスを表示するオーバーレイ。
  - 既定オン。run_sketch(show_hud=False) で無効化可。
  - 動作中に H キーでトグル可能（Parameter GUI が無くても HUD 単体で管理）。
  - フォント/色設定は YAML の hud セクションからロード（configs/default.yaml:60 付近）。
- Parameter GUI（src/engine/ui/parameters/\*.py） - 役割 - geometry / effect の引数、およびランナー・HUD・パレット関連パラメータを自動で列挙し、スライダー・チェックボックス・
  コンボ等として表示。 - 値はフレーム毎に ParameterRuntime を介して G / E 呼び出しに注入される。 - GUI からの変更は一時的 override として記録され、終了時に JSON で保存される。 - レイアウト仕様 - 各 G.<geometry>() 呼び出しごとに「geometry セクション」。 - ラベル未指定: geometry 名 (text, sphere など)。同名複数呼び出し時は text, text_1, ...。 - G.label("title").text(...) のように label を付けると title, title_1, ... として表示。 - 各パイプラインは「pipeline セクション」としてまとめられる。E.pipeline.label("name") で表示名を指定可。 - Style/Palette/HUD 関連は Style, Palette カテゴリとして別セクション。 - 値解決の優先順位 - GUI override > 明示引数（コード上の値） > 既定値。 - MIDI → GUI の自動上書きはしない（CC は api.cc を通じて draw 内で使用）。 - 永続化 - 保存先: data/gui/<script_stem>.json（parameter_gui.state_dir で上書き可）。 - 保存内容: - overrides: original から変化した値のみ（float は step に従い量子化して比較）。 - cc_bindings: パラメータ ID と CC 番号の対応。 - ranges: GUI 上の min/max オーバーライド。 - 起動時にロード → descriptor に合致するもののみ適用。 - 終了時に再保存。

  2.4 MIDI 入力

- 入力デバイス検出・接続（src/engine/io/manager.py:1）
  - config.yaml/configs/default.yaml の midi_devices に基づきポート名部分一致でデバイスを探す。
  - 見つからないポートはスキップ。InvalidPortError はログ出力に留め、ランナーは継続（MIDI 無し）。
- CC の解釈と正規化（src/engine/io/controller.py:1）
  - 14bit モード: MSB/LSB から 0..16383 を合成 → 0..127 に線形スケール → 0.0..1.0 に正規化。
  - 7bit モード: 0..127 をそのまま 0.0..1.0 にスケール。
  - 各コントローラは DualKeyDict を持ち、cc[番号] / cc["名前"] の両方で参照可。
- CC スナップショットの仕様（src/engine/io/cc_snapshot.py:1）
  - MidiService.snapshot() は Mapping[int,float] を返し、未登録キーは 0.0。
  - ランナーはこれを api.cc.set_snapshot() に渡して cc を更新。
- CC の永続化 - 保存先: io.cc*dir 設定（無ければ CWD/data/cc）。 - ファイル名: <script_name>*<port_name>.json。 - 形式: { "by_name": { "<logical_name>": float(0..1), ... } }。壊れていればゼロ初期化で再生成。

  2.5 ファイル出力（画像 / G-code / 動画 / 状態）

- スクリーンショット（src/engine/export/image.py:1）
  - 保存先ディレクトリ: util.paths.ensure_screenshots_dir() → <repo_root>/data/screenshot/。
  - ファイル名:
    - エントリスクリプト名が分かる場合: <script*stem>*<W>x<H>\_<yymmdd_hhmmss>.png。
    - 不明な場合: <YYYYmmdd*HHMMSS>*<W>x<H>.png。
    - 既存ファイルと衝突した場合は -1, -2, ... を付与。
  - include_overlay=True の場合:
    - pyglet のカラーバッファをそのまま保存（HUD 含む）。
    - scale != 1.0 / transparent=True は NotImplementedError。
  - include_overlay=False の場合:
    - オフスクリーン FBO にラインのみ描画し、その結果を保存。
    - mgl_context と draw コールバックが必須。足りない場合は ValueError。
- G-code エクスポート（src/engine/export/service.py:1, src/engine/export/gcode.py:1）
  - 保存先: <repo_root>/data/gcode/。
  - ファイル名:
    - キャンバス寸法が分かる場合: <name*prefix>*<W>x<H>\_<yymmdd_hhmmss>.gcode。
    - prefix 無し: <YYYYmmdd*HHMMSS>*<W>x<H>\_mm.gcode。
    - 重複時は -1, -2, ...。
  - 非ブロッキング:
    - ExportService が単一ワーカースレッドでジョブキューを処理。
    - 進捗は done_vertices/total_vertices と状態（pending/running/cancelling/completed/failed/cancelled）で観測可。HUD
      にも表示。
  - 失敗時:
    - 一時ファイル .part を削除し、状態を failed にし、error メッセージを残す。
    - G-code 本体の仕様（命令セット）は現時点では未確定であり、将来の実装に委ねられているが、「座標系は mm ベース」を
      仕様として維持。
- 動画（src/engine/export/video.py:1）
  - 保存先: <repo_root>/data/video/。
  - ファイル名: <name*prefix>*<WxH>_<fps>fps_<yymmdd*hhmmss>.mp4（prefix 無し時は <WxH>*<fps>fps\_<ts>.mp4）。重複は -1
    付加。
  - 依存:
    - imageio-ffmpeg or imageio がインポート可能なこと。不能なら録画開始時に RuntimeError。
  - フレーム取得:
    - HUD 含む録画: カラーバッファを RGBA→RGB に変換して書き出し。
    - HUD 無し録画: FBO でラインのみ描画し、必要に応じて MSAA / PBO を利用してフレームを取得。
    - 初期数フレームはドロップしてウォームアップ（色むら防止）。
- Parameter GUI 状態（src/engine/ui/parameters/persistence.py:1） - 形式: { version, script, saved_at, overrides, cc_bindings, ranges }。 - Float/ベクトル値は step に従って量子化され、微小な差分は保存されない。

  2.6 設定ファイル

- 読み込みルール（src/util/utils.py:1）
  - configs/default.yaml → config.yaml（ルート）の順に shallow merge（トップレベルキーのみ上書き）。
  - どちらも無い/壊れている場合は空 dict。
- 主なキー例（configs/default.yaml:1）
  - canvas.background_color, canvas.line_color。
  - canvas_controller.fps。
  - midi.enabled_default, midi_devices（ポート名と CC マップ）。
  - hud._, parameter_gui._, fonts.search_dirs, io.cc_dir など。
- 環境変数による上書き（src/common/settings.py:1 他） - 量子化: - PXD_PIPELINE_QUANT_STEP: float。既定 1e-6。 - 幾何/パイプラインキャッシュ: - PXD_geometry_CACHE_MAXSIZE, PXD_PREFIX_CACHE_ENABLED, PXD_PREFIX_CACHE_MAXSIZE, PXD_PREFIX_CACHE_MAX_VERTS,
  PXD_COMPILED_CACHE_MAXSIZE, PXD_PIPELINE_CACHE_MAXSIZE 等。 - レンダラ/デバッグ: - PXD_IBO_FREEZE_ENABLED, PXD_INDICES_CACHE_ENABLED, PXD_DEBUG_PREFIX_CACHE, PXD_DEBUG_FONTS 等。 - これらは「性能・デバッグ」のためのスイッチであり、論理結果（描画内容・幾何）は変えない前提。

  2.7 例外・ログポリシー（外から見える部分）

- 共通方針
  - API 層は print() を使わず logging を使用（architecture.md:1 の不変条件）。
  - 「ユーザー入力の誤り」は積極的に ValueError / TypeError 等で即座に知らせる。
  - 「環境依存の欠如」（pyglet/ModernGL/mido 未導入など）は基本的に ImportError または RuntimeError として顕在化し、可能
    であれば HUD/ログに分かりやすいメッセージを出す。
- 顕著な例 - 無効な render_scale → ValueError。 - Text シェイプでフォントが見つからない場合 → エラー内容をログ/HUD に表示しつつ、フォールバックフォント/描画スキップ。 - G-code エクスポートで writer 未設定 → HUD にエラーメッセージ（NotImplementedError 相当）。 - Video 録画時に imageio が無い場合 → 録画開始を拒否し、HUD/ログに理由を出す。

  2.8 性能要件（観察可能な期待値）

- フレームレート
  - 既定 fps=60 で、典型的な幾何サイズ（頂点数 <= ~1e5、レイヤー数十程度）では滑らかな描画が維持されることを目標。
  - HUD の vertex_max, line_max（configs/default.yaml:60 付近）がメータ 100% の目安。
- レイテンシ
  - Parameter GUI / MIDI の操作から描画への反映は 1–2 フレーム以内（体感即時）。
- スケーラビリティ
  - 重いエフェクトや頂点数増加に対しては、workers とキャッシュ設定（パイプライン/幾何）を調整することで「UI は維持しつつ
    draw 側は遅くなる」という退行に留める。

———

3. ユースケースと入出力フロー

ここではクラス名ではなく「やりたいこと」と「入力 → 処理 → 出力」の流れで記述します。

3.1 ユースケース A: 1 本のスケッチをリアルタイムで動かす

- 入力
  - ユーザーが Python スクリプトで draw(t) を定義し、from api import G, E, L, cc, C, lfo, run を使用。
  - 任意の MIDI デバイスを接続（設定に名前を書いておく）。
  - コマンドライン: python script.py。
- 処理
  - run(draw, ...) がウィンドウ・レンダラ・ワーカー・MIDI・GUI を初期化。
  - FrameClock が t を進めつつ WorkerPool に draw(t) を投げ、最新 Geometry/Layers を SwapBuffer 経由でレンダラへ渡す。
  - Parameter GUI が geometry/effect/Style/Palette パラメータを列挙し、ユーザー操作を ParameterStore に反映。
  - MIDI サービスが各フレームの CC をスナップショットとして api.cc に供給。
- 出力 - 画面上のスケッチ（HUD 付き）。 - data/gui/<script>.json に GUI 状態。 - data/cc/<script>\_<port>.json に MIDI ノブの最終値。

  3.2 ユースケース B: 静止画像のキャプチャ

- 入力
  - ユーザーがスケッチを実行中に P または Shift+P を押す。
- 処理
  - P:
    - pyglet からカラーバッファを取得し、そのまま PNG として保存。
  - Shift+P:
    - ModernGL の FBO にラインのみ描画し、高品質なオフスクリーン画像を生成。
- 出力 - data/screenshot/ に PNG ファイル（HUD 有り/無し）。 - HUD 上に「Saved PNG: <path>」メッセージ。

  3.3 ユースケース C: ペンプロッタ用 G-code 出力

- 入力
  - スケッチ実行中に G（Shift 無し）を押す。
- 処理
  - 現在フレームの幾何スナップショット（coords/offsets）を取得。
  - ExportService に G-code ジョブを投入。
  - Worker スレッドが .part を経由して G-code ファイルを書き出し、完了/失敗/キャンセル状態を更新。
- 出力 - data/gcode/ に G-code ファイル。 - HUD で頂点ベースの進捗と完了/エラーを表示。 - Shift+G でキャンセルした場合は .part の削除と HUD のキャンセル通知。

  3.4 ユースケース D: 動画としての書き出し

- 入力
  - スケッチ実行中に V または Shift+V を押す。
- 処理
  - V: HUD 含む画面バッファをフレームごとに取得し、imageio 経由で MP4 にエンコード。
  - Shift+V: FBO で HUD 無しのラインだけを描画 → フレームを取得 → 画面へブリット → MP4 に書き出し。
  - 録画中は REC インジケータを HUD に表示。
  - 再度 V を押すと録画停止＋ファイルクローズ。
- 出力 - data/video/ に MP4 ファイル。 - HUD に「Saved MP4: <path>」。

  3.5 ユースケース E: geometry/effect を拡張する

- 入力
  - ユーザーが src/geometrys/ / src/effects/ に関数を追加し、@geometry / @effect で装飾。
  - スケッチコードでは G.<name>(...) / E.<name>(...) を利用。
- 処理
  - 起動時に import geometrys, import effects による副作用でレジストリ登録。
  - Parameter GUI は **param_meta** から RangeHint 等を生成して自動表示。
  - 署名生成時は param_meta の step を見て量子化し、キャッシュ鍵に使用。
- 出力
  - 新しい geometry/effect が G / E の属性として利用可能。
  - GUI 上にパラメータスライダーが追加され、MIDI バインディングも可能。

———

4. ドメイン概念と不変条件

ここでは「何を守るためにクラス（相当の概念）が存在するか」を整理します。実クラス名とは独立に、以下の種別で考えます。

4.1 Value Object（不変値として扱うもの）

- Geometry（幾何）
  - 状態
    - coords: float32 (N,3)、offsets: int32 (M+1,)。
    - i 本目の線: coords[offsets[i]:offsets[i+1]]。
  - 不変条件
    - offsets[0] == 0、offsets[-1] == len(coords)。
    - 2D 入力は Z=0 で補完し常に (N,3)。
    - 空集合: coords.geometry==(0,3), offsets=[0]。
    - 変換操作は常に新しい Geometry を返す（純粋）。内部状態を外部に漏らさない（as_arrays(copy=False) は読み取り専
      用ビュー）。
- LazyGeometry（幾何仕様）
  - 状態
    - base_kind: "geometry" | "geometry"。
    - base_payload: geometry 名 + 実装 + パラメータ、または Geometry。
    - plan: effect 実装とパラメータの列。
  - 不変条件
    - plan の順序は固定。
    - realize() は base + plan を順次適用し、Geometry を返す。
    - 署名 (lazy_signature) は base+plan の組み合わせに一意。
- Layer
  - 状態
    - geometry, color (RGBA|None), thickness (float|None), name, meta。
  - 不変条件
    - geometry は Geometry or LazyGeometry。
    - color 未指定時はグローバル line_color をフォールバック。
    - thickness 未指定時はグローバル line_thickness をフォールバック。
- ParameterDescriptor / RangeHint
  - 各パラメータの ID, ラベル, 型, 既定値, レンジ, choices, vector_hint 等。
  - 不変条件:
    - ID は ParameterStore 内でユニーク。
    - value_type と default_value の型は対応。
    - RangeHint の min <= default <= max。
- LFO - 不変条件 - 波形種別が無効な場合でも「何らかの周期波形（sine）」として動作する（既定波形 fallback）。 - 出力は常に [lo, hi] にクランプされ、NaN/inf は出さない。

  4.2 Entity（同一性とライフサイクルを持つもの）

- SketchSession（ランタイム全体の文脈）
  （api.sketch.\_RuntimeContext が実体）
  - 状態
    - ウィンドウ、レンダラ、WorkerPool、StreamReceiver、FrameClock、MIDI サービス、Parameter GUI、HUD、ExportService、
      VideoRecorder 等の参照。
    - 現在の recording 状態（品質モードフラグ含む）。
  - 不変条件
    - 起動時に「一貫した依存関係」で初期化される（ウィンドウが存在する間は ModernGL コンテキストが有効）。
    - on_close では Parameter GUI → MIDI → レンダラ → GL の順でリソースが解放される。
- ExportJob（G-code ジョブ）
  - 状態
    - job_id, coords/offsets, state, done_vertices, total_vertices, path, error, cancel_event。
  - 不変条件
    - state は pending -> running -> (completed|failed|cancelled) のみ。
    - completed のときのみ path が実在するファイルを指す。
    - キャンセル/失敗時は .part は必ず削除。
- MidiController / MidiControllerManager
  - 状態
    - 接続ポート名、CC マップ（番号/論理名）、現在の CC 値、モード（7bit/14bit）。
  - 不変条件
    - モードごとの CC 解釈は常に 0..1 に正規化される。
    - SAVE_DIR 配下の JSON は壊れていても復帰可能（例外を潰してゼロ初期化）。
- ParameterStore - 状態 - descriptors（ID→Descriptor）、original 値、override 値、cc_bindings、range_overrides。 - 不変条件 - ID ごとに最大 1 つの Descriptor。 - current_value が None のときは original_value が意味を持つ。 - 型が Descriptor と合わない override は無視。

  4.3 Service（状態を持たない/薄い状態のみのドメイン操作）

- geometryRegistry / EffectRegistry
  - 関数登録・取得・一覧。
  - 不変条件:
    - 同名登録は上書きではなくエラー（現実装準拠）。
    - clear_registry() はテスト用途のみで、本番では使わない暗黙契約。
- WorkerPool / StreamReceiver / SwapBuffer
  （詳細は architecture 参照）
  - 役割: draw(t) を並列実行し、最新フレームのみをレンダラへ渡す。
  - 不変条件:
    - SwapBuffer は「front/back + version」で最後に到着したフレームだけが描画される。
    - Worker 側で例外が起きてもランタイムは極力動作を継続し、HUD/ログにエラーを出す。
- MidiService
  - 役割: MIDI コントローラのポーリングと CC スナップショットの提供。
  - 不変条件:
    - スナップショットはイミュータブルであり、呼び出し側の破壊的変更は他に影響しない。

———

5. 層構造と依存方向（新リライトのための境界案）

現行の L0..L3（architecture.md）を、DDD 風の層構造に写像します。実装時は「クラス設計よりモジュール境界」を優先します。

- Domain 層
  - Geometry / LazyGeometry / Layer / LFO / 各種エフェクト・シェイプの純関数部。
  - ParameterDescriptor / RangeHint / Palette（色空間ロジック）。
  - 不変条件と計算ロジックを持つ値オブジェクトや Entity（ExportJob など）をここに置く。
  - 依存先は common/, util/ の汎用関数のみ。
- Application 層
  - スケッチ実行ユースケース全体を掌握する「シナリオ実装」。
    - SketchRunner 相当（現 run_sketch の役割）:
      - user_draw の呼び出し、Parameter GUI/MIDI/HUD の構成、WorkerPool のセットアップ、出力機能の束ね。
    - ExportService（G-code ジョブ管理）。
    - VideoRecorder（録画セッション管理）。
    - ParameterRuntime（GUI 値を geometry/effect に注入）。
  - Domain と Infrastructure をつなぐ「ポート/アダプタ」の置き場所。
  - 依存方向: Application → Domain のみ。
- Infrastructure 層
  - ModernGL / pyglet / mido / imageio など外部ライブラリに直接依存する部分。
    - RenderWindow / LineRenderer / HUD 実装 / MIDI デバイスドライバ / ファイル I/O。
  - Repository 的なインタフェース（もし導入するなら）の実装。
  - 依存方向: Infrastructure → Domain（Domain は具体クラスを知らない）。
- Interface 層
  - api パッケージ（api.G/E/L/cc/C/lfo/run など）。
  - CLI/スクリプト (main.py:1 とユーザーの script.py)。
  - GUI のエンドポイント（Parameter GUI ウィンドウ、HUD 表示の外側 API）。
  - 依存方向: Interface → Application/Domain（直接 Infrastructure に触る場合もあるが、原則 Application 経由）。
- 依存規約（維持すべきルール）
  - ポリシー → 詳細: Domain → Application → Interface の方向に依存させない。
    - Domain は Infrastructure/Interface を import しない。
    - Application は Domain にのみビジネスルールを置き、Infrastructure を差し替え可能なポートとして扱う。
  - 既存のキャンバス禁止エッジ（engine/_ -> api/_ 禁止 など）は、新層構造に合わせて維持。

———

6. クラス化の判断基準（リライト時のガイドライン）

リライト時に「何をクラスにし、何を関数/データで済ませるか」を明文化します。

- クラス化する条件（いずれかを満たすもののみ）
  - (a) 守るべき不変条件があり、それをカプセル化したい（例: Geometry, ExportJob, ParameterStore）。
  - (b) 状態と振る舞いがセットで意味を持つ（例: SketchSession, MidiController）。
  - (c) ライフサイクル（生成・破棄）を明示的に管理したい（例: ウィンドウ、ワーカプール、録画セッション）。
  - (d) 多態で差し替え可能にしたい境界（例: GCodeWriter インタフェース、MIDI 入力実装など）。
- クラスにしないもの
  - 単にデータを運ぶだけの構造（座標列、簡単な設定） → dataclass or TypedDict 程度。
  - 一回限りの処理や純粋関数（エフェクト内部ロジック、LFO 計算、座標変換など） → 関数で実装。
- 仕様レベルでの約束
  - Geometry / LazyGeometry / Layer / LFO は Value Object として設計し、不変条件を破る API を提供しない。
  - ランナー（SketchRunner）は「ユースケースのシナリオ」を表すアプリケーションサービスとして簡潔に保ち、設定読み込み/ロ
    グ/監視/エクスポートを束ねるが、幾何計算ロジックは持たない。

———
