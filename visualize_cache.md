「キャッシュが思想通りに効いているか」を見るなら、
単に「速くなった気がする」ではなく、 1. どの Geometry ノードがどのフレームで「計算されたか」 2. どのノードが「キャッシュヒットで済んだか」

を可視化するのが一番わかりやすいです。

そのためにやることは大きく言うと二段階で、
まず realize の内部でイベントログを取れるようにしておき、そのログを元に「DAG ＋色」で見せる、という形にするときれいに整理できます。

⸻

まず、realize の中で最低限この三種類を区別して記録できるようにします。
• キャッシュヒットした（realize_cache からそのまま取れた）
• inflight の完了を待った（別スレッドが計算中のものを使った）
• 自分が実際に計算した（primitive/effect/concat を評価した）

realize を薄くラップして、例えば次のようなイベントログを残します。

from enum import Enum, auto
from dataclasses import dataclass, field

class RealizeEventType(Enum):
CACHE_HIT = auto()
INFLIGHT_WAIT = auto()
COMPUTE = auto()

@dataclass
class RealizeEvent:
geom_id: str
op: str
event_type: RealizeEventType # あれば depth, duration なども入れる
depth: int | None = None

@dataclass
class FrameRealizeLog: # 1 フレーム分のログ
events: list[RealizeEvent] = field(default_factory=list)

    def record(self, geom_id, op, event_type, depth=None):
        self.events.append(RealizeEvent(geom_id, op, event_type, depth))

# スレッドごと／フレームごとに、このログを差し替える

current_frame_log: FrameRealizeLog | None = None

---

「Geometry DAG をグラフとして可視化する」方法です。
これは Graphviz か NetworkX を使うのが現実的です。
純粋に DAG だけを描きたいなら Graphviz（graphviz パッケージ＋ dot 出力）が一番手数が少なくて済みます。

例えば、ルート Geometry から再帰的にたどって DOT を吐くユーティリティを一つ用意しておきます。

from graphviz import Digraph

def export_geometry_dag(root: Geometry, frame_log: FrameRealizeLog, filename: str):
dot = Digraph(comment="Geometry DAG")
visited = set()

    # イベントを id -> event_type にまとめる（最後のイベント優先）
    last_event: dict[str, RealizeEventType] = {}
    for ev in frame_log.events:
        last_event[ev.geom_id] = ev.event_type

    def visit(g: Geometry):
        if g.id in visited:
            return
        visited.add(g.id)

        status = last_event.get(g.id)
        if status == RealizeEventType.COMPUTE:
            color = "red"
        elif status == RealizeEventType.CACHE_HIT:
            color = "green"
        elif status == RealizeEventType.INFLIGHT_WAIT:
            color = "orange"
        else:
            color = "gray"

        label = f"{g.op}\n{g.id[:6]}"
        dot.node(g.id, label=label, style="filled", fillcolor=color)

        for child in g.inputs:
            visit(child)
            dot.edge(g.id, child.id)

    visit(root)
    dot.render(filename, format="png", cleanup=True)

これで、そのフレームで root を描いたときの DAG が
• 計算したノードは赤
• キャッシュヒットは緑
• inflight 待ちはオレンジ
• 触れていないノードは灰色

といった色分けで一枚の PNG に出せます。
フレームごとにこれを出して眺めると、「初回フレームでは葉ノードや一部の中間ノードが赤く、その後はほとんど緑になるか」「パラメータを少し動かしたときに、どのサブグラフが赤くなり直しているか」が視覚的に分かります。

ここで重要なのは、「DAG 自体は Geometry の op/inputs/id から復元し、色付けだけを FrameRealizeLog で決める」ことです。
こうすると、キャッシュ機構を弄っても DAG 可視化のロジックはほぼ固定のままにできます。
