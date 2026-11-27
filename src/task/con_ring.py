"""协奏环读数: 从协奏框截图读当前角色的协奏 (0~1)。

算法搬自上游 ok-ww v3.8.15 (52c2807) 的 measure_con_arc:
环带按角度分 72 格, 每格有 3 个以上属性色像素就算这格有协奏, 白色刻度线穿过环留下的单格缺口补上。
不用按分辨率标定 (以前的 _con_full_size), 短弧也读得出来; 整圈都覆盖才给 1, 否则最多 70/72。

我们另加两层防误判 (2026-11-27, 6 段 2K 录屏 7322 帧验证: 判满 478 -> 445 帧, 去掉的 33 帧都是假满, 真满一帧没漏):
1. 认 HUD: 环外上下左右 4 个刻度白点至少看到 3 个才读, 否则读 0。
   没 HUD 时 (大招过场、整屏特效、天空) 环那一圈也是属性色, 会被读成满。
2. 透色: 某个角度上环里侧空隙和环外侧都是本属性色, 说明背景跟环同色, 半透明的环会透色
   (比如火环压在红色特效上)。这种角度只认环芯是不是实的。
代价: HUD 淡入淡出、刻度被特效挡掉 2 个的那几帧读 0, 只会晚判满, 不会误判满。用例见 tests/test_con_ring.py。
"""
import cv2
import numpy as np

con_colors = [  # 不同角色属性的协奏值能量环的颜色范围列表, 下标同 Elements / ring_index。
    {
        'r': (205, 235),
        'g': (190, 222),  # for yellow spectro
        'b': (90, 130)
    },
    {
        'r': (150, 190),  # Red range
        'g': (95, 140),  # Green range for purple electric
        'b': (210, 249)  # Blue range
    },
    {
        'r': (200, 230),  # Red range
        'g': (100, 130),  # Green range    for red fire
        'b': (75, 105)  # Blue range
    },
    {
        'r': (60, 95),  # Red range
        'g': (150, 180),  # Green range    for blue ice
        'b': (210, 245)  # Blue range
    },
    {
        'r': (70, 110),  # Red range
        'g': (215, 250),  # Green range    for green wind
        'b': (155, 190)  # Blue range
    },
    {
        'r': (190, 220),  # Red range
        'g': (65, 105),  # Green range    for havoc
        'b': (145, 175)  # Blue range
    }
]


def _con_color_hue(color_range):
    center = np.uint8([[[sum(color_range['b']) // 2, sum(color_range['g']) // 2, sum(color_range['r']) // 2]]])
    return int(cv2.cvtColor(center, cv2.COLOR_BGR2HSV)[0, 0, 0])


con_hues = [_con_color_hue(color_range) for color_range in con_colors]  # OpenCV 色相 (0~179)
CON_RING_INNER_RATIO = 0.35119  # 环带半径 / 协奏框高
CON_RING_OUTER_RATIO = 0.42261
CON_ANGLE_BINS = 72  # 一格 5 度
CON_HUE_TOLERANCE = 8
CON_MIN_SATURATION = 100  # 有协奏的弧 130~160, 空槽的灰色 40~65
CON_MIN_VALUE = 170  # 有协奏的弧 215~235, 空槽 120~170
CON_MIN_PIXELS_PER_BIN = 3
# 认 HUD: 刻度白点
CON_TICK_RADIUS = (0.42, 0.50)  # 白点离环心 (框高的倍数), 2K 实测 0.435~0.47
CON_TICK_HALF_WIDTH = 0.035  # 白点偏离正上/下/左/右的容差 (框不完全对正环心也认得到)
CON_TICK_MIN_VALUE = 200
CON_TICK_MAX_SATURATION = 90  # 彩色背景上白点会被染色, 录屏里最高到 89
CON_TICK_PURE_SATURATION = 60  # 这么白就不用跟背景比
CON_TICK_SATURATION_DROP = 25  # 否则饱和度要比窗口里背景的中位数低这么多
CON_TICK_MIN_FOUND = 3  # 4 个里至少看到几个: 被特效挡掉 1 个照样认
# 透色: 背景跟环同色的角度只认环芯
CON_GUARD_IN = (0.30, 0.335)  # 环和中间图标之间那圈, 平时是半透明暗底
CON_GUARD_OUT = (0.44, 0.475)  # 环外那圈
CON_GUARD_FLOOD = 0.5  # 里外两圈都有这么多属性色 -> 这个角度背景同色
CON_CORE = (0.36, 0.41)  # 环芯: 透色主要在环带边上, 真环的环芯是实的
CON_CORE_MIN = 0.65


class _RingGeometry:
    """一种框尺寸下环带、里外两圈、环芯、4 个刻度窗口的掩码和每个像素的角度格。"""

    def __init__(self, h, w):
        center_y, center_x = (h - 1) / 2, (w - 1) / 2
        yy, xx = np.mgrid[0:h, 0:w]
        distance = np.hypot(yy - center_y, xx - center_x)
        angles = (np.degrees(np.arctan2(xx - center_x, center_y - yy)) + 360) % 360
        self.bins = (angles / (360 / CON_ANGLE_BINS)).astype(int) % CON_ANGLE_BINS
        self.band = (distance >= h * CON_RING_INNER_RATIO) & (distance <= h * CON_RING_OUTER_RATIO + 1)
        self.guard_in = self._annulus(distance, h, CON_GUARD_IN)
        self.guard_out = self._annulus(distance, h, CON_GUARD_OUT)
        self.core = self._annulus(distance, h, CON_CORE)
        self.guard_in_total = self._per_bin(self.guard_in, 1)
        self.guard_out_total = self._per_bin(self.guard_out, 1)
        self.core_total = self._per_bin(self.core, 0)
        radius = distance / h
        dy, dx = (yy - center_y) / h, (xx - center_x) / h
        on_tick_circle = (radius >= CON_TICK_RADIUS[0]) & (radius <= CON_TICK_RADIUS[1])
        near_x, near_y = np.abs(dx) <= CON_TICK_HALF_WIDTH, np.abs(dy) <= CON_TICK_HALF_WIDTH
        self.ticks = [on_tick_circle & (dy < 0) & near_x, on_tick_circle & (dy > 0) & near_x,
                      on_tick_circle & (dx < 0) & near_y, on_tick_circle & (dx > 0) & near_y]
        self.tick_pixels = max(1, round(2 * (h / 84) ** 2))  # 2K 框高 84 时要 2 个白像素, 按面积缩放

    @staticmethod
    def _annulus(distance, h, ratios):
        return (distance >= h * ratios[0]) & (distance < h * ratios[1])

    def _per_bin(self, mask, span):
        """每个角度格里 mask 的像素数; span > 0 时连左右各 span 格一起算 (首尾相接)。"""
        counts = np.bincount(self.bins[mask], minlength=CON_ANGLE_BINS).astype(float)
        if span:
            counts = np.convolve(np.r_[counts[-span:], counts, counts[:span]], np.ones(2 * span + 1), 'valid')
        return counts

    def share(self, colored, region, total, span):
        """每个角度格里 region 中属性色像素占多少。"""
        return self._per_bin(region & colored, span) / np.maximum(total, 1)


_geometries = {}


def _geometry(h, w):
    geometry = _geometries.get((h, w))
    if geometry is None:
        geometry = _geometries[(h, w)] = _RingGeometry(h, w)
    return geometry


def _count_ticks(hsv, geometry):
    """4 个刻度窗口里看到白点的有几个。白点: 很亮、不怎么饱和, 而且明显比窗口里的背景白。"""
    saturation, value = hsv[..., 1].astype(np.int16), hsv[..., 2]
    found = 0
    for window in geometry.ticks:
        s, v = saturation[window], value[window]
        if s.size == 0:
            continue
        background = int(np.median(s))
        white = (v >= CON_TICK_MIN_VALUE) & (s <= CON_TICK_MAX_SATURATION) & (
                (s <= CON_TICK_PURE_SATURATION) | (s <= background - CON_TICK_SATURATION_DROP))
        found += int(np.count_nonzero(white) >= geometry.tick_pixels)
    return found


def con_hud_visible(image):
    """协奏框里看得到 HUD 没有: 环外上下左右 4 个刻度白点至少看到 CON_TICK_MIN_FOUND 个。"""
    if image is None or image.size == 0:
        return False
    geometry = _geometry(*image.shape[:2])
    return _count_ticks(cv2.cvtColor(image, cv2.COLOR_BGR2HSV), geometry) >= CON_TICK_MIN_FOUND


def measure_con_arc(image, ring_index):
    """协奏环被属性色覆盖了几成 (0~1), 整圈都覆盖才给 1。看不到 HUD 读 0。

    Args:
        image (numpy.ndarray): 协奏框的 BGR 截图 (BaseCombatTask.get_con_box)。
        ring_index (int): 属性下标, 同 con_colors / Elements。
    """
    if image is None or image.size == 0 or not 0 <= ring_index < len(con_hues):
        return 0.0
    geometry = _geometry(*image.shape[:2])
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    if _count_ticks(hsv, geometry) < CON_TICK_MIN_FOUND:
        return 0.0
    hue_diff = np.abs(hsv[..., 0].astype(np.int16) - con_hues[ring_index])
    hue_diff = np.minimum(hue_diff, 180 - hue_diff)
    colored = ((hue_diff <= CON_HUE_TOLERANCE) & (hsv[..., 1] >= CON_MIN_SATURATION)
               & (hsv[..., 2] >= CON_MIN_VALUE))
    counts = np.bincount(geometry.bins[colored & geometry.band], minlength=CON_ANGLE_BINS)
    covered = counts >= CON_MIN_PIXELS_PER_BIN
    flooded = ((geometry.share(colored, geometry.guard_in, geometry.guard_in_total, 1) >= CON_GUARD_FLOOD)
               & (geometry.share(colored, geometry.guard_out, geometry.guard_out_total, 1) >= CON_GUARD_FLOOD))
    if flooded.any():
        core = geometry.share(colored, geometry.core, geometry.core_total, 0)
        covered = np.where(flooded, core >= CON_CORE_MIN, covered)
    return _coverage(covered)


def _coverage(covered):
    if covered.all():
        return 1.0
    if not covered.any():
        return 0.0
    # 白色刻度线穿过环, 满环上会留单格缺口: 两边都有协奏的单格补上
    bridged = covered | (np.roll(covered, 1) & np.roll(covered, -1))
    if bridged.all():
        return 1.0
    return min(float(bridged.sum()) / CON_ANGLE_BINS, 0.99)
