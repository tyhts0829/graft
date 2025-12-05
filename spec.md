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

意思決定メモ: 生成物（coords/offsets）を持つ“従来の Geometry”と、レシピを持つ“式ノード”を混同すると設計が破綻するため、型名は Geometry のままでも仕様で「レシピ」と定義して固定する。

⸻

# 1. 公開 API（Python）

## 1.1 api パッケージ公開シンボル

- G: primitive ファクトリ
  - G.<name>(\*\*params) -> Geometry
  - 例: G.circle(r=10.0), G.line(p0=(0,0), p1=(10,0))
- E: effect ファクトリ／パイプラインビルダ
  - E.<effect>(\*\*params) -> EffectBuilder
  - EffectBuilder(g: Geometry) -> Geometry
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

## 2.2 RealizedGeometry（実体配列）

- 役割
  - Geometry を評価した結果である、描画・エクスポート可能な配列。
- 内部状態
  - coords: float32 配列 shape (N,3)
  - offsets: int32 配列 shape (M+1,)
- 不変条件
  - offsets[0] == 0
  - offsets[-1] == len(coords)
  - offsets は単調非減少
  - 2D 入力は z=0 補完で常に (N,3)
- 不変性（契約）
  - 原則 writeable=False を設定して返す。
  - 返された配列を書き換えた場合の挙動は未定義。

意思決定メモ: 完全不変を保証するためのコピー強制はしない。性能優先のため「契約+防御柵（writeable=False）」で運用する。

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
    - step により q = round(x/step)\*step を採用（丸め規則は Python round に従う）
    - 署名に入れる値と実計算に渡す値は一致させる
  - step の解決
    - param_meta.step があればそれを優先
    - 無ければ DEFAULT_QUANT_STEP（設定/環境変数）を使用

意思決定メモ: 署名と実引数がズレると「同じ ID で違う実体」が発生して即破綻するため、量子化は“署名と計算で同一”を仕様として固定する。

⸻

## 3.3 「同じレシピを別物として扱う」手段（必要時のみ）

- 原則：同じレシピは同じ ID で良い（共有が利益）。
- どうしても分離したい場合のみ、予約キー **salt** を許可する：
  - **salt** は args に含めて署名に影響させる
  - realize は **salt** を計算には使わない（意味論は変えない）
- UI での「別々に調整」は、GeometryId ではなく **callsite_key（後述）**で行う。

意思決定メモ: “別物”要件は主に UI/メタの話で、幾何キャッシュに混ぜるべきではない。必要なら salt、通常は callsite で解決する。

⸻

# 4. Primitive / Effect / 合成

## 4.1 レジストリ

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

⸻

# 6. draw の契約とシーン正規化

## 6.1 user_draw の契約

- シグネチャ：user_draw(t: float) -> Geometry | Layer | Sequence[Geometry|Layer]
- cc や C は引数ではなく api.cc, api.C から読む

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
  - ウィンドウイベント、入力（キー/MIDI 受信の取り回し）、OpenGL 描画
- バックグラウンド（推奨）
  - user_draw(t) の実行（必要に応じて）
  - realize の事前計算（スレッドプールで並列化）

## 7.2 推奨パイプライン（同一プロセス内スレッド中心）

- 各フレームで以下を行う：
  1. スナップショット作成：S = {t, cc_snapshot, param_snapshot, palette_snapshot, settings}
  2. user_draw を実行して SceneSpec = list[Layer] を得る（ワーカースレッドでも可）
  3. SceneSpec に含まれる全 GeometryId を列挙し、realize をスレッドプールで並列実行してキャッシュを温める
  4. メインスレッドは「最新に完成した SceneSpec」だけを採用して描画（古い計算結果は破棄可）
- 破棄規則（推奨）
  - 計算が追いつかない場合、UI を止めずにフレームを落とす（最新優先）

意思決定メモ: 配列を IPC で運ぶと帯域とコピーが支配的になりがち。まずは同一プロセス内でキャッシュ共有し、realize の並列化で稼ぐ。

⸻

# 8. ランタイムコンテキスト（cc/Parameter の整合性）

- api.cc は「現在の draw 呼び出しに紐づくスナップショット」を返す view である
- 並列実行する場合、コンテキストは呼び出し単位で固定される
  - 実装は thread-local / contextvars などでよい
- これにより、複数ワーカーが同時に cc[i] を読んでも片方が別フレームの値を見ることを避ける

意思決定メモ: cc をグローバル可変のまま並列化すると、決定性以前に「同一 draw 内の整合性」が壊れる。API は変えずに内部で固定化する。

⸻

# 9. Parameter GUI / param_meta（必要な範囲）

- param_meta は primitive/effect の引数記述（default/min/max/step/choices 等）を提供する
- ランタイムは各呼び出し点を識別する callsite_key を生成し、GUI 上のパラメータ ID に用いる
  - GeometryId は共有され得るため、GUI 識別子に使わない
- 値解決の優先順位（推奨）
  - GUI override > 明示引数（コード） > default
- GUI での step は、署名と実引数の量子化にも流用できる（望ましい）

意思決定メモ: “同じレシピを 2 回呼んだら別々に調整したい”は UI の要件であり、幾何 ID（署名）に混ぜるべきではない。callsite_key で分離する。

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

# 11. 主要な設定ノブ（最低限）

- DEFAULT_QUANT_STEP（float 量子化の既定 step）
- REALIZE_CACHE_MAX_BYTES（実体キャッシュ上限）
- REALIZE_THREAD_WORKERS（realize 並列度）
- DRAW_EXEC_MODE
  - thread（同一プロセス） / process（別プロセス） / main（メインで実行）
- FRAME_DROP_POLICY
  - latest_only（最新優先）など

意思決定メモ: 設定は結果を“できるだけ”変えないのが理想だが、性能優先なら「フレームドロップ有無」などは体験を変える。ここは明示的にノブとして意図を持って露出させる。

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
