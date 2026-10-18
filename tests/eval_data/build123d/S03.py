from build123d import *

# Truncated cone (frustum) on the z axis: bottom diameter 40 mm, top diameter 20 mm,
# height 30 mm. Bottom face sits on z = 0.
result = Cone(
    bottom_radius=20,
    top_radius=10,
    height=30,
    align=(Align.CENTER, Align.CENTER, Align.MIN),
)
