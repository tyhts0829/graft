⸻

# 0. 基本方針（総論）

- 目標
  - 時間 t と入力（MIDI CC、GUI override、外部設定）に応じて、線描（ポリライン列）を生成・変形し、リアルタイム描画と各種エクスポート（画像/G-code/動画）を行う。
- コア設計
  - Geometry は座標配列そのものではなく「レシピ（DAG ノード）」。
  - 実体配列は RealizedGeometry(coords, offsets) として realize(Geometry) で遅延生成。
  - 描画に必要な色・線幅などは Layer に保持し、幾何レシピから分離する。
- 性能方針
  - キャッシュは「正しくヒットする」ことを最優先し、GeometryId を内容署名とする。
  - 不変性は“契約”（破壊的変更は未定義）とし、コピー強制はしない。

⸻

# 1. 公開 API（Python）

## 1.1 api パッケージ公開シンボル

- G: primitive ファクトリ
  - G.<name>(\*\*params) -> Geometry
  - 例: G.circle(r=10.0), G.line(length=10.0, angle=0.0, center=(0.0, 0.0, 0.0))
- E: effect ファクトリ／パイプラインビルダ
  - E.<effect>(\*\*params) -> Callable[[Geometry], Geometry]（返り値の builder 型は非公開）
  - E.<effect1>(...).<effect2>(...) -> Callable[[Geometry], Geometry]
- L: Layer ヘルパ
  - L(geometry, color=None, thickness=None, name=None) -> Layer
  - L.of([g1, g2, ...], \*\*style) -> list[Layer]
- cc: 現在フレームの CC スナップショット view
  - cc[i] -> float(0..1)（未定義は 0.0）
  - cc.raw() -> dict[int,float]（浅いコピー）
- geometry, effect: 拡張登録デコレータ
  - @geometry(name=..., param_meta=...)
  - @effect(name=..., param_meta=...)
- run, run_sketch: スケッチ実行

意思決定メモ: G/E/L は「ユーザーコードの書き味」を守るために残し、内部表現（レシピ DAG・キャッシュ・並行）は完全に隠蔽する。

⸻

# 2. ドメインモデル

## 2.1. Geometry（レシピノード）

- 役割
  - 線分群（ポリライン列）を生成・変形・合成する「レシピ」を表す不変ノード。
  - Geometry 自体は実体配列を（基本的に）保持しない。
- 内部状態（不変）
  - id: GeometryId（内容署名）
  - op: str（演算子名。primitive / effect / built-in combine を同一文字列空間で扱う）
  - args: tuple（正規化済み引数）
  - inputs: tuple[Geometry,...]（子ノード。primitive の場合は空）
- 典型ノード
  - primitive: op="circle", inputs=()
  - effect: op="scale", inputs=(child,)
  - combine: op="concat", inputs=(g1,g2,...)（+ の実体）

意思決定メモ: 「入力を持たない=geometry、持つ=effect」という分類は将来 concat 等で破綻しやすい。分類は内部で op の所属（primitive registry/effect registry/builtin）で行い、外部型は Geometry に統一する。

⸻

## 2.3 Layer（描画属性の付与）【改訂】

- 役割
  - 「何を描くか（Geometry）」と「どう描くか（stroke style）」を束ねるシーン要素である点は不変。
  - 太線描画（後述）とプレビュー/書き出しの整合のため、線幅の単位系を明示できるようにする。
- 内部状態（推奨）
  - geometry: Geometry
  - color: RGBA | None（None はデフォルトに委譲）
  - thickness: float | None（None はデフォルトに委譲）
  - name: str | None
- スタイル解決
  - color/thickness が None の場合はランナーのグローバル既定（config や API 引数）を使用する。
  - thickness が指定されている場合、thickness > 0 を要求し、満たさない場合は ValueError とする（Layer 生成時またはシーン正規化時に検査してよい）。
- Layer は Geometry とは別（不変）
  - Layer の color/thickness は GeometryId に影響しない。
  - 同一 Geometry を色違い・線幅違い・単位違いで複数描く場合でも、realize_cache/描画キャッシュは Geometry 単位で共有される（後述）。

⸻

## 2.3 Layer（描画属性の付与）

- 役割
  - 「何を描くか（Geometry）」と「どう描くか（stroke style）」を束ねるシーン要素。
- 内部状態（推奨）
  - geometry: Geometry
  - color: RGBA | None（None はデフォルトに委譲）
  - thickness: float | None（None はデフォルトに委譲）
  - name: str | None
- スタイル解決
  - color/thickness が None の場合はランナーのグローバル既定（config や API 引数）を使用。
- Layer は Geometry とは別
  - Layer の色・線幅は GeometryId に影響しない。
  - 同一 Geometry を色違いで複数描く場合、Geometry の実体化キャッシュは共有される。

意思決定メモ: style を Geometry に混ぜると、色や線幅変更のたびに幾何キャッシュが壊れて性能が落ちるため、Layer 分離を固定する。

⸻

# 3. 署名（GeometryId）と正規化

## 3.1. GeometryId の定義

- GeometryId は**内容署名（content signature）**であり、同一内容なら同一 ID になる。
- 署名入力は以下で構成する：
  - schema_version（例: 1）
  - op
  - inputs の id 列（順序込み）
  - args（正規化済み）
- ハッシュ関数は衝突が実用上無視できるもの（例: BLAKE2b 128bit / SHA-256）。

意思決定メモ: インスタンス ID を捨てたのは「毎フレーム同一呼び出しでもキャッシュを成立させる」ため。レシピ同一なら共有してよい。

⸻

## 3.2 args 正規化（canonicalization）

- 基本方針：同じ意味の入力は同じ args 表現になる。
- 受け付ける型（推奨）
  - int, float, bool, str, None, Enum
  - tuple/list（再帰的に正規化 →tuple 化）
  - dict（キーをソートし tuple((k,v),...) に変換）
- float の扱い
  - NaN/inf は ValueError（ノード生成時）
  - -0.0 は +0.0 に正規化
  - 量子化（任意だが推奨）
    - q = round(x/DEFAULT_QUANT_STEP)\*DEFAULT_QUANT_STEP を採用（丸め規則は Python round に従う）
    - 署名に入れる値と実計算に渡す値は一致させる（量子化は resolver で一度だけ実施し、Geometry では再量子化しない）

意思決定メモ: 署名と実引数がズレると「同じ ID で違う実体」が発生して即破綻するため、量子化は“署名と計算で同一”を仕様として固定する。

⸻

# 4. Primitive / Effect / 合成

## 4.1 レジストリデコレータ

- PrimitiveRegistry: op_name -> make(args) -> RealizedGeometry
- EffectRegistry: op_name -> apply(inputs_realized, args) -> RealizedGeometry
- 組み込み concat（+）は registry 外の built-in として扱う（実装固定でよい）

## 4.2 G（primitive 生成）

- G.<name>(\*\*params) は以下を行う：
  - Geometry(op=name, inputs=(), args=normalized) を構築
  - id を内容署名で生成
  - その Geometry を返す

## 4.3 E（effect 適用）

- E.<effect>(\*\*params) -> builder を返す
- builder(g: Geometry) -> Geometry は以下を行う：
  - params を解決（override/既定/正規化/量子化）
  - Geometry(op=effect, inputs=(g,), args=normalized) を構築
  - id を内容署名で生成
- E.a(...).b(...)(g)のようにメソッドチェーンすることが可能。

## 4.4 合成（+）

- g1 + g2 は Geometry(op="concat", inputs=(g1,g2), args=()) を生成
- 連鎖 g1 + g2 + g3 は左結合でもよいが、内部でフラット化して (g1,g2,g3) に正規化してよい（署名が安定する）

意思決定メモ: 「1 操作=1 ノード」の DAG に統一すると、部分共有・inflight 排除・差分再計算が素直になる。

⸻

# 5. 実体化 realize とキャッシュ

## 5.1 realize(Geometry) -> RealizedGeometry

- 入力：Geometry（レシピノード）
- 出力：RealizedGeometry(coords, offsets)
- 処理手順（規範）
  1. realize_cache を geometry.id で参照し、ヒットなら返す
  2. ミスなら inflight を参照し、同じ ID が計算中なら完了を待つ
  3. 自分が先行計算者なら、op に応じて評価する：
  - primitive: PrimitiveRegistry[op].make(args)
  - effect: 入力（通常 1 個）を realize し、EffectRegistry[op].apply(input, args)
  - concat: 各 inputs を realize し、連結して新しい RealizedGeometry を作る
  4. 生成物の不変条件を検証し（少なくとも offsets/shape/dtype）、writeable=False を設定
  5. realize_cache に格納し、待機者に通知、返す

## 5.2 realize_cache の仕様（性能最適化）

- 形：GeometryId -> RealizedGeometry
- 永続性は保証しない（LRU 等で削除可）
- 上限管理は「エントリ数」ではなく「推定バイト数（coords.nbytes+offsets.nbytes）」を推奨
- 削除されても Geometry（レシピ）が残っていれば再計算できる

## 5.3 inflight（重複計算排除）

- 形：GeometryId -> Future/Condition
- 同一 ID の同時計算を 1 回に潰す（スレッド並列時に重要）

意思決定メモ: 並列化で最も無駄が出るのは「同じサブグラフを別スレッドで二重計算」なので、inflight は必須の性能要件。

## 5.4 描画キャッシュ（RenderPrepCache / GpuCache）

5.4.1 RenderPrepCache（CPU 前処理キャッシュ）

- 目的
  - RealizedGeometry（ポリライン列）を、太線レンダリング向けの「厚み非依存」な頂点属性・インデックスへ変換した結果をキャッシュし、毎フレームの再前処理を避ける。
- 形
  - render_prep_cache: GeometryId -> PreparedStroke（後述）
  - 上限管理は推定バイト数を推奨（頂点属性バッファ＋インデックスの合計）。
- 永続性
  - 永続性は保証しない（LRU 等で削除可）。
  - 削除されても realize_cache から再構築できる。

### 5.4.2 GpuCache（GPU リソースキャッシュ）

- 目的
  - PreparedStroke を GPU バッファ（VBO/IBO/VAO 等）へアップロードした結果をキャッシュし、描画時のバッファ再生成を避ける。
- 形
  - gpu_cache: GeometryId -> GpuStrokeHandle（バッファハンドル群＋頂点数等）
  - 上限管理は推定 GPU バイト数を推奨。
- 制約（規範）
  - GPU リソースの生成・破棄・更新は必ずメインスレッド（GL コンテキスト所有スレッド）で行う。
  - gpu_cache は renderer の実装都合で随時クリアしてよい（正しさは維持されること）。

意思決定メモ: realize_cache は「CPU 配列の共有」しか解決しない。描画で支配的になるのは前処理とアップロードなので、描画専用キャッシュを GeometryId キーで独立に持つ。

⸻

# 6. draw の契約とシーン正規化

## 6.1 user_draw の契約

- シグネチャ：user_draw(t: float) -> Geometry | Layer | Sequence[Geometry|Layer]
- cc は引数ではなく api.cc から読む

## 6.2 戻り値の正規化

- ランナーは戻り値を list[Layer] に正規化する：
  - Geometry は Layer(geometry=..., color=None, thickness=None) で包む
  - Layer はそのまま採用
  - Sequence はフラットにして同様に処理
- Layer.color/thickness が None の場合はグローバル既定を適用して描画する（Layer 自体は None 保持のままでもよい）

意思決定メモ: draw の戻り値を最終的に Layer 列に揃えると、描画・エクスポート・動画の全経路が同じシーン表現を共有できる。

⸻

# 7. 実行モデル（描画・並行性）

## 7.1 スレッド/プロセスの役割分担（規範）

- メインスレッド（必須）
  - ウィンドウイベント、入力（キー/MIDI 受信の取り回し）、OpenGL 描画。
  - GpuCache の生成・破棄・更新（GL リソース操作は全てここ）。
- バックグラウンド（推奨）
  - user_draw(t) の実行（必要に応じて）。
  - realize の事前計算（スレッドプールで並列化）。
  - PreparedStroke（CPU 前処理）の生成（スレッドプールで並列化可）。ただし GL は触らない。

意思決定メモ: 「GL を触る境界」を仕様で固定し、ワーカー側は純 CPU 処理に閉じる。これによりデッドロックやコンテキスト競合のクラスのバグを削る。

## 7.2 推奨パイプライン（同一プロセス内スレッド中心）

- 各フレームで以下を行う（規範）
  1. スナップショット作成：S = {t, cc_snapshot, param_snapshot, palette_snapshot, settings}
  2. user_draw を実行して SceneSpec = list[Layer] を得る（ワーカースレッドでも可）
  3. SceneSpec に含まれる全 GeometryId を列挙し、realize をスレッドプールで並列実行して realize_cache を温める
  4. realize 完了したものから順に PreparedStroke を生成し、render_prep_cache を温める（ワーカースレッド可）
  5. メインスレッドは「最新に完成した SceneSpec」を採用し、必要な GeometryId について GpuCache を補完（未アップロード分のみ）して描画する
  6. 古い計算結果は破棄可（ただしキャッシュは残してよい）
- フレーム未完了時の扱い（推奨）
  - 描画で未準備（realize 未完了 / PreparedStroke 未完了）の Layer はスキップしてよい（設定で「前フレームの同名 Layer を維持」等に拡張可能）。

意思決定メモ: “CPU 側でできる限りを先に済ませる”と、メインスレッドは「バッファがあれば描く、なければ最小限アップロードして描く」に収束し、入力/ウィンドウ応答性が保ちやすい。
⸻

# 8. ランタイムコンテキスト（cc/Parameter の整合性）

- api.cc は「現在の draw 呼び出しに紐づくスナップショット」を返す view である
- 並列実行する場合、コンテキストは呼び出し単位で固定される
  - 実装は thread-local / contextvars などでよい
- これにより、複数ワーカーが同時に cc[i] を読んでも片方が別フレームの値を見ることを避ける

意思決定メモ: cc をグローバル可変のまま並列化すると、決定性以前に「同一 draw 内の整合性」が壊れる。API は変えずに内部で固定化する。

⸻

# 9. Parameter GUI

- draw 関数内で呼ばれる primitive や effect の引数は、あるいは他のモジュールから import される関数内の primitive や effect の引数はすべて paraemter_gui でスライダー等で制御できる。
- gui は主に 3 つの列エリアに分かれる。
  - 1 列目はラベル列。primitive や effect の関数名に連番がついたもの。scalar #1 など。
  - 2 列目は制御 UI 列。引数が float ならスライダー。Vec3 なら 3 列スライダー。引数が str ならテキストボックス、いくつかのタイプを表すならラジオボタン。bool ならトグルボタン。
  - 3 列目は min , max, cc の入力ボックス。これはスライダーの最小値、最大値を制御するものと、cc は数字をいれると PC に繋がれた midi コントローラーのその cc 番号でそのパラメータを制御できるようにする。

⸻

# 10. 例外・ログ（推奨方針）

デバッグと運用の安定性を優先する。

- ノード生成時（G/E 呼び出し時）
  - 正規化不能な型、NaN/inf、step 不正は TypeError/ValueError で即時に落とす
- realize 時
  - 計算失敗は RealizeError（独自例外）に包み、GeometryId と op と引数要約を付ける
- ランナー方針
  - user_draw 例外は捕捉し、HUD/ログに出しつつ継続するか終了するかを設定で選べる

意思決定メモ: “早く落とす”は creative coding でも得をすることが多い（壊れた入力が後段で増幅するのを防ぐ）。反復速度を優先する。

⸻

# 11. 主要な設定ノブ【追記（描画系）】

- 既存（不変）
  - DEFAULT_QUANT_STEP（float 量子化の既定 step）
  - REALIZE_CACHE_MAX_BYTES（実体キャッシュ上限）
  - REALIZE_THREAD_WORKERS（realize 並列度）
- 描画系（推奨追加）
  - RENDER_PREP_CACHE_MAX_BYTES（PreparedStroke の CPU キャッシュ上限）
  - GPU_CACHE_MAX_BYTES（GPU バッファキャッシュ上限）
  - STROKE_MITER_LIMIT（miter クランプ閾値。未指定なら実装既定）
  - AA_FEATHER_PX（フェザー幅。未指定なら実装既定）
  - MSAA_SAMPLES（未指定なら環境既定）

意思決定メモ: 描画キャッシュは CPU/GPU の制約が別で、単一ノブにまとめると運用で詰まりやすい。上限を分離して露出し、メモリと描画品質のトレードオフを制御可能にする。

⸻

# 12. 最小の具体例（仕様の読み替え確認用）

- ユーザーコード：

from api import G, E, L, cc

def draw(t):
r = 10 + 50 \* cc[1]
g = G.circle(r=r)
g = E.scale(s=1.2)(g)
return [
L(g, color="#fff", thickness=0.35),
L(E.rotate(angle=t)(g), color="#0ff", thickness=0.15),
]

- 内部的には：
  - G.circle が Geometry(op="circle", inputs=(), args=(...)) を作り、id は内容署名
  - E.scale が Geometry(op="scale", inputs=(circle,), args=(...)) を作る
  - 描画直前に必要な GeometryId が realize_cache に無ければ realize で生成し、以降は再利用

⸻

# 13. 描画バックエンド

## 13.1 基本方針

- RealizedGeometry は「中心線（ポリライン列）」として扱い、ストロークは三角形で描画する。
- glLineWidth に依存しない（環境差・上限・コアプロファイル問題を避ける）。
- 線幅（thickness）は Layer スタイルであり、GeometryId/realize_cache を壊さない。
- MSAA が有効な環境では MSAA を使用する（ウィンドウ/FBO 設定）。
- 追加で、フラグメント側に 1px 程度のフェザーを入れてエッジのギザを抑える実装を推奨する（導関数 fwidth 等を用いた解析的なスムージング）。
  - ただし AA の有無は見た目の問題であり、幾何（realize）やキャッシュの正しさに影響してはならない。

## 13.2 描画順序とブレンド（規範）

- Layer の並び順を描画順とする（後勝ち）。
- アルファブレンドを使用する場合、premultiplied alpha を推奨する（色解決側で統一してよい）。

## 13.3 LineMesh（VBO/IBO 管理）【実装契約】

- 実装: `src/render/line_mesh.py` の `LineMesh(ctx, program, initial_reserve=8*1024*1024)` を使用する。`ctx` は ModernGL コンテキスト、`program` は後述のシェーダ。
- `upload(vertices, indices)` の入力
  - `vertices`: float32, shape (N, 3)。座標系はワールド（mm）。`RealizedGeometry.coords` をそのまま渡す。
  - `indices`: uint32, shape (M,)。偶数長で、`GL_LINES` で解釈される 2 要素単位の線分列。ポリライン切り替えを明示したい場合は `LineMesh.PRIMITIVE_RESTART_INDEX (=0xFFFFFFFF)` を差し込んでよい（ctx.primitive_restart は `__init__` で有効化済み）。
- バッファ拡張は `upload` 内で自動。再確保時は VAO を貼り替えるため、呼び出し側は `vao` を作り直す必要なし。
- 描画は `vao.render(mode=ctx.LINES, vertices=index_count)` を基本とする。`index_count` は直近の `upload` で更新される。
- リソース解放は `release()` で VBO/IBO/VAO を破棄する（ウィンドウ終了時に必須）。

## 13.4 シェーダ契約（`src/render/shader.py`）

- `Shader.create_shader(ctx)` が返す `program` を前提とする。
- 頂点属性
  - `in_vert: vec3` … ワールド座標（mm）。Z はそのまま渡し、直交射影で XY のみ使用。
- ユニフォーム
  - `projection: mat4` … `utils.build_projection(canvas_w, canvas_h)` の結果（ModernGL 向けに転置済み）。
  - `line_thickness: float` … クリップ空間での線幅（全幅）。ワールド厚み `t_world` を `t_clip = t_world * (2.0 / canvas_width_mm)` で変換して渡す（現行シェーダは XY 同倍率を仮定）。
  - `color: vec4` … premultiplied alpha を推奨。未指定はデフォルト黒。
- パイプライン
  - VS: `projection * vec4(in_vert.xy, 0, 1)` で NDC へ。
  - GS: `layout(lines)` 入力を線分ごとに 4 頂点へ展開し、厚み方向オフセット `offset = normalize(p1 - p0).perp * line_thickness / 2` で太線化。
  - FS: 単色出力（必要に応じて AA フェザー処理を追加してよい）。

## 13.5 射影行列と厚み解釈（`src/render/utils.py`）

- `build_projection(canvas_width, canvas_height)` は「キャンバス寸法（mm）を基準とする正射影」を返す。X は左→右で [-1,1]、Y は上→下で [-1,1] へ線形マッピング（ModernGL のカラム優先に合わせ転置済み）。
- 厚みの扱い
  - thickness はワールド長（mm）で指定し、`line_thickness` ユニフォームへ渡す時に前述の `t_clip` へ変換する。
  - キャンバスの X/Y スケールが異なる場合でも、現行シェーダは等方厚みを仮定する。非等方補正が必要になったら、GS へ `viewport_size` を追加し、CPU 側で軸別スケールを計算する。

## 13.6 最小パイプライン（`main.py` → 画面描画）

- 目的: 現行の `main.py`（Geometry を生成し realize まで行う）を実際に描画する最小経路を固定する。
- 手順（1 フレーム分）
  1. GL 初期化: `ctx = moderngl.create_standalone_context()`（あるいはウィンドウ付きコンテキスト）。`program = Shader.create_shader(ctx)`、`mesh = LineMesh(ctx, program)` を用意。
  2. シーン取得: `geom = ...`（`main.py` の G/E 呼び出し）、`realized = realize(geom)` で `RealizedGeometry(coords, offsets)` を得る。
  3. インデックス生成: 各ポリライン区間 `[o_i, o_{i+1})` について `(k, k+1)` を列挙し uint32 配列 `indices` を作る。ポリライン間を切りたい場合は `PRIMITIVE_RESTART_INDEX` を差し込む。
  4. GPU 転送: `mesh.upload(vertices=realized.coords, indices=indices)`。
  5. ユニフォーム設定: `program["projection"].write(build_projection(canvas_w, canvas_h).tobytes())`; `program["line_thickness"].value = thickness_world * (2.0 / canvas_w)`（Layer.thickness が None の場合は既定値を使用）; `program["color"].value = (r,g,b,a)`。
  6. 描画: `ctx.clear()` の後 `mesh.vao.render(mode=ctx.LINES, vertices=mesh.index_count)` を呼ぶ。ブレンド有効時は premultiplied alpha 設定を合わせる。
  7. 終了処理: ウィンドウの swap/present。終了時に `mesh.release()`。
- レイヤーが複数ある場合は 3–6 を Layer 順に繰り返す。GeometryId が同じなら `realize` 結果とアップロード済みバッファを共有してよい。

⸻

# 14. 座標系・厚み単位【追記】

- 座標系（規範）
  - RealizedGeometry.coords は「ワールド座標」とみなし、プレビュー/書き出しはいずれもワールド → デバイスの変換（カメラ/ビューポート）を経て最終座標系へ写像する。
  - 2D 入力は z=0 補完済みであるため、レンダラは基本的に x,y を主に用い、z は将来拡張（深度/ソート）に使用してよい。
- 厚みの単位（規範）
  - thickness は常にワールド座標系での距離として解釈する。
  - プレビューと書き出しの双方で同じ解釈を用いる。
  - スクリーンピクセル固定の線幅モードはサポートしない（必要になった場合は別機能として追加する）。

意思決定メモ: 線幅は見た目と出力整合の中心で、曖昧さが残るとバグではなく「期待値差」で破綻する。単位を明文化し、既定値の適用規則も固定する。

⸻
