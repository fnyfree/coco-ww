"""协奏环读数 (src/task/con_ring.py): 上游 ok-ww v3.8.15 的算法 + 我们加的防误判。

图都是协奏框 (get_con_box 那块) 原样截下来的, 期望值是看图判的:
- up_*: 上游 ok-ww 测试用的 1080p 实战截图; up_frame_* 是从上游整屏测试图里裁的协奏框 (52~126 像素, 各种分辨率)。
- 2k_*: 我们 2K 录屏裁的 (2026-10~11 实战)。
  - nohud: 没 HUD 时的整屏颜色 (大招过场、天空、技能特效), 上游算法读成满。
  - tint: 火环压在红背景上透色, 上游算法读成满, 其实只有一段弧。
  - full_*: 背景跟环同色、刻度只看到 3 个这些难认的真满环, 防误判不能把它们也去掉。

在 coco-ww 根目录跑:  python\\python.exe -X utf8 tests\\test_con_ring.py
"""
import sys
import unittest
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.task import con_ring  # noqa: E402

IMAGES = ROOT / 'tests' / 'images' / 'con_ring'
SPECTRO, ELECTRIC, FIRE, ICE, WIND, HAVOC = range(6)

# (图, 属性, 读数下限, 读数上限)
RINGS = [
    ('up_spectro_full', SPECTRO, 1, 1),
    ('up_spectro_96', SPECTRO, 0.9, 0.99),  # 3 点钟方向一小段缺口: 不满
    ('up_spectro_85', SPECTRO, 0.78, 0.9),  # 旧算法读成满
    ('up_wind_80', WIND, 0.72, 0.88),  # 旧算法读 0.99
    ('up_wind_25', WIND, 0.18, 0.32),
    ('up_wind_18', WIND, 0.1, 0.25),  # 短弧, 旧算法读 0; 刻度只看到 3 个
    ('up_wind_empty', WIND, 0, 0),
    ('up_frame_absorb_1080p', SPECTRO, 0.01, 0.99),
    ('up_frame_all_cd_900p', SPECTRO, 1, 1),
    ('up_frame_angle_130_4k', WIND, 0.01, 0.99),
    ('up_frame_con_full_2k', FIRE, 1, 1),
    ('up_frame_con_full2_2k', HAVOC, 1, 1),
    ('up_frame_in_combat_900p', WIND, 1, 1),
    ('2k_tint_fire_29', FIRE, 0.2, 0.4),
    ('2k_tint_fire_90', FIRE, 0.8, 0.97),
    ('2k_tint_fire_83_flash', FIRE, 0.7, 0.95),
    ('2k_fire_21', FIRE, 0.15, 0.3),
    ('2k_spectro_78', SPECTRO, 0.7, 0.86),
    ('2k_ice_69', ICE, 0.6, 0.78),
    ('2k_electric_74', ELECTRIC, 0.66, 0.82),
    ('2k_full_fire_red_bg', FIRE, 1, 1),
    ('2k_full_fire_red_bg2', FIRE, 1, 1),
    ('2k_full_electric_light_bg', ELECTRIC, 1, 1),
    ('2k_full_electric_3_ticks', ELECTRIC, 1, 1),
    ('2k_full_wind_blue_bg', WIND, 1, 1),
    ('2k_full_spectro', SPECTRO, 1, 1),
]

# (图, 上游算法把它读成满的属性)
NO_HUD = [
    ('2k_nohud_purple', ELECTRIC),
    ('2k_nohud_peach', FIRE),
    ('2k_nohud_blue_texture', ICE),
    ('2k_nohud_orange', FIRE),
    ('2k_nohud_sky', ICE),
]


def load(name):
    image = cv2.imread(str(IMAGES / f'{name}.png'))
    if image is None:
        raise FileNotFoundError(IMAGES / f'{name}.png')
    return image


class ConRingTest(unittest.TestCase):
    def test_readings(self):
        for name, element, low, high in RINGS:
            with self.subTest(name):
                con = con_ring.measure_con_arc(load(name), element)
                self.assertGreaterEqual(con, low)
                self.assertLessEqual(con, high)

    def test_other_elements_read_nothing(self):
        # 按错的属性读 (比如 ring_index 认错了) 不会把别的颜色当满
        for name, element, _, _ in RINGS:
            image = load(name)
            for other in range(len(con_ring.con_colors)):
                if other == element:
                    continue
                with self.subTest(name=name, element=other):
                    self.assertLess(con_ring.measure_con_arc(image, other), 1)

    def test_hud_seen_on_every_ring(self):
        for name, _, _, _ in RINGS:
            with self.subTest(name):
                self.assertTrue(con_ring.con_hud_visible(load(name)))

    def test_no_hud_reads_zero(self):
        for name, element in NO_HUD:
            with self.subTest(name):
                image = load(name)
                self.assertFalse(con_ring.con_hud_visible(image))
                for any_element in range(len(con_ring.con_colors)):
                    self.assertEqual(0, con_ring.measure_con_arc(image, any_element))

    def test_bad_input_reads_zero(self):
        image = load('up_spectro_full')
        self.assertEqual(0, con_ring.measure_con_arc(None, SPECTRO))
        self.assertEqual(0, con_ring.measure_con_arc(np.zeros((0, 0, 3), np.uint8), SPECTRO))
        self.assertEqual(0, con_ring.measure_con_arc(image, -1))  # 属性还没认出来
        self.assertFalse(con_ring.con_hud_visible(None))

    def test_single_bin_gap_is_bridged(self):
        # 白刻度线穿过满环留下的单格缺口算满; 缺两格就不满, 最多 70/72, 读不到 0.985~0.999
        covered = np.ones(con_ring.CON_ANGLE_BINS, bool)
        covered[0] = False
        self.assertEqual(1, con_ring._coverage(covered))
        covered[1] = False
        self.assertAlmostEqual(70 / 72, con_ring._coverage(covered))
        covered[:] = False
        self.assertEqual(0, con_ring._coverage(covered))


if __name__ == '__main__':
    unittest.main()
