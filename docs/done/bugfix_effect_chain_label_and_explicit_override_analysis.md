# bugfix_effect_chain_label_and_explicit_override_analysis.md

どこで: effect ヘッダ名は `src/app/parameter_gui/labeling.py` / `src/app/parameter_gui/store_bridge.py`。effect chain の識別は `src/api/effects.py`。永続化と merge は `src/parameters/store.py` / `src/parameters/persistence.py`。
何を: (1) effect チェーンのデフォルト名 `effect#N` が run 間で増える問題、(2) 明示 kwargs 指定時に override が False にならない問題を、現行実装に即して原因整理し、修正方針を提案する。
なぜ: 小さなコード編集（引数追加など）で GUI 表示名や override 状態が揺れ、ユーザビリティが落ちるため。

---

## 1. バグ1: `eff2` が `effect#2` / `effect#3` になる

### 1.1 期待される挙動（ユーザー要求）

- `eff1` は `E(name="triple_scale")...` のように明示名があるので、その名前で表示される。
- `eff2` は名前なしなので **`effect#1`** でよい。
- `main.py` を閉じて JSON 永続化 → `ply1` に引数を追加 → 再実行しても、`eff2` が `effect#2` や `effect#3` に増えないでほしい。

### 1.2 現行実装（どこで何が決まっているか）

- effect chain の識別子 `chain_id` は `EffectNamespace.factory()` 内で `site_id = caller_site_id(skip=1)` を採用している。
  - つまり `chain_id` は「E.* を呼んだ呼び出し箇所の site_id」そのもの。(`src/api/effects.py`)
- GUI の effect チェーンヘッダ名は `effect_chain_header_display_names_from_snapshot()` が決める。(`src/app/parameter_gui/labeling.py`)
  - `E(name=...)` がある場合は label を採用
  - label が無い場合は `ParamStore.chain_ordinals()` の値を使って `effect#N` を生成
- `ParamStore` は `chain_id -> ordinal` を `_chain_ordinals` として永続化している。(`src/parameters/store.py`)
  - 新しい chain_id が現れると `len(_chain_ordinals)+1` を割り当てる

### 1.3 根本原因（なぜ `#2` / `#3` になるか）

原因は 2 つが重なっている。

1) **「名前付きチェーン」も `effect#N` の採番母集団に入っている**

- 現在は chain ordinal が「label の有無に関係なく」採番されるため、
  - `eff1` (label あり) が ordinal=1
  - `eff2` (label なし) が ordinal=2
  → `eff2` が `effect#2` になる。

2) **chain_id が site_id 由来なので、無関係に見える編集でも chain_id が変わり得る**

- `site_id` は現状 `"{filename}:{co_firstlineno}:{f_lasti}"`。(`src/parameters/key.py`)
- `f_lasti` はバイトコード位置なので、同一関数内の前の行に引数を足したりすると、その後の命令オフセットもズレる可能性がある。
- その結果、`eff2` の `chain_id` が別文字列として扱われ、`_chain_ordinals` に新規キーとして追加される。
  - 例: 前回 `eff2` が ordinal=2 だった → 今回は別 chain_id になって ordinal=3 → `effect#3` になる。

補足:

- 現在の reconcile/prune は「(op, site_id) グループ」単位を主対象にしており、**chain_id 自体の同一性**は扱っていない。
- `snapshot_for_gui()` で stale な行は隠せても、`chain_ordinals` の数値自体は残るため、表示名が `effect#3` になる余地がある。

### 1.4 修正案（おすすめ）

#### 方式A（最小・おすすめ）: `effect#N` を「無名チェーンだけで正規化」して表示する

`effect_chain_header_display_names_from_snapshot()` で `effect#N` を作るとき、

- 「label が無いチェーン」のみを対象に、表示用の `N` を 1..K に振り直す
- label があるチェーンは採番から除外する

これで:

- `eff1` が label ありでも、`eff2` は常に `effect#1`
- chain_id が変わって内部 ordinal が 3 になっても、無名チェーンが 1 つなら表示は `effect#1` のまま

副作用/トレードオフ:

- 無名チェーンが複数ある場合、表示順（どれが #1/#2 になるか）は「その時点のチェーン順」に依存する。
  - ただし「label を付ければ固定できる」ので回避は容易。

#### 方式B（重め）: chain_id の reconcile（旧 chain_id -> 新 chain_id）まで行い、ordinal を移植する

- チェーンを「ステップ列の fingerprint」でマッチさせ、旧 chain_id の ordinal を新 chain_id に移す。
- これは “正しさ” は上がるが、実装コストと曖昧性が増える（本件の要求に対して過剰になりがち）。

現時点の要求（`eff2` を `effect#1` に固定し、編集で `#3` に増えない）には方式Aで十分。

---

## 2. 横道: `eff2` のような「変数名を自動でラベル表示」できるか？

結論: **技術的には可能だが、素直で堅牢な実装になりにくいので非推奨**。

理由:

- Python は RHS（`E.scale().rotate()`）の評価時点では、代入先の変数名（`eff2`）を保持していない。
- 自動化するには、呼び出し元の source を `inspect` で取って AST 解析し、`f_lineno`/`f_lasti` 等から「この式がどの代入文に属するか」を推定する必要がある。
  - 1 行 1 式の前提が崩れると急に壊れる（複数代入、複数行、条件式、関数化、フォーマッタ等）。
  - `inspect.getsource()` が取れない環境もある（REPL、zipapp、最適化等）。

代替（おすすめ）:

- 既に `E(name="...")` があるので、**明示で `E(name="eff2")...` と書く**のが一番確実。
  - 文字列 1 個の手入力で “意図がコードに残る” ため、将来の読みやすさも上がる。

---

## 3. バグ2: `type_index=1` を明示しても GUI の override が False にならない

### 3.1 期待される挙動（ユーザー要求）

- `G.polyhedron(type_index=1)` のように「コードが明示指定した引数」は、GUI 側の override が **False**（=コードの base を使う）でいてほしい。
- 永続化復元時に、過去の GUI 状態が優先されて override=True のままになるのは困る。

### 3.2 現行実装（なぜ起きるか）

現行の override 初期化ポリシーは以下:

- `FrameParamRecord.explicit`（ユーザーが kwargs を明示指定したか）を `ParamStore.store_frame_params()` に渡す。(`src/parameters/resolver.py`)
- 初回だけ `ParamStore.ensure_state(..., initial_override=not explicit)` で override を初期化する。(`src/parameters/store.py`)
  - 明示引数 → initial_override=False（コード優先）
  - 省略引数 → initial_override=True（GUI 優先）

しかし:

- 永続化復元後は state が既に存在するため、`ensure_state()` は **override を更新しない**。
- 結果として「以前は省略だった → override=True が保存されていた」状態で、
  次に「明示に変えた」場合でも override が True のまま残る。

### 3.3 修正案（おすすめ）

「明示/省略の変更」を検知し、**ユーザーが手で弄っていない範囲だけ** override を追従させる。

ここで重要なのは「永続化された override を全部信用する」でも「毎回リセットする」でもなく、
**“ポリシー由来の初期値” と “ユーザー意思での変更” を分けて扱う**こと。

最小のアイデア:

- 前回観測した `explicit`（bool）を ParameterKey 単位で記録し、JSON にも保存する
- 次回観測で `explicit` が変化したときだけ、
  - 現在の `override` が「前回 explicit に対する既定値」と一致している場合は、既定値を更新する
  - 一致していない場合はユーザー変更とみなして上書きしない

例:

- 前回: implicit（explicit=False）→ 既定 override=True
- 今回: explicit=True に変わった
  - もし override=True のままなら「既定値のまま」なので override=False に切り替える（ユーザー要求を満たす）
  - もし override=False だったら、ユーザーが切っていた可能性が高いので維持する

この方式のメリット:

- 「明示にしたのに GUI が勝つ」を止められる
- ユーザーが override を明示的に切り替えた場合は尊重できる

---

## 4. 修正チェックリスト（次の実装タスク）

※このファイルは分析結果なので、実装は別タスクで消し込む。

### effect chain の `effect#N` 正規化

- [ ] `src/app/parameter_gui/labeling.py` の `effect_chain_header_display_names_from_snapshot()` を変更
  - [ ] label ありチェーンを採番母集団から除外
  - [ ] 表示用 `N` を無名チェーンのみに 1..K で正規化（chain_ordinal のギャップ吸収）
- [ ] pytest 追加
  - [ ] label あり + 無名 1 本で無名が常に `effect#1`
  - [ ] chain_id が変わって内部 ordinal が増えても表示は `effect#1` のまま

### explicit/implicit 変化時の override 追従

- [ ] `ParamStore` の JSON に `explicit`（前回観測値）を保存できるようにする
- [ ] `store_frame_params()` マージ時に explicit 変化を検知し、条件付きで override を更新する
- [ ] pytest 追加
  - [ ] implicit→explicit で override が False へ戻る（ただしユーザー変更と推定される場合は維持）

