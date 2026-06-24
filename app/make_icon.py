#!/usr/bin/env python3
"""Generate app/icon.ico — a rounded gradient tile with a sync check mark."""
from PIL import Image, ImageDraw
from pathlib import Path

S = 256
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

# vertical gradient #6c8cff -> #8a6cff
top, bot = (0x6c, 0x8c, 0xff), (0x8a, 0x6c, 0xff)
grad = Image.new("RGBA", (S, S))
gd = grad.load()
for y in range(S):
    t = y / (S - 1)
    r = int(top[0] + (bot[0] - top[0]) * t)
    g = int(top[1] + (bot[1] - top[1]) * t)
    b = int(top[2] + (bot[2] - top[2]) * t)
    for x in range(S):
        gd[x, y] = (r, g, b, 255)

# rounded-rect mask
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([6, 6, S - 6, S - 6], radius=52, fill=255)
img.paste(grad, (0, 0), mask)

# white check mark
d = ImageDraw.Draw(img)
d.line([(70, 134), (114, 178), (190, 86)], fill=(255, 255, 255, 255),
       width=24, joint="curve")

out = Path(__file__).resolve().parent / "icon.ico"
img.save(out, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("wrote", out)
