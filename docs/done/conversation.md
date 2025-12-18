方向性は正しいです。いま pyglet や moderngl を“作品生成の入口”側でインポートしているなら、そこがヘッドレス一括出力の最大の障害になります。解決策は単純で、「幾何生成（DAG→RealizedGeometry）と描画（PNG/SVG などへのレンダリング）を、依存関係として完全に分離する」ことです。描画は後段の“アダプタ”に押し込み、作品スクリプトとコアは GPU/Window 依存をゼロにします。

設計としては、grafix を少なくとも次の 3 層に分けるのが堅いです。まず Core 層は Geometry/Effector/Realize/Layer/Parameter など、純粋にデータと計算だけを持ちます。次に Export/Render 層は、Core の出力（例えば Layer 列と RealizedGeometry）を受け取って、SVG/PNG/G-code などに変換します。最後に Interactive 層が pyglet / moderngl を使ってウィンドウ表示やリアルタイム操作を提供します。Interactive は「あると便利」だが「なくても作品は生成できる」を徹底します。

現実的な最短ルートは「まず SVG を吐けるようにする」です。SVG はヘッドレスで確実に生成でき、あなたの“線の作品”と相性が良い。サムネ PNG が必要なら、SVG→PNG のラスタライズを後段で行えばよい（この後段だけに cairo などを依存させれば、GPU 依存は消えます）。さらに、ペンプロッタやベクタ資産とも一貫します。将来的に GL レンダラを足すのも容易です（SVG と GL が同じレンダラ・インターフェースを実装するだけ）。

実装の勘所は「インポートを動的にする」ことです。Core や作品スクリプトで pyglet を import してしまうと、その時点でヘッドレス実行が壊れます。GL/Window 系は grafix/renderers/gl_pyglet.py のようなモジュールに閉じ込め、CLI で --renderer gl を指定したときだけ遅延 import される形にします。例えばこうです。

# grafix/rendering/load_renderer.py

def load_renderer(name: str):
if name == "svg":
from .svg_renderer import SvgRenderer
return SvgRenderer()
if name == "gl":
from .gl_pyglet_renderer import GlPygletRenderer # ここで初めて pyglet/moderngl が必要
return GlPygletRenderer()
raise ValueError(name)

この分離ができると、grafix thumbs は「作品スクリプトを読み込む →build()して Layer を得る →realize→SVG/PNG に吐く → 一覧 HTML/インデックス JSON 生成」という単純なパイプラインになります。Codex にやらせるときも、ここが安定しているほど反復が回ります。

もう一段だけ現実的な注意点があります。PNG に直接レンダする CPU レンダラを自作する場合、ポリラインの太さ、結合（join）、端点（cap）、アンチエイリアスをどうするかで品質差が出ます。これを自前でやるのは意外に面倒なので、当面は「SVG を正として出す」か、CPU レンダは既存の 2D 描画バックエンド（Cairo や Skia 等）に任せる方がコストが低いです。あなたの目的はまず“サムネ一括出力で作品の探索を回す”ことなので、品質が完全でなくても、安定して大量に吐ける方が価値が高いです。

結論としては、いまやるべき変更は「作品スクリプトと Core から、pyglet/moderngl 依存を完全に追い出して、レンダラを選択可能な後段アダプタに隔離する」。この一点です。ここまで行けば、ヘッドレス一括サムネ出力はすぐに成立しますし、その上でインタラクティブ表示は“別レンダラ”として維持できます。必要なら、あなたの現状のディレクトリ構成を前提に、最小のファイル分割案（どのモジュールから何を移すか）まで具体化して提示します。

---

src ディレクトリ配下に core、export、interactive の 3 つのディレクトリを切ると筋が良い？あるいはディレクトリ構成とは切り離したほうがいい？

---

筋は良いです。特に「ヘッドレス一括出力」と「対話 UI（pyglet/moderngl）」を両立したいなら、ディレクトリ（=Python パッケージ）として境界を可視化しておくメリットが大きいです。Codex に作業させる場合も、「この層は触ってよい／触ると壊れる」が構造で伝わるので、事故が減ります。

ただし、ディレクトリを切るだけでは境界は守られません。結局は依存方向（core が interactive/export を絶対に import しない）を設計として固定し、必要なら機械的に検査するところまでセットでやるのが本質です。したがって「ディレクトリ構成とは切り離すべきか」という問いに対しては、切り離す必要はなく、むしろ構成で境界を表現しつつ、依存方向をルール化して守るのが現実解です。

実務的には、src/grafix/ の下を大きく 3 層に切って問題ありません。ただし名前は export より render / io / formats の方が意図が明確になりやすいです（export は“何でも入る”箱になりがちです）。例えばこういう形が運用しやすいです。

src/
grafix/
core/
geometry.py
effects.py
realize.py
layer.py
params.py
protocols.py # Renderer/Exporter の Protocol（インターフェース）だけ置く
render/ # ヘッドレス出力に直結する層（SVG/PNG/G-code など）
svg.py
gcode.py
raster.py # もし必要なら（cairo 等）
pipeline.py # thumbs/batch 等の共通パイプライン
interactive/ # ウィンドウ/GL/入力
pyglet_app.py
moderngl_renderer.py
controls.py
cli/
**main**.py
thumbs.py

このときの最重要ルールは、「core は純粋計算とデータ構造のみ」「render と interactive は core に依存してよいが逆は不可」です。Renderer のような抽象（Protocol や ABC）は core に置き、実装（SVG レンダラ、GL レンダラ）はそれぞれ render / interactive に置くと、循環参照が起きにくくなります。core からは typing.Protocol 越しに呼び出すだけにして、実体の import はしない、ということです。

もう一点、依存パッケージ管理も層分離とセットにすると強いです。core は numpy など最小依存に抑え、interactive は pyglet / moderngl を extras として分離し、render も（cairo 等を使うなら）extras に分ける。そうすると「ヘッドレス環境では interactive をインストールしない」という運用が可能になり、事故（import 時にウィンドウ初期化など）が構造的に起きなくなります。

結論としては、3 ディレクトリ分割は“筋が良い”。ただし成功条件は、(1) 依存方向を一方通行に固定する、(2) 抽象は core、実装は外側、(3) 依存ライブラリも extras で分ける、の 3 点です。これが揃うと、ヘッドレス一括サムネ出力の実装も、対話 UI の維持も、どちらも破綻しにくくなります。
