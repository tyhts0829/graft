`core/effects` の各 module は互いを import せず独立を保つこと。共有数値処理は責務別の
`core/geometry_kernels` だけに置き、effect module 側は validation、diagnostic、kernel composition を
担当すること。

冒頭の semantic header は `docs/agent_docs/documentation.md` の形式に従う。`Purpose` にはアルゴリズムの
手順ではなく、そのeffectが入力へ与える意味を記載する。package共通の依存規則を各headerへ反復せず、
`Constraints` にはopen/closed、座標frame、決定性、fallbackなど、そのeffect固有のinvariantだけを書く。
