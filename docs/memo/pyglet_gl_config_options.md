# どこで: `docs/pyglet_gl_config_options.md`。
# 何を: `src/app/draw_window.py` が指定している `pyglet.gl.Config`（OpenGL 設定テンプレート）の各オプションを、意味・影響・トレードオフ込みで整理する。
# なぜ: `Config` と `Window` の責務が混ざりやすく、見た目/性能/互換性の調整ポイントを後から追えるようにするため。

## 対象（このリポでの利用箇所）

- 描画ウィンドウ: `src/app/draw_window.py` の `create_draw_window()` が `pyglet.gl.Config(...)` を作り、`pyglet.window.Window(..., config=config)` に渡す。
- Parameter GUI: `src/app/parameter_gui/pyglet_backend.py` でも同様に `pyglet.gl.Config(...)` を作り、GUI 用の `Window` に渡す。

このドキュメントは、手元の環境で確認できる pyglet `2.1.11` の `Config` 実装（`pyglet/gl/base.py`）に基づく。

## `Config` は「希望条件（テンプレート）」である

`pyglet.gl.Config` は「この属性を満たす OpenGL フレームバッファ/コンテキストが欲しい」という希望条件（テンプレート）を表す。

- `Config` で指定した値は、原則として「最低限この条件を満たすもの」を要求する。
  - 例: `samples=4` は「4 サンプル“以上”」の意味合いになる（実際に 4x になるとは限らない）。
- `Window` 生成時に、画面（screen）に存在する実在の設定（complete config）へ解決される。
  - テンプレートがそのまま使われるのではなく、内部で `screen.get_best_config(config)` のような解決が行われる。
- 要求が厳しすぎると、マッチする設定が存在せず例外になる。
  - 代表例: MSAA（`sample_buffers`/`samples`）や特定の GL バージョン指定が環境にない場合。

## `src/app/draw_window.py` で指定している項目（詳細）

### `double_buffer=True`

ダブルバッファ（フロント/バックの 2 枚）を要求する。

- 何が変わるか
  - 描画先は基本的にバックバッファになり、最後に `flip()`（バッファ交換）で表示が更新される。
  - 描画途中の状態が画面に出にくくなり、ちらつき（tearing/partial draw）が起きにくい。
- トレードオフ
  - バックバッファを持つ分だけメモリは増える（通常は許容）。
  - `flip()` を前提とした描画ループ設計になる（このリポは `MultiWindowLoop` で `flip()` を集約している）。

### `sample_buffers=1` と `samples=4`（MSAA）

MSAA（Multi-Sample Anti-Aliasing）を要求する 2 つの指定。

- `sample_buffers=1`
  - マルチサンプル用のバッファを使う（0 ではない）ことを要求する。
  - ここが 0/未指定だと、`samples` を指定しても実際には MSAA が無効になりうる。
- `samples=4`
  - 1 ピクセルあたりのサンプル数を最低 4 以上にすることを要求する（典型的には 4x MSAA）。
- 何が良くなるか
  - 斜め線や曲線のエッジのジャギーが軽減され、線描画の見た目が滑らかになりやすい。
  - 「線を滑らかにしたい」という用途では効果が分かりやすい。
- トレードオフ
  - 塗りつぶし負荷/メモリ帯域/VRAM 使用量が増える。
  - 環境によっては要求を満たせず `NoSuchConfigException` などで `Window` 生成が失敗する可能性がある。
  - 高い値（例: 8, 16）にすると失敗率も上がるので、まず 4 を基準にするのが無難。

### `vsync=True`（注意: `Config` の項目ではない）

このリポの `create_draw_window()` では `Config(..., vsync=True)` の形で指定しているが、pyglet 2.1.11 の `Config` が持つ公式な属性一覧に `vsync` は含まれない。

- 結論
  - `Config` 側に `vsync` を渡しても、**実質的に無視される**（`Config` は未知のキーワードを単に属性として載せない）。
- VSync はどこで制御すべきか
  - `pyglet.window.Window(vsync=...)` に渡す（ウィンドウ単位の指定）
  - もしくは `pyglet.options["vsync"] = True/False` を **Window 作成前** に設定する（グローバルの既定値）
- このリポの現状
  - `src/api/run.py` で `pyglet.options["vsync"] = True` を Window 作成前に設定しているため、描画ウィンドウはそこで VSync が有効化される想定。
  - GUI 側は `src/app/parameter_gui/pyglet_backend.py` が `Window(vsync=bool(vsync))` を渡している。

## `Config` で指定できる主な項目（一覧 + 使いどころ）

以下は pyglet 2.1.11 の `Config` が認識する属性（`Config._attribute_names`）を、用途ごとにまとめたもの。

### バッファ構成（色/深度/ステンシル）

- `red_size`, `green_size`, `blue_size`, `alpha_size`
  - それぞれの色成分のビット深度を要求する。
  - `alpha_size=8` を要求すると「透明」を扱えるフレームバッファが取りやすい（ただし実際の透過表示は OS/ウィンドウスタイル依存）。
- `buffer_size`
  - 色バッファ合計のビット数を要求する（成分別指定と併用すると解釈が難しくなるので、どちらかに寄せるのが無難）。
- `depth_size`
  - 深度バッファのビット数を要求する（3D の深度テストを使うなら重要）。
  - 2D ライン描画中心なら省略しても成立しやすい。
- `stencil_size`
  - ステンシルバッファのビット数を要求する（マスク/クリップ/アウトライン等で使う）。

### アンチエイリアス（MSAA）

- `sample_buffers`
  - MSAA バッファ有無の要求（`1` を指定すると「MSAA を使う」方向へ寄る）。
- `samples`
  - サンプル数の最低値要求（例: `4`）。

### 追加・特殊バッファ

- `aux_buffers`
  - 補助カラーバッファの数（一般用途では使わないことが多い）。
- `accum_red_size`, `accum_green_size`, `accum_blue_size`, `accum_alpha_size`
  - アキュムレーションバッファ（古い用途が中心で、現代の描画ではほぼ使わない）。
- `stereo`
  - 左右のステレオバッファを要求する（特殊用途）。

### OpenGL コンテキストの条件

- `major_version`, `minor_version`
  - 希望する OpenGL バージョンを要求する。
  - 高くしすぎると起動不能になりやすい（特に macOS は最大が限定される）。
- `forward_compatible`
  - forward compatible なコンテキストを要求する。
  - 互換性の都合で古い機能が使えない（＝ Core Profile 寄り）になることがある。
- `opengl_api`
  - `"gl"`（通常の OpenGL）か `"gles"`（OpenGL ES）などの API 系統を指定する。
- `debug`
  - デバッグコンテキストを要求する。
  - ドライバによっては性能低下/ログ増加があり得るが、開発中の GL エラー検出に有用。

### 透明フレームバッファ

- `transparent_framebuffer`
  - フレームバッファの透明化を要求する（OS やウィンドウスタイルの制約が大きい）。
  - 透明系を狙う場合は通常 `alpha_size=8` とセットで考える。

## `Window` 側のオプション（混同しやすいもの）

`vsync` のように「`Config` ではなく `Window` の引数」で制御する項目もある。
このリポで触る可能性が高いものだけ挙げる。

- `vsync`
  - バッファ `flip` をディスプレイの垂直同期に合わせるかどうか。
- `resizable`, `fullscreen`, `visible`, `caption`
  - ウィンドウの振る舞い/見た目の基本設定。
- `style`（例: `transparent`, `overlay` など）
  - 透明系のウィンドウスタイル指定と連動し、内部で `alpha_size`/`transparent_framebuffer` が調整される場合がある。
- `screen`, `display`, `mode`
  - マルチディスプレイやフルスクリーン時のターゲット指定に関わる（用途が明確なときだけ触る）。
- `context`
  - 既存コンテキストをアタッチする特殊用途（通常は `config` から生成させる）。

