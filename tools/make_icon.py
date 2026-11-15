"""Draw the PhoneGo launcher icon.

The idea: two screens. The one behind is the phone's own; the one in front is
the one PhoneGo works on, and the arrow says it is doing something there -- the
whole product in one mark. Everything is drawn at 4x and downsampled, so the
small densities stay clean without a vector toolchain.

Run: python3 tools/make_icon.py
"""
from PIL import Image, ImageDraw

SIZE = 1024            # master canvas
ACCENT = (47, 107, 255)      # #2F6BFF, the app's accent
DEEP = (21, 63, 176)         # its darker end, for the gradient
BACK = (255, 255, 255, 90)   # the rear screen, translucent


def rounded(size: int) -> Image.Image:
    """A rounded-square tile with a diagonal gradient."""
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    grad = Image.new("RGBA", (size, size))
    px = grad.load()
    for y in range(size):
        for x in range(size):
            t = (x / size + y / size) / 2
            px[x, y] = (
                int(ACCENT[0] + (DEEP[0] - ACCENT[0]) * t),
                int(ACCENT[1] + (DEEP[1] - ACCENT[1]) * t),
                int(ACCENT[2] + (DEEP[2] - ACCENT[2]) * t),
                255,
            )
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=int(size * 0.235), fill=255)
    tile.paste(grad, (0, 0), mask)
    return tile


def draw(size: int = SIZE) -> Image.Image:
    img = rounded(size)
    d = ImageDraw.Draw(img)
    s = size / 1024

    def phone(cx, cy, w, h, radius, fill=None, outline=None, width=0):
        d.rounded_rectangle(
            [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2],
            radius=radius, fill=fill, outline=outline, width=width)

    def arrow(cx, cy, length, thickness):
        """A right-pointing arrow as one polygon; lines left a notch at the joint."""
        head = thickness * 1.9
        half = thickness / 2
        tip = cx + length / 2
        tail = cx - length / 2
        d.polygon([
            (tail, cy - half), (tip - head, cy - half), (tip - head, cy - thickness * 1.5),
            (tip, cy),
            (tip - head, cy + thickness * 1.5), (tip - head, cy + half), (tail, cy + half),
        ], fill=ACCENT)

    # the phone the user is holding, ghosted, sitting up and to the left
    phone(360 * s, 432 * s, 286 * s, 512 * s, 62 * s,
          outline=(255, 255, 255, 190), width=int(27 * s))
    # the screen PhoneGo drives, solid white, in front
    phone(650 * s, 566 * s, 300 * s, 540 * s, 66 * s, fill=(255, 255, 255, 255))
    # two lines of "content" and the arrow that means it is working
    d.rounded_rectangle([596 * s, 430 * s, 704 * s, 456 * s], radius=13 * s, fill=(47, 107, 255, 70))
    d.rounded_rectangle([560 * s, 492 * s, 740 * s, 518 * s], radius=13 * s, fill=(47, 107, 255, 45))
    arrow(650 * s, 620 * s, 205 * s, 50 * s)
    return img


def main() -> None:
    import os

    master = draw()
    res = "android-app/app/src/main/res"
    for folder, px in (
        ("mipmap-mdpi", 48), ("mipmap-hdpi", 72), ("mipmap-xhdpi", 96),
        ("mipmap-xxhdpi", 144), ("mipmap-xxxhdpi", 192),
    ):
        os.makedirs(f"{res}/{folder}", exist_ok=True)
        master.resize((px, px), Image.LANCZOS).save(f"{res}/{folder}/ic_launcher.png")
    os.makedirs("docs/ui", exist_ok=True)
    master.resize((512, 512), Image.LANCZOS).save("docs/ui/31-icon-phonego.png")
    print("wrote", res, "and docs/ui/31-icon-phonego.png")


if __name__ == "__main__":
    main()
