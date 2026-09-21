#!/usr/bin/env python3
"""TMDB 图片规范化 CLI（tmdb-helper.user.js 图片层的 Pillow 移植）。

按 TMDB 官方图片规范处理任意来源图片：比例不对裁剪（禁止拉伸）、超上限等比缩小、
低于下限直接报错（官方禁止放大小图）、16:9 类自动去黑边。依赖 Pillow（skill venv 自带）。

子命令：
  download 图片URL -o OUT [--referer URL]      按图床 Referer 表下载（防盗链），豆瓣/bkimg 自动升级原图
  transform INPUT TYPE [-o OUT] [--crop-half left|right] [--no-blackbars] [--quality 92]
                                               TYPE = poster|backdrop|still|logo|profile
  probe INPUT                                  只打印尺寸与判定类型，不产出文件

官方规范表与 tmdb-helper TMDBH_IMAGE_SPECS 同源；profile（人物头像）为官方 2:3、最低 300×450。
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, Optional

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    print("缺少依赖 Pillow：请用 skill 的 .venv/bin/python 运行，或 pip install pillow", file=sys.stderr)
    raise

# —— TMDB 图片类型官方规范（/bible/image）：比例、分辨率上下限、格式 ——
IMAGE_SPECS: Dict[str, Dict[str, Any]] = {
    "poster": {"label": "海报", "ratio": 2 / 3, "ratioTolerance": 0.02, "minWidth": 500, "minHeight": 750, "maxWidth": 2000, "maxHeight": 3000, "mime": "image/jpeg", "crop": True, "scale": True, "autoCrop": True},
    "backdrop": {"label": "背景图", "ratio": 16 / 9, "ratioTolerance": 0.02, "minWidth": 1280, "minHeight": 720, "maxWidth": 3840, "maxHeight": 2160, "mime": "image/jpeg", "crop": True, "scale": True, "autoCrop": True},
    "still": {"label": "剧照", "ratio": 16 / 9, "ratioTolerance": 0.02, "minWidth": 1280, "minHeight": 720, "maxWidth": 3840, "maxHeight": 2160, "mime": "image/jpeg", "crop": True, "scale": True, "autoCrop": True},
    "profile": {"label": "人物头像", "ratio": 2 / 3, "ratioTolerance": 0.02, "minWidth": 300, "minHeight": 450, "maxWidth": 2000, "maxHeight": 3000, "mime": "image/jpeg", "crop": True, "scale": True, "autoCrop": True},
    "logo": {"label": "标志", "ratio": 0, "ratioTolerance": 0, "minWidth": 200, "minHeight": 50, "maxWidth": 2000, "maxHeight": 2000, "mime": "image/png", "crop": False, "scale": True, "autoCrop": False},
}


def infer_image_type(width: int, height: int, is_png: bool) -> Optional[str]:
    """按比例推断图片类型：≈2:3 → 海报，≈16:9 → 背景图/剧照，透明宽 PNG → 标志。"""
    if not width or not height:
        return None
    if is_png and width / height >= 1.6:
        return "logo"
    ratio = width / height
    for key in ("poster", "backdrop"):
        spec = IMAGE_SPECS[key]
        if abs(ratio - spec["ratio"]) <= max(spec["ratioTolerance"], 0.05):
            return key
    return None


def detect_black_bars(pixels, width: int, height: int, options: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
    """逐行/列扫描近黑像素返回四边可裁宽度（TMDB-Import bordercrop 思路）。

    行/列平均亮度 ≤ meanMax 且最亮像素 ≤ peakMax 视为黑边；单边 < minBar 忽略、
    最多裁到边长 maxShare，防止全暗图被裁光。pixels 为 RGB 元组列表或展平序列。
    """
    opts = options or {}
    mean_max = float(opts.get("meanMax", 20))
    peak_max = float(opts.get("peakMax", 48))
    min_bar = max(2, int(opts.get("minBar") or max(4, round(min(width, height) * 0.02))))
    max_share = float(opts.get("maxShare", 0.45))

    def lum(x: int, y: int) -> float:
        r, g, b = pixels[y * width + x][:3]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    def row_dark(y: int) -> bool:
        total, peak = 0.0, 0.0
        for x in range(width):
            v = lum(x, y)
            total += v
            peak = max(peak, v)
        return total / width <= mean_max and peak <= peak_max

    def col_dark(x: int) -> bool:
        total, peak = 0.0, 0.0
        for y in range(height):
            v = lum(x, y)
            total += v
            peak = max(peak, v)
        return total / height <= mean_max and peak <= peak_max

    cap_y = max(0, int(height * max_share))
    cap_x = max(0, int(width * max_share))
    top = 0
    while top < cap_y and row_dark(top):
        top += 1
    bottom = 0
    while bottom < cap_y and row_dark(height - 1 - bottom):
        bottom += 1
    left = 0
    while left < cap_x and col_dark(left):
        left += 1
    right = 0
    while right < cap_x and col_dark(width - 1 - right):
        right += 1

    def clamp(value: int, dim: int) -> int:
        return value if value >= min(min_bar, int(dim * max_share)) else 0

    return {"top": clamp(top, height), "bottom": clamp(bottom, height), "left": clamp(left, width), "right": clamp(right, width)}


def compute_transform(spec: Dict[str, Any], width: int, height: int, options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """按官方规范计算处理参数：比例不对裁剪、超上限缩小、低于下限报错。

    options.cropHalf = "left"|"right"：横向拼图封面（正面+背面）居中裁 2:3 会带拼缝，
    取右半/左半才能得到单面竖版海报；纵向上仍居中。
    """
    opts = options or {}
    crop_half = opts.get("cropHalf") if opts.get("cropHalf") in ("left", "right") else ""
    problems: list = []
    notes: list = []
    sx, sy, sw, sh = 0, 0, width, height
    if spec["autoCrop"] and spec["ratio"] and abs(width / height - spec["ratio"]) > spec["ratioTolerance"]:
        if width / height > spec["ratio"]:
            sw = round(height * spec["ratio"])
            if crop_half == "right":
                sx = width - sw
            elif crop_half == "left":
                sx = 0
            else:
                sx = (width - sw) // 2
        else:
            sh = round(width / spec["ratio"])
            sy = (height - sh) // 2
        ratio_label = "2:3" if spec["ratio"] == 2 / 3 else "16:9"
        half_label = {"right": "取右半裁剪", "left": "取左半裁剪"}.get(crop_half, "居中裁剪")
        notes.append(f"已{half_label}为 {ratio_label} 比例")
    out_w, out_h = sw, sh
    if spec["scale"] and (out_w > spec["maxWidth"] or out_h > spec["maxHeight"]):
        scale = min(spec["maxWidth"] / out_w, spec["maxHeight"] / out_h)
        out_w = max(1, round(out_w * scale))
        out_h = max(1, round(out_h * scale))
        notes.append(f"已等比缩小到 {out_w}×{out_h}（满足最高分辨率）")
    if out_w < spec["minWidth"] or out_h < spec["minHeight"]:
        problems.append(f"分辨率 {out_w}×{out_h} 低于最低要求 {spec['minWidth']}×{spec['minHeight']}（TMDB 禁止放大低分辨率小图，请更换更清晰的图片）")
    return {"crop": {"sx": sx, "sy": sy, "sw": sw, "sh": sh}, "width": out_w, "height": out_h, "notes": notes, "problems": problems}


def transform_image(input_path: str, kind: str, output_path: Optional[str] = None, crop_half: str = "", no_blackbars: bool = False, quality: int = 92) -> Dict[str, Any]:
    if kind not in IMAGE_SPECS:
        raise ValueError(f"type 必须是 {'/'.join(IMAGE_SPECS)}")
    spec = IMAGE_SPECS[kind]
    output = output_path or default_output_name(input_path, kind)
    im = Image.open(input_path)
    is_png = (im.format == "PNG") or str(input_path).lower().endswith(".png")
    im = im.convert("RGBA") if kind == "logo" else im.convert("RGB")
    width, height = im.size

    bars = {"top": 0, "bottom": 0, "left": 0, "right": 0}
    if spec["autoCrop"] and not no_blackbars and kind in ("backdrop", "still"):
        flat = list(im.getdata())
        bars = detect_black_bars(flat, width, height)
        if any(bars.values()):
            im = im.crop((bars["left"], bars["top"], width - bars["right"], height - bars["bottom"]))
            width, height = im.size

    plan = compute_transform(spec, width, height, {"cropHalf": crop_half})
    if plan["problems"]:
        raise ValueError("；".join(plan["problems"]))
    box = plan["crop"]
    if (box["sx"], box["sy"], box["sw"], box["sh"]) != (0, 0, width, height):
        im = im.crop((box["sx"], box["sy"], box["sx"] + box["sw"], box["sy"] + box["sh"]))
    if (im.size[0], im.size[1]) != (plan["width"], plan["height"]):
        im = im.resize((plan["width"], plan["height"]), Image.LANCZOS)
    if spec["mime"] == "image/png":
        im.save(output, "PNG", optimize=True)
    else:
        im.save(output, "JPEG", quality=quality, optimize=True)
    return {
        "output": output,
        "type": kind,
        "width": im.size[0],
        "height": im.size[1],
        "blackBars": bars if any(bars.values()) else None,
        "notes": plan["notes"],
        "bytes": __import__("os").path.getsize(output),
    }


def default_output_name(input_path: str, kind: str) -> str:
    import os

    root, _ = os.path.splitext(input_path)
    return f"{root}_{kind}.{'png' if kind == 'logo' else 'jpg'}"


def build_contact_sheet(inputs: list, output_path: Optional[str] = None, cols: int = 4, cell: int = 320) -> Dict[str, Any]:
    """把候选缩略拼成一张 contact sheet 让用户挑图（避免选到水印/logo/无关图）。

    每格按比例缩放放进 cell×cell 方格（不拉伸），格内左上角标序号（对应输入顺序）。
    """
    import os

    from PIL import ImageDraw

    cols = max(1, int(cols))
    cell = max(80, int(cell))
    tiles = []
    for index, path in enumerate(inputs):
        im = Image.open(path).convert("RGB")
        im.thumbnail((cell, cell), Image.LANCZOS)
        tile = Image.new("RGB", (cell, cell), (24, 24, 28))
        tile.paste(im, ((cell - im.size[0]) // 2, (cell - im.size[1]) // 2))
        draw = ImageDraw.Draw(tile)
        draw.rectangle([4, 4, 34, 26], fill=(0, 0, 0))
        draw.text((10, 7), str(index + 1), fill=(255, 255, 255))
        tiles.append((os.path.basename(path), tile))
    rows = -(-len(tiles) // cols)
    sheet = Image.new("RGB", (cols * cell, rows * cell), (10, 10, 12))
    for index, (_, tile) in enumerate(tiles):
        x = (index % cols) * cell
        y = (index // cols) * cell
        sheet.paste(tile, (x, y))
    out = output_path or "contact-sheet.jpg"
    sheet.save(out, "JPEG", quality=88, optimize=True)
    return {"output": out, "candidates": len(tiles), "grid": f"{min(cols, len(tiles))}x{rows}", "cell": cell,
            "files": [name for name, _ in tiles]}


def main(argv=None) -> int:
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from fetch_source import download_image  # 下载复用 Referer 表与原图升级逻辑

    parser = argparse.ArgumentParser(description="TMDB 图片规范化 CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("download", help="按图床 Referer 表下载图片")
    p.add_argument("url")
    p.add_argument("-o", "--out", required=True)

    p = sub.add_parser("transform", help="按官方规范处理图片")
    p.add_argument("input")
    p.add_argument("type", choices=sorted(IMAGE_SPECS))
    p.add_argument("-o", "--out")
    p.add_argument("--crop-half", choices=("left", "right"), default="", help="横向拼图封面取半裁")
    p.add_argument("--no-blackbars", action="store_true", help="跳过去黑边")
    p.add_argument("--quality", type=int, default=92)

    p = sub.add_parser("probe", help="只打印尺寸与判定类型")
    p.add_argument("input")

    p = sub.add_parser("contact-sheet", help="候选图拼 contact sheet 供挑图")
    p.add_argument("inputs", nargs="+", help="候选图片（顺序即编号）")
    p.add_argument("-o", "--out", default="contact-sheet.jpg")
    p.add_argument("--cols", type=int, default=4)
    p.add_argument("--cell", type=int, default=320, help="单格边长像素")

    args = parser.parse_args(argv)
    try:
        if args.command == "download":
            print(download_image(args.url, args.out))
        elif args.command == "transform":
            print(json.dumps(transform_image(args.input, args.type, args.out, args.crop_half, args.no_blackbars, args.quality), ensure_ascii=False, indent=2))
        elif args.command == "probe":
            im = Image.open(args.input)
            kind = infer_image_type(im.size[0], im.size[1], im.format == "PNG")
            print(json.dumps({"input": args.input, "width": im.size[0], "height": im.size[1], "format": im.format, "inferredType": kind}, ensure_ascii=False))
        elif args.command == "contact-sheet":
            print(json.dumps(build_contact_sheet(args.inputs, args.out, args.cols, args.cell), ensure_ascii=False, indent=2))
        return 0
    except (ValueError, RuntimeError) as err:
        print(f"错误：{err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
