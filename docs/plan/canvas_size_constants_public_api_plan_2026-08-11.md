# Canvas size 定数を root 公開する計画（2026-08-11）

作成日: 2026-08-11  
ステータス: 承認待ち（未実装）

## 1. 依頼の解釈

本計画では「canvas サイズの定数」を、A4/A5 などの**静的な標準用紙寸法プリセット**と
解釈する。目標とする利用形は次である。

```python
from grafix import A5, A5_LANDSCAPE, G, run

CANVAS_SIZE = A5_LANDSCAPE


def draw(t: float):
    return G.circle(center=(105.0, 74.0, 0.0), radius=40.0)


if __name__ == "__main__":
    run(draw, canvas_size=CANVAS_SIZE)
```

`run(canvas_size=...)` で決まった現在値を、実行時に変化する
`CANVAS_WIDTH` / `CANVAS_HEIGHT` として global に公開する仕組みではない。後者は
複数の `RenderSession`、background worker、source reload と衝突し、「定数」にもならないため、
必要なら別の runtime context API として設計する。

## 2. 現状調査

- `RenderOptions.canvas_size` と `run(..., canvas_size=...)` の公開型は
  `tuple[int, int]` で、順序は `(width, height)` である。
- `RenderOptions` は正の整数からなる2要素 tuple を検証し、既定値は `(800, 800)` である。
- curated sketch には `(148, 210)`（A5縦）、`(210, 148)`（A5横）、
  `(210, 297)`（A4縦）、`(297, 210)`（A4横）の重複定義が多い。
- root `grafix` は PEP 562 の遅延 facade であり、公開名は
  `TYPE_CHECKING` import、`__all__`、`_PUBLIC_NAMES` の3箇所で明示管理している。
- root 公開面は `tests/api/test_public_type_graph.py`、遅延 import は
  `tests/api/test_lazy_facade.py`、project-local root stub は
  `src/grafix/devtools/generate_stub.py` の `_ROOT_STUB` で固定されている。
- `canvas_size` は renderer 全体では論理座標の寸法である。G-code 出力ではこの数値を mm として
  扱うが、PNG/SVG/preview 全体に物理単位を新設する変更ではない。

## 3. 検討した API 形状

| 案 | 利用形 | 評価 |
| --- | --- | --- |
| root の plain tuple 定数 | `from grafix import A5` | **採用**。現行引数へそのまま渡せ、変換や新しい型が不要 |
| Enum | `PaperSize.A5.value` | 不採用。`canvas_size=` へ直接渡せず、既存 tuple 契約に対して冗長 |
| dataclass / 独自 `CanvasSize` | `A5.width` など | 不採用。値オブジェクトと変換規約を増やす必要がない |
| registry / 文字列 lookup | `paper_size("A5")` | 不採用。固定値4個に対して過剰で、入力失敗経路も増える |
| runtime global 幅・高さ | `from grafix import CANVAS_WIDTH` | 不採用。session/worker ごとの値を module global 定数にはできない |

plain tuple は現在の validator、型注釈、export 経路のすべてとそのまま整合し、既存実装を
変更しない。tuple 自体が immutable なので runtime class や互換 wrapper も不要である。

## 4. 推奨する公開契約

初回は repository で実使用が集中している A4/A5 の縦横4定数に限定する。

| 公開名 | 値 | 意味 |
| --- | --- | --- |
| `A4` | `(210, 297)` | ISO 216 A4、縦向き |
| `A4_LANDSCAPE` | `(297, 210)` | ISO 216 A4、横向き |
| `A5` | `(148, 210)` | ISO 216 A5、縦向き |
| `A5_LANDSCAPE` | `(210, 148)` | ISO 216 A5、横向き |

方針は次のとおりとする。

- 値は ISO 216 の公称 mm 寸法を整数 tuple にしたものとする。
- 短い `A4` / `A5` は縦向きに固定し、横向きだけ明示的な suffix を付ける。
- 公開値は `Final[tuple[int, int]]` として型付けする。runtime の代入禁止機構は追加しない。
- `from grafix import ...` を正規入口とし、`grafix.api` 直下には重複公開しない。
- A0〜A3、A6以降、B系列、Letter/Legal、任意 pixel preset は、実需が生じた時に同じ
  plain tuple 方針で追加する。初回から registry や網羅的 catalog は作らない。
- `run()` / `RenderOptions` の既定 canvas `(800, 800)` は変更しない。

## 5. 配置と依存方向

新規 pure value module `src/grafix/core/canvas_sizes.py` を定義元とする。

```python
from typing import Final

A4: Final[tuple[int, int]] = (210, 297)
A4_LANDSCAPE: Final[tuple[int, int]] = (A4[1], A4[0])
A5: Final[tuple[int, int]] = (148, 210)
A5_LANDSCAPE: Final[tuple[int, int]] = (A5[1], A5[0])
```

- `canvas_size` と同じ core domain の不変値として置き、API、GUI、export、config へ依存させない。
- `src/grafix/core/__init__.py` は空の package boundary のままにし、そこから再 export しない。
- root facade は `_PUBLIC_NAMES` からこの定義 module を遅延 import する。
- `from grafix import A5` だけで `grafix.api`、`pyglet`、`grafix.interactive` を初期化しない。
- landscape 値は portrait 値から一方向に導出し、重複した数値のずれを防ぐ。

## 6. 実装計画

### 6.1 定数の定義

- [ ] `src/grafix/core/canvas_sizes.py` を追加する。
- [ ] module header/docstring で `(width, height)`、縦横、ISO 216 の公称 mm 値であることを説明する。
- [ ] 4定数を exact `tuple[int, int]` として定義し、module `__all__` を明示する。
- [ ] lookup、Enum、独自 value class、orientation helper は追加しない。

### 6.2 root facade への公開

- [ ] `src/grafix/__init__.py` の `TYPE_CHECKING` import に4定数を追加する。
- [ ] root `__all__` に4定数を追加する。
- [ ] `_PUBLIC_NAMES` に定義 module/attribute の対応を追加する。
- [ ] eager import や facade 専用 wrapper は追加せず、定義元と同一オブジェクトを返す。
- [ ] `src/grafix/api/__init__.py` と `src/grafix/api/__init__.pyi` は変更しない。

### 6.3 型情報と stub generator

- [ ] `src/grafix/devtools/generate_stub.py` の `_ROOT_STUB` に4定数の direct re-export と
  `__all__` entry を追加する。
- [ ] stub CLI で `typings/grafix/__init__.pyi` を再生成し、generator の内容と一致させる。
- [ ] project-local stub を使う mypy probe で、4定数が `tuple[int, int]` として
  `canvas_size=` に渡せることを確認する。
- [ ] 動的 DSL 用の `src/grafix/api/__init__.pyi` / `typings/grafix/api/__init__.pyi` には
  root-only 定数を混ぜない。

### 6.4 テスト

- [ ] `tests/api/test_canvas_size_constants.py` を追加し、4定数の exact value、tuple 型、
  `(width, height)` の向きを固定する。
- [ ] root の値と `grafix.core.canvas_sizes` の値が `is` で同一であることを確認する。
- [ ] `RenderOptions(canvas_size=A5)` と `RenderOptions(canvas_size=A5_LANDSCAPE)` が成功し、
  canonical な寸法を保持することを確認する。
- [ ] `tests/api/test_public_type_graph.py` の明示 root contract に4定数を追加する。
- [ ] isolated subprocess で定数参照後も `grafix.api` / interactive runtime / `pyglet` が
  未ロードであることを `tests/api/test_lazy_facade.py` に追加する。
- [ ] 既存の `from grafix import *` 検証で新しい全名が解決されることを確認する。
- [ ] `tests/devtools/test_generate_stub_project_local.py` の生成物/probe に定数 import を追加する。

### 6.5 利用者向け文書

- [ ] `README.md` に A4/A5 の縦横定数と `run(..., canvas_size=A5)` の短い例を追加する。
- [ ] renderer では論理寸法として使い、G-code では数値を mm と解釈するという単位境界を明記する。
- [ ] `docs/developer_guide.md` の root 公開 API と定義 module の案内を更新する。
- [ ] 既存 quick start の任意正方形 `(300, 300)` は作品意図が異なるため置換しない。

### 6.6 検証コマンド

- [ ] 対象 API/import/stub テストを実行する。

```bash
PYTHONPATH=src pytest -q \
  tests/api/test_canvas_size_constants.py \
  tests/api/test_public_type_graph.py \
  tests/api/test_lazy_facade.py \
  tests/devtools/test_generate_stub_project_local.py
```

- [ ] 変更対象に ruff を実行する。

```bash
ruff check \
  src/grafix/__init__.py \
  src/grafix/core/canvas_sizes.py \
  src/grafix/devtools/generate_stub.py \
  tests/api/test_canvas_size_constants.py \
  tests/api/test_public_type_graph.py \
  tests/api/test_lazy_facade.py \
  tests/devtools/test_generate_stub_project_local.py
```

- [ ] `mypy src/grafix` と project-local stub probe を実行する。
- [ ] 最後に `PYTHONPATH=src pytest -q` を実行し、全体回帰がないことを確認する。

## 7. 既存 sketch の扱い

- 既存の `CANVAS_WIDTH` / `CANVAS_HEIGHT` / `CANVAS_SIZE` は有効なユーザーコードであり、
  一括置換しない。
- active artwork、過去の生成物、agent loop run、README gallery の全 source は移行対象にしない。
- 今回は公開機構と文書例だけを追加し、新規 sketch から採用できる状態を受け入れ条件とする。
- curated sketch の移行が必要なら、公開 API 完了後に挙動不変の別タスクとして対象を明示する。

## 8. 非目的

- `canvas_size` の既定値、検証規則、座標原点、render/export の挙動変更
- `canvas_size` からの current width/height runtime context の提供
- paper name からの lookup、config/YAML schema、CLI `--canvas A5` の追加
- A系列全サイズ、B系列、北米用紙、screen/social-media pixel preset の網羅
- 自動 orientation 判定、scale/fit/crop、DPI、余白、plotter calibration の追加
- `grafix.api` 直下への二重 re-export、互換 alias/shim
- 既存 sketch の一括書換え

## 9. リスクと抑制策

| リスク | 抑制策 |
| --- | --- |
| `A5` の縦横が曖昧 | 短名は縦、横は `_LANDSCAPE` と文書・value test で固定する |
| mm と論理座標を混同する | 「公称 mm 値を logical `(width, height)` として渡す」と説明し、全 renderer の物理単位化とは分ける |
| root import が重くなる | PEP 562 mapping で pure core module だけを遅延 importし、isolated import test を置く |
| runtime と stub の公開面がずれる | `_ROOT_STUB` を生成元として更新し、生成物と mypy probe をテストする |
| top-level 名が増え続ける | 初回は実需のある4名だけに限定し、registry や全規格を先回りしない |
| landscape 数値が portrait とずれる | landscape tuple を portrait 定数から導出し、exact value test も置く |

## 10. 受け入れ条件（DoD）

- [ ] 次の import が runtime と型検査の両方で成功する。

```python
from grafix import A4, A4_LANDSCAPE, A5, A5_LANDSCAPE
```

- [ ] 4定数が表の exact tuple 値を持ち、`run` / `RenderOptions` の `canvas_size=` へ
  変換なしで渡せる。
- [ ] root `__all__`、PEP 562 mapping、project-local root stub が一致する。
- [ ] 定数参照だけでは GUI/runtime を import しない。
- [ ] 既存の default canvas、render/export、sketch の挙動を変更しない。
- [ ] 対象テスト、ruff、mypy、全 pytest が成功する。

## 11. 承認時の確認点

推奨は **A4/A5 × 縦横の4定数を root のみへ公開**する最小構成である。
A0〜A10 の全 A 系列を最初から公開したい場合、または「静的用紙 preset」ではなく
「実行中 canvas の width/height 取得」を意図していた場合は、実装前に本計画の範囲を変更する。
