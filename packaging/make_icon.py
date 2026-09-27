"""Generate the 256px Windows icon from simple vector shapes."""

from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SCALE = 4
size = 256 * SCALE
image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
draw = ImageDraw.Draw(image)
def box(coords):
    return tuple(round(value * SCALE) for value in coords)

draw.rounded_rectangle(box((8, 8, 248, 248)), radius=48 * SCALE,
                       fill="#111a20", outline="#60737d", width=3 * SCALE)
draw.ellipse(box((46, 46, 210, 210)), outline="#40545e", width=3 * SCALE)
draw.ellipse(box((75, 75, 181, 181)), outline="#60737d", width=2 * SCALE)
draw.line(box((128, 28, 128, 93)), fill="#d8a35d", width=8 * SCALE)
draw.line(box((128, 163, 128, 228)), fill="#d8a35d", width=8 * SCALE)
draw.line(box((28, 128, 93, 128)), fill="#d8a35d", width=8 * SCALE)
draw.line(box((163, 128, 228, 128)), fill="#d8a35d", width=8 * SCALE)
draw.ellipse(box((108, 108, 148, 148)), fill="#d8a35d")
draw.ellipse(box((118, 118, 138, 138)), fill="#111a20")
image = image.resize((256, 256), Image.Resampling.LANCZOS)
image.save(ROOT / "assets" / "wardogs.png")
image.save(ROOT / "assets" / "wardogs.ico", format="ICO",
           sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
