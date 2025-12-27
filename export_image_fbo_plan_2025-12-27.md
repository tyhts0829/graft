# `export_image`（FBO オフスクリーン PNG）実装計画（2025-12-27）

## 目的

旧プロジェクトの「方式 2（FBO 経由で高解像度・オーバーレイ除外）」と同じ発想で、
`src/grafix/export/image.py` に **PNG 保存**を実装する。

- 画面スクショではなく「ラインのみ」を保存できる経路を用意する
- `scale` により高解像度で書き出せる（例: 300→1200 なら scale=4）
- `transparent`（透過背景）を扱えるようにする（背景 α=0 で clear）

## 制約（リポ内のルール）

- `tests/architecture/test_dependency_boundaries.py` により、`src/grafix/export/` は
  `moderngl` / `pyglet` / `grafix.interactive` を import できない。
- よって export 側は **duck typing（`ctx: object`）**で受け取り、PNG 書き出しも標準ライブラリで完結させる。
  （依存追加はしない）

## 実装方針（API）

`src/grafix/export/image.py` を「layers→ 画像」ではなく、
**「draw callback を FBO に描いて PNG にする」**関数として定義し直す（破壊的変更 OK）。

提案シグネチャ（案）:

- `export_png_offscreen(ctx: object, draw: Callable[[], None], path: str|Path, *, size_px: tuple[int,int], background_color: tuple[float,float,float]=(1,1,1), transparent: bool=False) -> Path`

ポイント:

- `ctx` は ModernGL Context を想定するが、export パッケージ側では型を固定しない
- `draw()` は「現在 bind 済みの framebuffer に、viewport 前提で描く」関数を呼び出し側が渡す

## 実装方針（処理フロー）

1. 入力検証
   - `size_px=(w,h)` が正の int
2. 退避
   - `old_viewport = ctx.viewport`（取れなければ None 扱い）
3. FBO 作成
   - `fbo = ctx.simple_framebuffer((w,h), components=4)`（RGBA）
4. FBO bind + clear
   - `fbo.use()`
   - `fbo.clear(r, g, b, a)`（`transparent=True` なら a=0）
5. viewport 設定
   - `ctx.viewport = (0, 0, w, h)`
6. 描画
   - `draw()`
7. readback
   - `data = fbo.read(components=4, alignment=1)`（RGBA bytes）
   - 画像の上下向きが期待と逆なら **vflip**（後述の PNG writer 側で対応）
8. 後始末
   - `ctx.viewport = old_viewport`（取れていれば）
   - `ctx.screen.use()`（戻せる場合）
   - `fbo.release()`
9. PNG 書き込み
   - `data`（RGBA）を **標準ライブラリのみ**で PNG 化して `path` に保存

## PNG writer（標準ライブラリ実装）

`image.py` 内に private 関数として最小 PNG エンコーダを実装する（依存追加回避）。

- 入力: `(w, h, rgba_bytes)`（len = `w*h*4`）
- 出力: PNG bytes（IHDR/IDAT/IEND、zlib 圧縮、CRC32）
- 走査線: filter type 0（None）
- `vflip` 対応: `flip_y: bool` 引数で scanline 順を反転できるようにする

## 変更予定ファイル

- `src/grafix/export/image.py`（スタブ撤去、FBO オフスクリーン PNG 実装）
- `src/grafix/api/export.py`
  - 破壊的変更に追従（将来 headless export を繋ぐなら api 側で ctx を用意する）
- テスト
  - `tests/export/test_image_png_writer.py`（PNG writer のみを決定的にテスト）
  - 可能なら `tests/export/test_image_offscreen_api.py`（dummy ctx で「呼び出し順」だけ検証）

## TODO（チェックリスト）

- [ ] `src/grafix/export/image.py` の API を確定（`export_image` 名を残すか、`export_png_offscreen` にするか）
- [ ] FBO キャプチャ処理を実装（viewport 退避/復元、FBO 作成/解放、clear/draw/read）
- [ ] PNG writer 実装（RGBA→PNG、vflip 対応、エラー文言を簡潔に）
- [ ] `tests/export/test_image_png_writer.py` 追加（2x2 などで PNG 復号して一致検証）
- [ ] 既存呼び出し元（`src/grafix/api/export.py`）の追従 or 撤去（未使用なら削る）
- [ ] 手動確認（interactive 側で `draw()` を渡して 1200px 出力を確認）

## 事前確認したい点

- [ ] `export_image` の公開 API は「layers ベース」より「draw callback ベース」で OK？（今回は方式 2 の踏襲を優先）
- [ ] `transparent=True` のときは「背景だけ α=0、線は不透明」で OK？
- [ ] vflip は「常に反転」か「必要時のみ（テストで確定）」どちらで進める？
