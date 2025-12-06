import sys

sys.path.append("src")

from api import E, G
from core.realize import realize

c = G.circle(r=2.0, segments=8)
print(c)
s = E.scale(s=0.5)
scaled_c = s(c)
print(scaled_c)

realized_c = realize(scaled_c)
print(realized_c)
