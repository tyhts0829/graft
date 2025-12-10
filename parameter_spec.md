⸻

8. ランタイムコンテキスト（cc/Parameter の整合性）【追記】
   • parameter_gui で解決されるパラメータ値は「現在の draw 呼び出しに紐づくスナップショット」に固定される。
   • 並列実行する場合、ランナーは draw 呼び出し単位で以下を固定化する：
   • cc_snapshot（既存）
   • param_snapshot（追加：GUI 状態の読み取り専用ビュー）
   • discovery_sink（追加：その draw で発見したパラメータの収集先）

実装は thread-local / contextvars などでよい。G/E の呼び出しは内部で param_snapshot を参照して引数を解決し、discovery_sink に発見情報を書き込む。

意思決定メモ: parameter_gui をグローバル可変で読ませると、同一 draw 内で ui_min/ui_max/override が途中で変わり得て決定性と整合性が崩れる。cc と同様にスナップショットを“呼び出し単位で固定”することで、API を増やさずに安全な並列化が可能になる。

⸻

9. Parameter GUI【追記：動的発見・解決仕様】

9.1 目的（再掲・具体化）
• 目的
• user_draw 実行中に実際に呼ばれた primitive / effect の引数を自動的に列挙し、GUI 行（コントロール）として生成・維持する。
• 各引数について、コードが与える基準値（base）に対し、GUI 手動値（override）または MIDI CC による値（cc）を適用して “effective 値” を決定し、Geometry ノード生成に反映する。
• 非目的
• realize 後の配列（RealizedGeometry）を書き換えることによる変形は行わない（DAG レシピを組み直す）。

意思決定メモ: 配列の事後変更は GeometryId と実体キャッシュを破壊しやすい。引数解決をノード生成時点に集約すると、署名とキャッシュの整合性を保ったままリアルタイム制御ができる。

⸻

9.2 ParameterKey（識別子）と site_id
• 各 GUI 行（=制御対象パラメータ）は ParameterKey で一意に識別する。
• ParameterKey の構成（規範）
• op: str（primitive/effect 名）
• site_id: str（呼び出し箇所署名。後述）
• arg: str（引数名）
• site_id（呼び出し箇所署名）の要件
• 同一のソース上の“同じ呼び出し箇所”を安定に指す（同一フレーム・別フレームで一致）。
• 同一行に複数呼び出しがある場合でも衝突しにくい。
• 取得コストが小さい（毎フレーム多数呼ばれても許容）。
• site_id の推奨定義（規範）
• Python のフレーム情報から次を取り出して文字列化する：
• filename（code.co_filename）
• function_anchor（code.co_firstlineno 等、関数の同定に使える情報）
• bytecode_offset（frame.f_lasti）
• 例："{filename}:{co_firstlineno}:{f_lasti}"
• site_id の取得タイミング（規範）
• primitive: G.<name>(**params) が呼ばれた時点（=ノード生成時点）の“呼び出し元”を site_id とする。
• effect:
• E.<effect>(**params) およびメソッドチェーンで追加される各 effect ステップごとに、ステップ追加時点の呼び出し元を step_site_id として保持する。
• builder を apply して Geometry ノードを作る際は、そのステップに保持された step_site_id を site_id として使用する。
• これにより、E.a(...).b(...)(g) の a と b を別の GUI 行として識別できる。

意思決定メモ: effect を apply 時点の callsite で識別すると、チェーン内の複数ステップが同一 site_id になり分離できない。ステップ“宣言時点”で site_id を固定し、apply 時はそれを使用するのが最も自然で安定する。

⸻

9.3 ParamMeta（UI 型・範囲・量子化）
• primitive / effect の登録側（@geometry / @effect）は param_meta を提供できる。
• param_meta の役割（推奨）
• GUI 生成のための型情報（float / bool / str / enum / vecN 等）
• 既定の ui_min/ui_max（スライダー範囲）
• step（量子化ステップ。署名と計算で同一に適用）
• 表示名や並び順などの補助（任意）
• meta が無い場合のフォールバック（推奨）
• 正規化で許容される型（int/float/bool/str/None/Enum/tuple/list/dict）から UI 型を推定する。
• float 系の ui_min/ui_max は、base 値から推奨レンジを自動生成する（設定ノブで調整可能）。
• このフォールバックは利便性のためのものであり、安定運用には meta 提供を推奨する。

意思決定メモ: 「すべての引数を GUI で制御できる」という体験を守るには推定が必要だが、正確な範囲・step はドメイン知識がないと破綻しやすい。meta を第一級にしつつ、最低限の推定で穴を埋める。

⸻

9.4 GUI 状態（ParamState）と永続化
• GUI は ParameterKey ごとに ParamState を保持する。
• ParamState の最小構成（規範）
• override: bool（GUI 手動値を使うか）
• ui_value: Any（手動値。型は meta に従う）
• ui_min: Any（スライダー最小。float/vecN の場合に使用）
• ui_max: Any（スライダー最大。float/vecN の場合に使用）
• cc: int | None（MIDI CC 番号。None は未割当）
• last_seen_frame: int（最後に draw 中で出現したフレーム番号。運用用）
• 永続化（推奨）
• ParamState は GUI のプロファイルとして保存・復元できる（JSON 等）。
• 保存キーは ParameterKey（op/site_id/arg）を基本とする。
• コード編集で site_id が変わるとマッピングが変わり得るため、復元時は「完全一致しないキーは新規扱い」にする。

意思決定メモ: GeometryId をキーにすると内容同一のノードが潰れてしまい、呼び出し箇所ごとの GUI を構成できない。呼び出し箇所をキーにするのが GUI 仕様（関数名+連番）と整合する。

⸻

9.5 動的発見（discovery）プロトコル
• discovery の目的
• 今フレームの user_draw が実際に生成したパラメータ集合を列挙し、GUI に行を生成/更新させる。
• discovery の単位（規範）
• draw 呼び出しごとに独立した DiscoveryBuffer（収集バッファ）を持つ。
• G/E のノード生成は、各引数について discovery レコードを DiscoveryBuffer に追加する。
• discovery レコード（推奨）
• key: ParameterKey
• group_label: str（例："circle #1" のような表示用ラベル）
• base_value: Any（ユーザーコードが渡した値）
• effective_value: Any（解決後の値。量子化済み）
• source: str（“base” / “gui” / “cc”）
• meta_summary: dict（型・既定 ui_min/ui_max/step 等）
• ランナーによるマージ（規範）
• draw 終了後、ランナーは DiscoveryBuffer をメインスレッドで master ParamStore にマージする。
• 新規 ParameterKey を見つけた場合、ParamState を生成して既定値を設定する：
• override=False
• ui_value=base_value（型に従い正規化）
• ui_min/ui_max=meta 既定（無ければフォールバックで生成）
• cc=None
• 既存 ParameterKey は last_seen_frame を更新する。
• 今フレーム見えなかった ParameterKey は削除せず保持し、必要なら GUI で非表示/グレー表示にする。

意思決定メモ: discovery を draw 実行スレッドから master state に直接書くと、ロック競合と可視性問題が発生する。呼び出し単位で収集し、境界でマージするのが最も単純で安全。

⸻

9.6 引数の値解決（base / GUI / CC）
• すべての GUI 制御は「ノード生成時の引数解決」として実装する。
• 解決の入力（規範）
• base_value: user code から渡された値（式評価後）
• ParamState（スナップショットから取得）
• cc_snapshot（スナップショット）
• param_meta（型・範囲・step）
• 解決規則（規範） 1. ParamState.cc が設定されている場合、CC 制御を優先する：
• u = cc_snapshot[cc]（0..1、未定義は 0.0）
• effective = map_cc(u, ui_min, ui_max, meta)（型に応じた写像） 2. そうでなく ParamState.override が True の場合、effective = ui_value 3. それ以外は effective = base_value 4. effective を型検証し、必要なら clamp/離散化を行う（型規則は meta に従う） 5. float を含む場合は step を用いて量子化し、署名と計算に同一の effective を渡す
• CC 写像（推奨）
• float: ui_min..ui_max を線形補間
• vecN: 各成分を線形補間（ui_min/ui_max は成分ごと、またはスカラなら全成分に適用）
• bool: u >= 0.5 を True
• enum: index = round(u\*(n-1))（範囲に clamp）
• str: CC 制御は未対応（禁止してよい）

意思決定メモ: 優先順位を固定しないと「cc を割り当てたのにスライダーが勝つ」「override を切っても固定値が残る」など UI が不安定になる。CC > GUI > base の 3 段に固定すると説明可能性が高い。

⸻

9.7 正規化・署名・キャッシュとの整合
• G/E のノード生成は、解決後の effective 値を args 正規化（canonicalization）にかけた上で Geometry を生成する（規範）。
• float の量子化は、以下を満たすように行う（規範）
• GeometryId の署名に入る値と、実計算（PrimitiveRegistry/EffectRegistry）に渡す値が完全に一致する。
• NaN/inf は reject（ValueError）。
• -0.0 は +0.0 に正規化。
• これにより、parameter_gui の操作は GeometryId を正しく変化させ、realize_cache は自然に整合する（規範）。

意思決定メモ: GUI で値を動かした結果が「同じ GeometryId のまま違う配列」になると、キャッシュが破綻して再現不能になる。解決 → 量子化 → 正規化 → 署名の順序を固定し、計算値も同一にするのが必須。

⸻

9.8 GUI 表示モデル（3 列）と連番
• GUI の行は ParameterKey ごとに 1 行を基本とする。
• 列構成（既存仕様の具体化）
• 1 列目（ラベル列）
• group_label: "{op} #{ordinal}" を基本とし、引数名は必要なら補助表示する（例：scale #2 / s）。
• 2 列目（制御 UI 列）
• meta 型に応じた UI を表示する（float=スライダー、vecN=成分スライダー、str=テキスト、enum=ラジオ/ドロップダウン、bool=トグル）。
• override の有効/無効を切り替える UI（例：BASE/GUI トグル）を必須とする。
• 3 列目（ui_min/ui_max/cc 入力列）
• ui_min/ui_max はスライダーのレンジとして使用する。
• cc は MIDI CC 番号を設定する。設定時は CC 制御が優先される。
• ordinal（# の連番）の決め方（規範）
• op ごとに、初めて観測した site_id の順に 1,2,3… を割り当てる。
• 同一 op & site_id は常に同じ ordinal を持つ（GUI プロファイルが続く限り）。

意思決定メモ: 連番は “実行時に見つかった順” に固定するのが最も実装が単純で、ユーザーの認知とも一致する。ソート（行番号順など）は編集で揺れやすく、運用上の安定性が落ちる。

⸻

9.9 制約・既知の挙動
• ループ内で同一呼び出し箇所（同一 site_id）が複数回実行される場合
• それらは同一 ParameterKey を共有し、1 つの GUI 行でまとめて制御される（規範）。
• コード編集による site_id 変化
• 行の移動・式の組み替え等で site_id が変わると、保存済み GUI 状態が一致しない場合がある（仕様）。
• draw 外で Geometry を構築するパターン
• parameter_gui による動的制御を前提とする場合、G/E によるノード生成は runner 管理下（draw 呼び出し）で行うことを推奨する。

意思決定メモ: “呼び出し箇所”をキーにする以上、ループの反復やコード編集の影響は避けられない。これを仕様として明文化し、必要なら将来「明示キー」拡張で解決する余地を残す。

⸻

9.10 例外・ログ（GUI 入力の扱い）
• GUI 入力はユーザーコードと異なり、運用上は「落とす」より「安全に無効化」を優先してよい（推奨）。
• 具体方針（推奨）
• ui_min/ui_max の不正（ui_min>=ui_max、型不一致）はその項目を無視し、最後の有効値または meta 既定にフォールバックし、HUD/ログに警告を出す。
• cc の不正（範囲外、非整数）は無視して警告。
• ui_value の型不一致やパース失敗は override を一時無効化して警告（または前回値を保持）。

意思決定メモ: GUI は反復的に編集され、無効な中間状態（入力途中）を頻繁に通過する。ここで例外を投げて落とすと創作体験を壊しやすい。一方で、無言で無視すると原因不明になるため、HUD/ログの警告は必須とする。

⸻

8. ランタイムコンテキスト（cc/Parameter の整合性）【追記】
   • parameter_gui で解決されるパラメータ値は「現在の draw 呼び出しに紐づくスナップショット」に固定される。
   • 並列実行する場合、ランナーは draw 呼び出し単位で以下を固定化する：
   • cc_snapshot（既存）
   • param_snapshot（追加：GUI 状態の読み取り専用ビュー）
   • discovery_sink（追加：その draw で発見したパラメータの収集先）

実装は thread-local / contextvars などでよい。G/E の呼び出しは内部で param_snapshot を参照して引数を解決し、discovery_sink に発見情報を書き込む。

意思決定メモ: parameter_gui をグローバル可変で読ませると、同一 draw 内で ui_min/ui_max/override が途中で変わり得て決定性と整合性が崩れる。cc と同様にスナップショットを“呼び出し単位で固定”することで、API を増やさずに安全な並列化が可能になる。

⸻

9. Parameter GUI【追記：動的発見・解決仕様】

9.1 目的（再掲・具体化）
• 目的
• user_draw 実行中に実際に呼ばれた primitive / effect の引数を自動的に列挙し、GUI 行（コントロール）として生成・維持する。
• 各引数について、コードが与える基準値（base）に対し、GUI 手動値（override）または MIDI CC による値（cc）を適用して “effective 値” を決定し、Geometry ノード生成に反映する。
• 非目的
• realize 後の配列（RealizedGeometry）を書き換えることによる変形は行わない（DAG レシピを組み直す）。

意思決定メモ: 配列の事後変更は GeometryId と実体キャッシュを破壊しやすい。引数解決をノード生成時点に集約すると、署名とキャッシュの整合性を保ったままリアルタイム制御ができる。

⸻

9.2 ParameterKey（識別子）と site_id
• 各 GUI 行（=制御対象パラメータ）は ParameterKey で一意に識別する。
• ParameterKey の構成（規範）
• op: str（primitive/effect 名）
• site_id: str（呼び出し箇所署名。後述）
• arg: str（引数名）
• site_id（呼び出し箇所署名）の要件
• 同一のソース上の“同じ呼び出し箇所”を安定に指す（同一フレーム・別フレームで一致）。
• 同一行に複数呼び出しがある場合でも衝突しにくい。
• 取得コストが小さい（毎フレーム多数呼ばれても許容）。
• site_id の推奨定義（規範）
• Python のフレーム情報から次を取り出して文字列化する：
• filename（code.co_filename）
• function_anchor（code.co_firstlineno 等、関数の同定に使える情報）
• bytecode_offset（frame.f_lasti）
• 例："{filename}:{co_firstlineno}:{f_lasti}"
• site_id の取得タイミング（規範）
• primitive: G.<name>(**params) が呼ばれた時点（=ノード生成時点）の“呼び出し元”を site_id とする。
• effect:
• E.<effect>(**params) およびメソッドチェーンで追加される各 effect ステップごとに、ステップ追加時点の呼び出し元を step_site_id として保持する。
• builder を apply して Geometry ノードを作る際は、そのステップに保持された step_site_id を site_id として使用する。
• これにより、E.a(...).b(...)(g) の a と b を別の GUI 行として識別できる。

意思決定メモ: effect を apply 時点の callsite で識別すると、チェーン内の複数ステップが同一 site_id になり分離できない。ステップ“宣言時点”で site_id を固定し、apply 時はそれを使用するのが最も自然で安定する。

⸻

9.3 ParamMeta（UI 型・範囲・量子化）
• primitive / effect の登録側（@geometry / @effect）は param_meta を提供できる。
• param_meta の役割（推奨）
• GUI 生成のための型情報（float / bool / str / enum / vecN 等）
• 既定の ui_min/ui_max（スライダー範囲）
• step（量子化ステップ。署名と計算で同一に適用）
• 表示名や並び順などの補助（任意）
• meta が無い場合のフォールバック（推奨）
• 正規化で許容される型（int/float/bool/str/None/Enum/tuple/list/dict）から UI 型を推定する。
• float 系の ui_min/ui_max は、base 値から推奨レンジを自動生成する（設定ノブで調整可能）。
• このフォールバックは利便性のためのものであり、安定運用には meta 提供を推奨する。

意思決定メモ: 「すべての引数を GUI で制御できる」という体験を守るには推定が必要だが、正確な範囲・step はドメイン知識がないと破綻しやすい。meta を第一級にしつつ、最低限の推定で穴を埋める。

⸻

9.4 GUI 状態（ParamState）と永続化
• GUI は ParameterKey ごとに ParamState を保持する。
• ParamState の最小構成（規範）
• override: bool（GUI 手動値を使うか）
• ui_value: Any（手動値。型は meta に従う）
• ui_min: Any（スライダー最小。float/vecN の場合に使用）
• ui_max: Any（スライダー最大。float/vecN の場合に使用）
• cc: int | None（MIDI CC 番号。None は未割当）
• last_seen_frame: int（最後に draw 中で出現したフレーム番号。運用用）
• 永続化（推奨）
• ParamState は GUI のプロファイルとして保存・復元できる（JSON 等）。
• 保存キーは ParameterKey（op/site_id/arg）を基本とする。
• コード編集で site_id が変わるとマッピングが変わり得るため、復元時は「完全一致しないキーは新規扱い」にする。

意思決定メモ: GeometryId をキーにすると内容同一のノードが潰れてしまい、呼び出し箇所ごとの GUI を構成できない。呼び出し箇所をキーにするのが GUI 仕様（関数名+連番）と整合する。

⸻

9.5 動的発見（discovery）プロトコル
• discovery の目的
• 今フレームの user_draw が実際に生成したパラメータ集合を列挙し、GUI に行を生成/更新させる。
• discovery の単位（規範）
• draw 呼び出しごとに独立した DiscoveryBuffer（収集バッファ）を持つ。
• G/E のノード生成は、各引数について discovery レコードを DiscoveryBuffer に追加する。
• discovery レコード（推奨）
• key: ParameterKey
• group_label: str（例："circle #1" のような表示用ラベル）
• base_value: Any（ユーザーコードが渡した値）
• effective_value: Any（解決後の値。量子化済み）
• source: str（“base” / “gui” / “cc”）
• meta_summary: dict（型・既定 ui_min/ui_max/step 等）
• ランナーによるマージ（規範）
• draw 終了後、ランナーは DiscoveryBuffer をメインスレッドで master ParamStore にマージする。
• 新規 ParameterKey を見つけた場合、ParamState を生成して既定値を設定する：
• override=False
• ui_value=base_value（型に従い正規化）
• ui_min/ui_max=meta 既定（無ければフォールバックで生成）
• cc=None
• 既存 ParameterKey は last_seen_frame を更新する。
• 今フレーム見えなかった ParameterKey は削除せず保持し、必要なら GUI で非表示/グレー表示にする。

意思決定メモ: discovery を draw 実行スレッドから master state に直接書くと、ロック競合と可視性問題が発生する。呼び出し単位で収集し、境界でマージするのが最も単純で安全。

⸻

9.6 引数の値解決（base / GUI / CC）
• すべての GUI 制御は「ノード生成時の引数解決」として実装する。
• 解決の入力（規範）
• base_value: user code から渡された値（式評価後）
• ParamState（スナップショットから取得）
• cc_snapshot（スナップショット）
• param_meta（型・範囲・step）
• 解決規則（規範） 1. ParamState.cc が設定されている場合、CC 制御を優先する：
• u = cc_snapshot[cc]（0..1、未定義は 0.0）
• effective = map_cc(u, ui_min, ui_max, meta)（型に応じた写像） 2. そうでなく ParamState.override が True の場合、effective = ui_value 3. それ以外は effective = base_value 4. effective を型検証し、必要なら clamp/離散化を行う（型規則は meta に従う） 5. float を含む場合は step を用いて量子化し、署名と計算に同一の effective を渡す
• CC 写像（推奨）
• float: min..max を線形補間
• vecN: 各成分を線形補間（ui_min/ui_max は成分ごと、またはスカラなら全成分に適用）
• bool: u >= 0.5 を True
• enum: index = round(u\*(n-1))（範囲に clamp）
• str: CC 制御は未対応（禁止してよい）

意思決定メモ: 優先順位を固定しないと「cc を割り当てたのにスライダーが勝つ」「override を切っても固定値が残る」など UI が不安定になる。CC > GUI > base の 3 段に固定すると説明可能性が高い。

⸻

9.7 正規化・署名・キャッシュとの整合
• G/E のノード生成は、解決後の effective 値を args 正規化（canonicalization）にかけた上で Geometry を生成する（規範）。
• float の量子化は、以下を満たすように行う（規範）
• GeometryId の署名に入る値と、実計算（PrimitiveRegistry/EffectRegistry）に渡す値が完全に一致する。
• NaN/inf は reject（ValueError）。
• -0.0 は +0.0 に正規化。
• これにより、parameter_gui の操作は GeometryId を正しく変化させ、realize_cache は自然に整合する（規範）。

意思決定メモ: GUI で値を動かした結果が「同じ GeometryId のまま違う配列」になると、キャッシュが破綻して再現不能になる。解決 → 量子化 → 正規化 → 署名の順序を固定し、計算値も同一にするのが必須。

⸻

9.8 GUI 表示モデル（3 列）と連番
• GUI の行は ParameterKey ごとに 1 行を基本とする。
• 列構成（既存仕様の具体化）
• 1 列目（ラベル列）
• group_label: "{op} #{ordinal}" を基本とし、引数名は必要なら補助表示する（例：scale #2 / s）。
• 2 列目（制御 UI 列）
• meta 型に応じた UI を表示する（float=スライダー、vecN=成分スライダー、str=テキスト、enum=ラジオ/ドロップダウン、bool=トグル）。
• override の有効/無効を切り替える UI（例：BASE/GUI トグル）を必須とする。
• 3 列目（ui_min/ui_max/cc 入力列）
• ui_min/ui_max はスライダーのレンジとして使用する。
• cc は MIDI CC 番号を設定する。設定時は CC 制御が優先される。
• ordinal（# の連番）の決め方（規範）
• op ごとに、初めて観測した site_id の順に 1,2,3… を割り当てる。
• 同一 op & site_id は常に同じ ordinal を持つ（GUI プロファイルが続く限り）。

意思決定メモ: 連番は “実行時に見つかった順” に固定するのが最も実装が単純で、ユーザーの認知とも一致する。ソート（行番号順など）は編集で揺れやすく、運用上の安定性が落ちる。

⸻

9.9 制約・既知の挙動
• ループ内で同一呼び出し箇所（同一 site_id）が複数回実行される場合
• それらは同一 ParameterKey を共有し、1 つの GUI 行でまとめて制御される（規範）。
• コード編集による site_id 変化
• 行の移動・式の組み替え等で site_id が変わると、保存済み GUI 状態が一致しない場合がある（仕様）。
• draw 外で Geometry を構築するパターン
• parameter_gui による動的制御を前提とする場合、G/E によるノード生成は runner 管理下（draw 呼び出し）で行うことを推奨する。

意思決定メモ: “呼び出し箇所”をキーにする以上、ループの反復やコード編集の影響は避けられない。これを仕様として明文化し、必要なら将来「明示キー」拡張で解決する余地を残す。

⸻

9.10 例外・ログ（GUI 入力の扱い）
• GUI 入力はユーザーコードと異なり、運用上は「落とす」より「安全に無効化」を優先してよい（推奨）。
• 具体方針（推奨）
• ui_min/ui_max の不正（ui_min>=ui_max、型不一致）はその項目を無視し、最後の有効値または meta 既定にフォールバックし、HUD/ログに警告を出す。
• cc の不正（範囲外、非整数）は無視して警告。
• ui_value の型不一致やパース失敗は override を一時無効化して警告（または前回値を保持）。

意思決定メモ: GUI は反復的に編集され、無効な中間状態（入力途中）を頻繁に通過する。ここで例外を投げて落とすと創作体験を壊しやすい。一方で、無言で無視すると原因不明になるため、HUD/ログの警告は必須とする。
