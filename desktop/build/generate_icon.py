"""Generate the 123Cloud app icon from build/icon-source.jpg.

Composes the circular "123 云盘" logo onto a macOS-style rounded-square
tile with a soft blue-white gradient, then emits icon.png / icon.icns /
icon.ico.  Pillow is a build-time tool only; icns additionally needs
sips/iconutil (macOS only).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.join(HERE, "icon-source.jpg")
SIZE = 1024
TILE_RADIUS = int(SIZE * 0.225)


def find_logo_circle(image: Image.Image) -> tuple[int, int, int]:
    """Locate the circular logo on the white background.

    Returns (center_x, center_y, radius) in source pixels.
    """
    gray = image.convert("L")
    # Anything clearly darker than near-white belongs to the logo.
    mask = gray.point(lambda v: 255 if v <= 242 else 0)
    bbox = mask.getbbox()
    if not bbox:
        raise RuntimeError("could not locate logo on white background")
    left, top, right, bottom = bbox
    width, height = right - left, bottom - top
    diameter = max(width, height)
    return (left + right) // 2, (top + bottom) // 2, diameter // 2


def rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask


def make_tile(size: int) -> Image.Image:
    """Rounded-square tile with a soft blue-white vertical gradient."""
    top = (238, 246, 255)
    bottom = (205, 224, 252)
    gradient = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / max(1, size - 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        gradient.putpixel((0, y), color)
    tile = gradient.resize((size, size)).convert("RGBA")

    # Gentle radial sheen in the upper area for a glassy look.
    sheen = Image.new("L", (size, size), 0)
    sheen_draw = ImageDraw.Draw(sheen)
    sheen_draw.ellipse((size * 0.1, -size * 0.35, size * 0.95, size * 0.55), fill=90)
    sheen = sheen.filter(ImageFilter.GaussianBlur(size * 0.10))
    white_layer = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    tile = Image.composite(white_layer, tile, sheen.point(lambda v: v // 2))

    tile.putalpha(rounded_mask(size, TILE_RADIUS))

    # Hairline border
    draw = ImageDraw.Draw(tile)
    draw.rounded_rectangle(
        (1, 1, size - 2, size - 2),
        radius=TILE_RADIUS - 1,
        outline=(255, 255, 255, 200),
        width=2,
    )
    return tile


def compose() -> Image.Image:
    source = Image.open(SOURCE).convert("RGB")
    cx, cy, radius = find_logo_circle(source)
    # Tighter circular crop of the logo itself (1px inward to avoid white halo)
    logo_diameter = int(radius * 2) - 2
    box = (cx - logo_diameter // 2, cy - logo_diameter // 2, cx + logo_diameter // 2, cy + logo_diameter // 2)
    logo = source.crop(box)

    tile = make_tile(SIZE)

    # Circular alpha mask for the logo (slight feather on the edge)
    logo_size = logo.size[0]
    circle_mask = Image.new("L", (logo_size, logo_size), 0)
    mask_draw = ImageDraw.Draw(circle_mask)
    mask_draw.ellipse((0, 0, logo_size - 1, logo_size - 1), fill=255)
    circle_mask = circle_mask.filter(ImageFilter.GaussianBlur(1))
    logo.putalpha(circle_mask)

    # Scale the logo to ~94% of the tile and center it.
    target = int(SIZE * 0.94)
    logo = logo.resize((target, target), Image.LANCZOS)
    offset = (SIZE - target) // 2
    tile.alpha_composite(logo, (offset, offset))

    # Re-apply the tile mask so nothing spills outside the rounded square.
    return Image.composite(tile, Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0)), rounded_mask(SIZE, TILE_RADIUS))


def make_icns(png_path: str) -> str | None:
    if sys.platform != "darwin":
        return None
    with tempfile.TemporaryDirectory() as tmp:
        iconset = os.path.join(tmp, "icon.iconset")
        os.makedirs(iconset)
        for size in (16, 32, 64, 128, 256, 512, 1024):
            for scale, suffix in ((1, ""), (2, "@2x")):
                target = size * scale
                out = os.path.join(iconset, f"icon{size}x{size}{suffix}.png")
                subprocess.run(
                    ["sips", "-z", str(target), str(target), png_path, "--out", out],
                    check=True,
                    capture_output=True,
                )
        icns = os.path.join(HERE, "icon.icns")
        subprocess.run(["iconutil", "-c", "icns", iconset, "-o", icns], check=True, capture_output=True)
        return icns


def make_ico(png_path: str) -> str:
    image = Image.open(png_path)
    ico = os.path.join(HERE, "icon.ico")
    image.save(ico, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return ico


def main() -> None:
    if not os.path.exists(SOURCE):
        raise SystemExit(f"missing {SOURCE} — put the logo image there first")
    icon = compose()
    png = os.path.join(HERE, "icon.png")
    icon.save(png)
    outputs = [png]
    icns = make_icns(png)
    if icns:
        outputs.append(icns)
    outputs.append(make_ico(png))
    for path in outputs:
        print("wrote", path)


if __name__ == "__main__":
    main()
