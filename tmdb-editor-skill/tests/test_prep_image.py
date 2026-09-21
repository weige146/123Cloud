"""prep_image.py 纯逻辑与端到端（本地生成图片，不联网）：规范计算 / 去黑边 / 转换管线。"""

import importlib.util
import pathlib
import sys

import pytest
from PIL import Image

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
spec = importlib.util.spec_from_file_location("prep_image", SCRIPTS / "prep_image.py")
pi = importlib.util.module_from_spec(spec)
sys.modules["prep_image"] = pi
spec.loader.exec_module(pi)


class TestSpecs:
    def test_known_types(self):
        assert pi.IMAGE_SPECS["poster"]["ratio"] == pytest.approx(2 / 3)
        assert pi.IMAGE_SPECS["still"]["ratio"] == pytest.approx(16 / 9)
        assert pi.IMAGE_SPECS["logo"]["mime"] == "image/png"
        assert pi.IMAGE_SPECS["profile"]["minWidth"] == 300

    def test_infer_type(self):
        assert pi.infer_image_type(1000, 1500, False) == "poster"
        assert pi.infer_image_type(1920, 1080, False) == "backdrop"
        assert pi.infer_image_type(600, 200, True) == "logo"
        assert pi.infer_image_type(100, 100, False) is None


class TestComputeTransform:
    def test_noop_when_already_ok(self):
        plan = pi.compute_transform(pi.IMAGE_SPECS["poster"], 1000, 1500)
        assert plan["crop"] == {"sx": 0, "sy": 0, "sw": 1000, "sh": 1500}
        assert plan["width"] == 1000 and plan["height"] == 1500
        assert plan["problems"] == []

    def test_crop_center_when_too_wide(self):
        plan = pi.compute_transform(pi.IMAGE_SPECS["poster"], 2000, 1500)
        assert plan["crop"]["sw"] == 1000 and plan["crop"]["sx"] == 500
        assert "居中裁剪" in plan["notes"][0]

    def test_crop_half_left_right(self):
        left = pi.compute_transform(pi.IMAGE_SPECS["poster"], 2000, 1500, {"cropHalf": "left"})
        right = pi.compute_transform(pi.IMAGE_SPECS["poster"], 2000, 1500, {"cropHalf": "right"})
        assert left["crop"]["sx"] == 0
        assert right["crop"]["sx"] == 1000

    def test_crop_when_too_tall(self):
        plan = pi.compute_transform(pi.IMAGE_SPECS["still"], 1280, 1440)
        assert plan["crop"]["sh"] == 720 and plan["crop"]["sy"] == 360

    def test_scale_down_over_max(self):
        plan = pi.compute_transform(pi.IMAGE_SPECS["poster"], 4000, 6000)
        assert plan["width"] == 2000 and plan["height"] == 3000
        assert "缩小" in plan["notes"][-1]

    def test_reject_upscale(self):
        plan = pi.compute_transform(pi.IMAGE_SPECS["poster"], 300, 450)
        assert plan["problems"] and "禁止放大" in plan["problems"][0]


def _rgb_image(width, height, painter):
    im = Image.new("RGB", (width, height))
    for y in range(height):
        for x in range(width):
            im.putpixel((x, y), painter(x, y))
    return im


class TestBlackBars:
    def test_detects_top_bottom(self):
        # 16:9 中间亮区上下各 60px 黑边
        im = _rgb_image(320, 180, lambda x, y: (10, 10, 10) if y < 60 or y >= 120 else (200, 200, 200))
        bars = pi.detect_black_bars(list(im.getdata()), 320, 180)
        assert bars["top"] == 60 and bars["bottom"] == 60 and bars["left"] == 0 and bars["right"] == 0

    def test_no_bars(self):
        im = _rgb_image(320, 180, lambda x, y: (150, 150, 150))
        assert pi.detect_black_bars(list(im.getdata()), 320, 180) == {"top": 0, "bottom": 0, "left": 0, "right": 0}

    def test_all_dark_capped_not_flattened(self):
        im = _rgb_image(320, 180, lambda x, y: (8, 8, 8))
        bars = pi.detect_black_bars(list(im.getdata()), 320, 180)
        assert bars["top"] <= int(180 * 0.45) and bars["bottom"] <= int(180 * 0.45)


class TestTransformPipeline:
    def _tmp(self, tmp_path, name):
        return str(tmp_path / name)

    def test_poster_crop_end_to_end(self, tmp_path):
        src = self._tmp(tmp_path, "src.png")
        _rgb_image(2000, 1500, lambda x, y: (120, 30, 90)).save(src)
        out = pi.transform_image(src, "poster", self._tmp(tmp_path, "out.jpg"))
        assert out["width"] == 1000 and out["height"] == 1500
        with Image.open(out["output"]) as im:
            assert im.size == (1000, 1500)

    def test_still_blackbar_removal_end_to_end(self, tmp_path):
        # 1920×1080 上下各 100px 黑边 → 去边 1920×880 → 裁 16:9 → 1564×880，仍过最低分辨率
        im = Image.new("RGB", (1920, 1080), (200, 200, 200))
        black = Image.new("RGB", (1920, 100), (8, 8, 8))
        im.paste(black, (0, 0))
        im.paste(black, (0, 980))
        src = self._tmp(tmp_path, "bars.png")
        im.save(src)
        out = pi.transform_image(src, "still", self._tmp(tmp_path, "out.jpg"))
        assert out["blackBars"]["top"] == 100 and out["blackBars"]["bottom"] == 100
        assert abs(out["width"] / out["height"] - 16 / 9) < 0.03
        assert out["width"] >= 1280 and out["height"] >= 720

    def test_too_small_raises(self, tmp_path):
        src = self._tmp(tmp_path, "small.png")
        Image.new("RGB", (400, 600)).save(src)
        with pytest.raises(ValueError, match="禁止放大"):
            pi.transform_image(src, "poster", self._tmp(tmp_path, "out.jpg"))

    def test_logo_png_output(self, tmp_path):
        src = self._tmp(tmp_path, "logo.png")
        _rgb_image(400, 100, lambda x, y: (0, 0, 0)).save(src)
        out = pi.transform_image(src, "logo", self._tmp(tmp_path, "out.png"))
        assert out["output"].endswith(".png")
        with Image.open(out["output"]) as im:
            assert im.format == "PNG"

    def test_invalid_type(self, tmp_path):
        with pytest.raises(ValueError, match="type"):
            pi.transform_image("x.png", "banner")


class TestContactSheet:
    def test_grid_and_output(self, tmp_path):
        import json

        paths = []
        for i, size in enumerate([(100, 100), (300, 150), (200, 400)]):
            p = str(tmp_path / f"c{i}.png")
            Image.new("RGB", size, (i * 60, 100, 100)).save(p)
            paths.append(p)
        out = str(tmp_path / "sheet.jpg")
        pi.build_contact_sheet(paths, out, cols=2, cell=120)
        with Image.open(out) as im:
            assert im.format == "JPEG"
            assert im.size == (240, 240)  # 2 列 × 2 行
