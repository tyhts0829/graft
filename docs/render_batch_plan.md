# render_batch_plan.md

どこで: `src/render/frame_pipeline.py`, `src/api/layers.py`, `src/render/index_buffer.py`（必要なら）, `src/render/draw_renderer.py` など描画パス。
何を: 同一 Layer 内の複数 Geometry をまとめて 1 度の VBO/IBO アップロードで描画するバッチ化を導入する計画。API で指定された color/thickness は Layer 単位なので、Layer 内でのスタイル統一を前提に安全にまとめられる。
なぜ: 現行は Layer 内の Geometry を 1 個ずつ `render_layer` しており、100 個の円で 100 回のアップロード/描画が走る。まとめれば転送回数を Layer 数に抑え、大幅なパフォーマンス改善が見込める。

## 0. ゴール
- Layer 内の Geometry を concat し、`render_layer` を Layer ごとに 1 回にする。
- 既存 API（L に Geometry/リストを渡す）を変えずに内部でバッチ化する。
- indices 生成と VBO/IBO 転送の回数を Layer 数に比例させる。

## 1. 方針
- `render_scene` で Layer 単位に Geometry を concat する。具体的には `normalize_scene` の後、各 Layer について:
  - Geometry が単体ならそのまま。
  - Geometry のリストなら `concat_realized_geometries` 相当の「Geometry concat」を行うためのユーティリティを追加（offsets/coords を足し合わせて 1 Geometry を作る）。
- concat 後の Geometry を 1 回 `realize` → 1 回 `build_line_indices` → 1 回 `render_layer`。
- index 生成は既存 `build_line_indices` をそのまま利用（concat で offsets が一体化する）。
- DrawRenderer 側の API 変更は不要。渡す realized/indices がまとめられるだけ。

## 2. タスク分解（チェックリスト）
- [ ] `src/render/geometry_batch.py`（新規）: Geometry の列を 1 つにまとめるヘルパを実装。inputs/args を結合し、id は再計算する。
- [ ] `frame_pipeline.py`: Layer ごとに Geometry をまとめる前処理を追加し、まとめた Geometry だけを realize する。
- [ ] `api/layers.py`: L(...) でリストを受け取ったときに Layer 1 つに複数 Geometry を保持できるよう型注釈と説明を確認（実装は既にリスト→複数 Layer だが、ここで「concat する」ため Layer 内に Geometry のリストを許容する形に整理）。
- [ ] テスト: `tests/render/test_batching.py` を追加し、
  - 複数 Geometry を含む Layer を渡したときに `render_layer` 呼び出しが 1 回になること（モック renderer で検証）。
  - concat の offsets/coords が正しく結合されること。
  - 既存単体ケースが壊れないこと。
- [ ] ドキュメント: `parameter_gui_plan` には変更不要。`render_batch_plan.md` を完了としてチェック（このファイル）。
- [ ] Uniform 最適化: 射影行列と line_thickness/color の設定をフレーム/Layer 単位にまとめ、Geometry ごとに書き直さないようにする。
- [ ] IBO/VBO 再利用: offsets 署名をキーに IBO をキャッシュし、同一署名の Layer では VBO のみ更新するパスを追加する（Freeze 相当）。

## 3. リスク・留意点
- Geometry concat で `GeometryId` を再計算する必要がある（inputs/args 依存なので、concat ヘルパで blake2b を再利用）。
- offsets が 32bit int のままで十分か確認（小規模なら問題なし）。
- Layer 内のスタイル統一を前提にしているため、今後 Layer に per-Geometry の style を入れる設計に変える場合は再検討が必要。
- realize_cache は concat 後 GeometryId 単位で動くので、キャッシュ効果はむしろ向上する見込み。

## 5. この前提（Layer内は同じ色・太さ）で効く追加ポイント
1) Layer内バッチ描画  
   - 色・太さが同一なら Geometry を concat して 1 回の `render_layer` で描ける。アップロード/ドロー回数が Layer 数に圧縮される。
2) Uniform 設定回数の削減  
   - Layer 内では色・線幅を一度だけ設定すればよく、Geometry ごとの Uniform 更新が不要になる。
3) バッファ再利用の粒度向上  
   - offsets 署名が Layer 単位になり、IBO 再利用や VBO-only 更新の最適化が効きやすくなる。

## 4. 完了定義
- Layer 内 Geometry を concat し、`render_layer` 呼び出しが Layer 数に比例する実装になっている。
- 新規テストが通り、既存描画が崩れない。
- main.py のような多数オブジェクトでもカクつきが軽減されることを手動確認。
