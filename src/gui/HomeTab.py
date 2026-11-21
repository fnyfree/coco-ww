"""主页: 背景是玩家自己选的图, 上面两张毛玻璃卡片放最常用的两件事 —— 自动战斗和刷4C。

- 右上角的「启动 F9」是整个识别的总开关 (跟其他页运行栏右边那个按钮是同一个);
  自动战斗卡片底下的开关只管自动战斗这一个任务, 不碰总开关
- 细的设置还在「任务」页, 卡片右上角的齿轮直接跳过去
- 运行时顶部有一条状态, 点「详细」或右上角「详细面板」去详细面板 (DashboardTab)
- 背景图从卡片上沿开始往下渐变成深色, 卡片和队伍像放在图的「地面」上

Qt 的控件做不了 CSS 那种实时 backdrop-filter。这里在尺寸变化时把「背景图 + 底部渐变」合成一张,
再缩小放大一遍当模糊底; 每张玻璃卡片画自己那块模糊底, 再盖一层半透明深色, 看起来一样。
"""
import json
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QFileDialog, QFrame, QGraphicsDropShadowEffect, QGridLayout, QHBoxLayout, QLabel,
                               QMenu, QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from ok import Logger, og
from ok.ui.qt.kit.icons import icon as kit_icon
from ok.ui.qt.kit.theme import FONT_FAMILY
from ok.ui.qt.shell.run_bar import format_elapsed, game_display_name
from ok.util.config import Config

logger = Logger.get_logger(__name__)

ACCENT = '#F2C14E'
ON_ACCENT = '#1A1408'
WHITE = '#FFFFFF'
TEXT_SECONDARY = 'rgba(255, 255, 255, 0.62)'
FALLBACK_BG = QColor('#353B46')
BASE = (12, 14, 19)  # 底部渐变最后落到的深色
GLASS_ALPHA = 0.55
TONE_COLORS = {
    'idle': QColor(255, 255, 255, 102),
    'success': QColor('#4ADE80'),
    'accent': QColor(ACCENT),
    'warning': QColor('#F5A524'),
    'danger': QColor('#F87171'),
}

CONFIG_NAME = 'HomeTab'
BG_KEY = 'Background Image'
BG_FILE_STEM = 'home_background'  # 选的图复制成 configs/home_background.<后缀>, 原图挪走了也不丢
BG_MAX_SIDE = 3840
CHAR_HEADS = Path('assets') / 'char_heads'
RUNS_DIR = Path('logs') / 'axis_runs'

CARD_WIDTH = 290
STACK_BELOW = 900  # 内容区比这窄时队伍放到卡片上面
FADE_LEAD = 64  # 渐变从卡片上沿再往上这么多开始
# (位置, 不透明度): 位置 0 是渐变起点, 1 是页面底; 先慢后快, 到底接近实色
FADE_STOPS = ((0.0, 0.0), (0.26, 0.3), (0.52, 0.66), (0.78, 0.9), (1.0, 0.97))
BLUR_DOWNSCALE = 18
REFRESH_MS = 1000
LAST_RUN_CACHE_SECONDS = 15

INTERACTION_NAMES = {'SendInput': '前台输入', 'Pynput': '前台输入 (Pynput)', 'PostMessage': '后台输入'}
NO_TELEPORT = 'No'


# --- 纯逻辑 (有单元测试) -------------------------------------------------------------

def cover_source_rect(src_w, src_h, dst_w, dst_h):
    """原图按「铺满、居中裁切」画进 dst_w×dst_h 时, 取原图的哪一块: (x, y, w, h)。"""
    if min(src_w, src_h, dst_w, dst_h) <= 0:
        return 0, 0, 0, 0
    scale = max(dst_w / src_w, dst_h / src_h)
    w = min(src_w, round(dst_w / scale))
    h = min(src_h, round(dst_h / scale))
    return (src_w - w) // 2, (src_h - h) // 2, w, h


def fade_top(height, bottom_top, lead=FADE_LEAD):
    """渐变起点的 y: 下面那排卡片的上沿再往上 lead, 但至少留出页面下面四分之一来渐变。"""
    if height <= 0:
        return 0
    return max(0, min(bottom_top - lead, int(height * 0.75)))


def class_name(cls_or_name):
    return getattr(cls_or_name, '__name__', None) or (str(cls_or_name) if cls_or_name else None)


def team_from_tasks(tasks, char_info):
    """执行器任务里最近认出来的队伍 (按 1/2/3 号位), 认不全返回 None。"""
    for task in tasks:
        chars = getattr(task, 'chars', None)
        if not chars or len(chars) != 3 or any(char is None for char in chars):
            continue
        names = [class_name((char_info.get(getattr(char, 'char_name', None)) or {}).get('cls')) for char in chars]
        if None not in names and len(set(names)) == 3:
            return names
    return None


def team_from_last_run(folder=RUNS_DIR, newest=5):
    """最近一局跑轴记录开头写的队伍 (按 1/2/3 号位), 用在这次启动后还没进过战斗的时候。"""
    try:
        files = sorted(Path(folder).glob('*.jsonl'), key=lambda p: p.name)
    except OSError:
        return None
    for path in reversed(files[-newest:]):
        try:
            with open(path, encoding='utf-8') as f:
                first = json.loads(f.readline())
        except (OSError, ValueError):
            continue
        team = first.get('team') if isinstance(first, dict) and first.get('e') == 'start' else None
        if not isinstance(team, list) or len(team) != 3:
            continue
        members = sorted((m for m in team if isinstance(m, dict)), key=lambda m: m.get('i', 0))
        names = [m.get('cls') or m.get('n') for m in members]
        if len(names) == 3 and all(names) and len(set(names)) == 3:
            return names
    return None


def _seconds(value):
    try:
        return max(0.0, float(value or 0))
    except (TypeError, ValueError):
        return 0.0


def combat_chips(config, f_start_key='按 F 开始战斗', f_delay_key='F 之后等待(秒)'):
    """自动战斗卡片上的几个小标签, 最多三个。用大招是默认, 只在关掉时提一句。"""
    chips = []
    if config.get(f_start_key):
        wait = _seconds(config.get(f_delay_key))
        chips.append(f'按 F 开打 · {wait:g} 秒' if wait else '按 F 开打')
    if config.get('Auto Target', True):
        chips.append('自动选敌')
    if not config.get('Use Liberation', True):
        chips.append('大世界不放大招')
    return chips


def run_view(run_button, hotkey):
    """右上角总开关: (文字, 图标, 按钮样式, 能不能点)。

    run_button 是运行栏按钮现在的状态 (start / pause / cancel / disabled), 点它就是按 F9。
    """
    suffix = f'  {hotkey}' if hotkey else ''
    if run_button == 'pause':
        return f'暂停{suffix}', 'pause', 'dark', True
    if run_button == 'cancel':
        return '取消启动', 'x', 'dark', True
    if run_button == 'disabled':
        return f'启动{suffix}', 'play', 'dark', False
    return f'启动{suffix}', 'play', 'accent', True


def combat_view(run_button, enabled, in_combat, farm_active):
    """自动战斗卡片标题下的状态: (文字, 色调)。

    enabled 是自动战斗自己的开关; run_button 是总开关 (运行栏按钮) 的状态, 见 run_view。
    """
    if run_button == 'disabled':
        return '被其他程序接管中', 'warning'
    if run_button == 'cancel':
        return '正在启动游戏', 'accent'
    running = run_button == 'pause'
    if running and farm_active:
        return '交给刷4C', 'accent'
    if not enabled:
        return '已关闭', 'idle'
    if not running:
        return '等启动识别', 'warning'
    if in_combat:
        return '战斗中', 'accent'
    return '待命 · 进战斗自动开打', 'success'


def combat_hint(run_button, enabled, f_start, hotkey, farm_active=False):
    if farm_active and run_button == 'pause':
        return '刷4C 进本后自己打，这里不用管'
    if not enabled:
        return '打开下面的开关，进战斗就自动打'
    if run_button == 'start':
        return f'识别还没启动，点右上角「启动」或按 {hotkey}' if hotkey else '识别还没启动，点右上角「启动」'
    if f_start:
        return '进战斗自动打，副本里按 F 直接开'
    return '进战斗自动打'


def farm_target(config, teleport_keys, tr=lambda s: s):
    """刷4C 卡片上的目标: (标题, 副标题)。"""
    mode = config.get('Teleport to Boss', NO_TELEPORT)
    if mode in teleport_keys:
        name = config.get(teleport_keys[mode][0]) or '还没选 Boss'
        level = config.get('Boss Level')
        return name, f'{tr(mode)} · {level} 级' if level else tr(mode)
    return '不传送', '进本或走到 Boss 跟前再点开始'


def format_number(value):
    if value is None or value == '':
        return '—'
    if isinstance(value, float):
        return f'{value:.1f}' if value != int(value) else str(int(value))
    return str(value)


# --- 小部件 ---------------------------------------------------------------------------

def _label(text='', size=13, weight=400, color=WHITE, parent=None):
    label = QLabel(text, parent)
    label.setStyleSheet(f'color: {color}; background: transparent; font-family: {FONT_FAMILY}; '
                        f'font-size: {size}px; font-weight: {weight};')
    return label


def _shadow(widget, blur=22, alpha=150):
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, 1)
    effect.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(effect)


BUTTON_STYLES = {
    'accent': (f'background: {ACCENT}; color: {ON_ACCENT}; border: 0;', 'background: #F6CF6E;', 'background: #DDAE3F;'),
    'glass': ('background: rgba(255, 255, 255, 0.10); color: #FFFFFF; border: 1px solid rgba(255, 255, 255, 0.24);',
              'background: rgba(255, 255, 255, 0.17);', 'background: rgba(255, 255, 255, 0.07);'),
    'dark': ('background: rgba(12, 14, 20, 0.6); color: #FFFFFF; border: 1px solid rgba(255, 255, 255, 0.12);',
             'background: rgba(30, 33, 42, 0.75);', 'background: rgba(12, 14, 20, 0.8);'),
    'dashed': ('background: transparent; color: rgba(255, 255, 255, 0.85); border: 1px dashed rgba(255, 255, 255, 0.38);',
               'background: rgba(255, 255, 255, 0.10);', 'background: rgba(255, 255, 255, 0.05);'),
    'icon': ('background: transparent; color: rgba(255, 255, 255, 0.75); border: 0;',
             'background: rgba(255, 255, 255, 0.12);', 'background: rgba(255, 255, 255, 0.06);'),
}


def style_button(button, kind, radius=12, size=15, weight=700):
    base, hover, pressed = BUTTON_STYLES[kind]
    button.setStyleSheet(
        f'QPushButton {{ {base} border-radius: {radius}px; font-family: {FONT_FAMILY}; font-size: {size}px; '
        f'font-weight: {weight}; padding: 0 14px; }}'
        f'QPushButton:hover {{ {hover} }} QPushButton:pressed {{ {pressed} }}'
        'QPushButton:disabled { background: rgba(255, 255, 255, 0.08); color: rgba(255, 255, 255, 0.35); }')


def _button(text, kind, height, icon_name=None, icon_color=WHITE, radius=12, size=15, weight=700, parent=None):
    button = QPushButton(text, parent)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setFixedHeight(height)
    style_button(button, kind, radius, size, weight)
    if icon_name:
        button.setIcon(kit_icon(icon_name, icon_color))
        button.setIconSize(QSize(15, 15))
    return button


def _icon_button(icon_name, tooltip, parent=None):
    button = _button('', 'icon', 30, icon_name, QColor(255, 255, 255, 204), radius=8, parent=parent)
    button.setFixedWidth(30)
    button.setStyleSheet(button.styleSheet().replace('padding: 0 14px;', 'padding: 0;'))
    button.setToolTip(tooltip)
    return button


class Dot(QWidget):
    def __init__(self, size=8, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._color = TONE_COLORS['idle']

    def set_tone(self, tone):
        color = TONE_COLORS.get(tone, TONE_COLORS['idle'])
        if color != self._color:
            self._color = color
            self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._color)
        painter.drawEllipse(self.rect())


class Avatar(QWidget):
    """角色头像: assets/char_heads/<类名>.png 剪成圆, 没有图就画名字的第一个字。"""

    def __init__(self, size=60, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._pixmap = None
        self._text = ''
        self._key = None

    def set_char(self, cls_name, label):
        if cls_name == self._key:
            return
        self._key = cls_name
        path = CHAR_HEADS / f'{cls_name}.png'
        pixmap = QPixmap(str(path)) if cls_name and path.exists() else QPixmap()
        self._pixmap = None if pixmap.isNull() else pixmap
        self._text = (label or cls_name or '?')[:1]
        self.setToolTip(label or cls_name or '')
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addEllipse(rect)
        painter.setClipPath(path)
        painter.fillPath(path, QColor('#F4F4F5') if self._pixmap else QColor(255, 255, 255, 40))
        if self._pixmap is not None:
            x, y, w, h = cover_source_rect(self._pixmap.width(), self._pixmap.height(), rect.width(), rect.height())
            painter.drawPixmap(rect, self._pixmap, QRectF(x, y, w, h))
        else:
            painter.setPen(QColor(WHITE))
            font = painter.font()
            font.setPixelSize(int(self.height() * 0.36))
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._text)
        painter.setClipping(False)
        painter.setPen(QPen(QColor(255, 255, 255, 110), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(rect)


class GlassFrame(QFrame):
    """毛玻璃面板: 画主页模糊底里自己那一块, 再盖半透明深色和一圈细边。"""

    def __init__(self, home, radius=16, alpha=GLASS_ALPHA, parent=None):
        super().__init__(parent)
        self._home = home
        self._radius = radius
        self._alpha = alpha

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)
        painter.setClipPath(path)
        blurred = self._home.blurred_backdrop()
        if blurred is not None:
            origin = self.mapTo(self._home, QPoint(0, 0))
            dpr = blurred.devicePixelRatio()
            source = QRectF(origin.x() * dpr, origin.y() * dpr, self.width() * dpr, self.height() * dpr)
            painter.drawPixmap(QRectF(self.rect()), blurred, source)
        painter.fillPath(path, QColor(12, 14, 20, int(255 * self._alpha)))
        painter.setClipping(False)
        painter.setPen(QPen(QColor(255, 255, 255, 30), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)


def _icon_tile(icon_name, parent=None):
    tile = QLabel(parent)
    tile.setFixedSize(36, 36)
    tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
    tile.setStyleSheet('background: rgba(255, 255, 255, 0.10); border-radius: 10px;')
    tile.setPixmap(kit_icon(icon_name, WHITE).pixmap(QSize(18, 18)))
    return tile


def _chip(text, parent=None):
    chip = _label(text, 12, 400, WHITE, parent)
    chip.setStyleSheet(chip.styleSheet() + ' background: rgba(255, 255, 255, 0.10); border-radius: 11px; padding: 0 9px;')
    chip.setFixedHeight(22)
    chip.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return chip


class _CardHeader(QWidget):
    def __init__(self, title, icon_name, settings_tip, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        row.addWidget(_icon_tile(icon_name))
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(_label(title, 16, 700))
        status_row = QHBoxLayout()
        status_row.setSpacing(6)
        self.dot = Dot(6)
        self.status = _label('', 12, 400, TEXT_SECONDARY)
        status_row.addWidget(self.dot)
        status_row.addWidget(self.status, 1)
        text.addLayout(status_row)
        row.addLayout(text, 1)
        self.settings_button = _icon_button('gear', settings_tip)
        row.addWidget(self.settings_button, 0, Qt.AlignmentFlag.AlignTop)

    def set_status(self, text, tone):
        self.status.setText(text)
        self.dot.set_tone(tone)


class SwitchKnob(QWidget):
    """开关的样子, 只管画; 点击交给外面那一整行。"""

    WIDTH = 40
    HEIGHT = 22

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._on = False

    def set_on(self, on):
        if on != self._on:
            self._on = on
            self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        radius = self.HEIGHT / 2
        painter.setBrush(QColor(ACCENT) if self._on else QColor(255, 255, 255, 46))
        painter.drawRoundedRect(QRectF(0, 0, self.WIDTH, self.HEIGHT), radius, radius)
        knob = self.HEIGHT - 6
        x = self.WIDTH - 3 - knob if self._on else 3
        painter.setBrush(QColor(ON_ACCENT) if self._on else QColor(255, 255, 255, 210))
        painter.drawEllipse(QRectF(x, 3, knob, knob))


class ToggleRow(QPushButton):
    """卡片底下一整行的开关: 左边写开没开, 右边是开关, 点哪儿都算。"""

    def __init__(self, height=46, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(height)
        base, hover, pressed = BUTTON_STYLES['glass']
        self.setStyleSheet(f'QPushButton {{ {base} border-radius: 12px; }}'
                           f'QPushButton:hover {{ {hover} }} QPushButton:pressed {{ {pressed} }}')
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 0, 14, 0)
        row.setSpacing(10)
        self.text = _label('', 15, 700)
        self.knob = SwitchKnob()
        self.text.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(self.text)
        row.addStretch(1)
        row.addWidget(self.knob)
        self._on = None

    @property
    def on(self):
        return bool(self._on)

    def set_on(self, on):
        if on == self._on:
            return
        self._on = on
        self.knob.set_on(on)
        self.text.setText('已开启' if on else '已关闭')
        color = WHITE if on else TEXT_SECONDARY
        self.text.setStyleSheet(f'color: {color}; background: transparent; font-family: {FONT_FAMILY}; '
                                'font-size: 15px; font-weight: 700;')


# --- 主页 -----------------------------------------------------------------------------

class HomeTab(QWidget):
    # MainWindow 读这几个: 放在「运行」分组最上面, 打开程序先显示它
    add_after_default_tabs = False
    nav_section = 'run'
    startup_page = True
    # 主页自己有连接状态 (左上) 和总开关 (右上), 主窗口在这页把顶上那条运行栏收起来
    show_run_bar = False
    icon = 'home'

    def __init__(self):
        super().__init__()
        self.setObjectName('coco_home')
        self.name = '首页'
        self.executor = None
        self.config = Config(CONFIG_NAME, {BG_KEY: ''})
        self._image = None  # 原图 (QImage)
        self._scene = None  # 背景图 + 渐变, 按当前尺寸合成好的 (QPixmap)
        self._blurred = None
        self._built_for = None
        self._stacked = None
        # 点了开关但任务还没真的开/关 (开是在后台线程里开的): (想要的状态, 截止时间), 期间按想要的画
        self._combat_wanted = None
        self._last_run_team = None
        self._last_run_read = 0.0
        self._team_code = (None, '')  # (队伍, 「队伍代码：自定义/内置」), 换队伍才重新比对代码文件

        self._rebuild_timer = QTimer(self)
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.setInterval(60)
        self._rebuild_timer.timeout.connect(self._rebuild_backdrop)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(REFRESH_MS)
        self._refresh_timer.timeout.connect(self.refresh)

        self._build()
        self._load_background()
        try:
            from ok.ui.qt.Communicate import communicate
            for signal in (communicate.executor_paused, communicate.task, communicate.window):
                signal.connect(self._on_event)
        except Exception as e:
            logger.warning(f'主页: 接不上执行器信号 ({e}), 只靠定时刷新')

    # --- 搭界面 ---------------------------------------------------------------------

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 20)
        root.setSpacing(0)

        top = QHBoxLayout()
        top.setSpacing(12)
        self.status_chip = GlassFrame(self, radius=15)
        self.status_chip.setFixedHeight(30)
        chip_row = QHBoxLayout(self.status_chip)
        chip_row.setContentsMargins(12, 0, 14, 0)
        chip_row.setSpacing(8)
        self.connection_dot = Dot(7)
        self.connection_label = _label('', 12, 600)
        self.connection_detail = _label('', 12, 400, 'rgba(255, 255, 255, 0.55)')
        self.connection_chevron = QLabel()
        self.connection_chevron.setFixedSize(12, 12)
        self.connection_chevron.setPixmap(kit_icon('chevron-down', QColor(255, 255, 255, 140), 2).pixmap(12, 12))
        chip_row.addWidget(self.connection_dot)
        chip_row.addWidget(self.connection_label)
        chip_row.addWidget(self.connection_detail)
        chip_row.addWidget(self.connection_chevron)
        # 点一下打开连接设置 (选游戏窗口、截图方式、输入方式), 原来顶上运行栏左边那个
        self.status_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.status_chip.setToolTip('游戏窗口、截图和输入方式')
        self.status_chip.installEventFilter(self)
        top.addWidget(self.status_chip, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addStretch(1)

        self.live_strip = GlassFrame(self, radius=17, alpha=0.72)
        self.live_strip.setFixedHeight(34)
        strip_row = QHBoxLayout(self.live_strip)
        strip_row.setContentsMargins(14, 0, 5, 0)
        strip_row.setSpacing(10)
        self.live_dot = Dot(8)
        self.live_dot.set_tone('accent')
        self.live_title = _label('', 13, 700)
        self.live_detail = _label('', 13, 400, TEXT_SECONDARY)
        self.live_button = _button('详细', 'glass', 24, 'chevron-right', radius=12, size=12, weight=600)
        self.live_button.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.live_button.setToolTip('去详细面板看这一局')
        self.live_button.clicked.connect(lambda: self._open_page('DashboardTab'))
        for widget in (self.live_dot, self.live_title, self.live_detail, self.live_button):
            strip_row.addWidget(widget)
        self.live_strip.hide()
        top.addWidget(self.live_strip, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addStretch(1)

        self.background_button = _button('', 'dark', 34, 'image', radius=17)
        self.background_button.setFixedWidth(34)
        self.background_button.setStyleSheet(
            self.background_button.styleSheet().replace('padding: 0 14px;', 'padding: 0;'))
        self.background_button.setToolTip('换背景')
        self.background_button.clicked.connect(self._open_background_menu)
        top.addWidget(self.background_button, 0, Qt.AlignmentFlag.AlignVCenter)
        # 整个识别的总开关, 跟运行栏那个按钮、F9 是同一个动作; 自动战斗自己的开关在卡片里
        self.run_button = _button('启动', 'accent', 34, 'play', ON_ACCENT, radius=17, size=14)
        self.run_button.setMinimumWidth(112)
        self.run_button.setToolTip('启动识别: 开着的功能 (自动战斗等) 开始工作, 游戏没开会先启动游戏')
        self.run_button.clicked.connect(self._on_run_action)
        top.addWidget(self.run_button, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(top)
        root.addStretch(1)

        self.bottom = QWidget(self)
        self.bottom_grid = QGridLayout(self.bottom)
        self.bottom_grid.setContentsMargins(24, 0, 0, 0)
        self.bottom_grid.setHorizontalSpacing(24)
        self.bottom_grid.setVerticalSpacing(20)
        self.team_block = self._build_team_block()
        self.cards = QWidget(self.bottom)
        cards_row = QHBoxLayout(self.cards)
        cards_row.setContentsMargins(0, 0, 0, 0)
        cards_row.setSpacing(14)
        self.combat_card = self._build_combat_card()
        self.farm_card = self._build_farm_card()
        cards_row.addWidget(self.combat_card)
        cards_row.addWidget(self.farm_card)
        root.addWidget(self.bottom)
        self._apply_layout(stacked=False)
        # 卡片那排挪了位置 (队伍名变长、窗口变高), 渐变起点跟着变, 背景要重新合成
        self.bottom.installEventFilter(self)

    def eventFilter(self, watched, event):
        # 状态胶囊在底部区域之前建好, 那时候还没有 self.bottom
        if watched is getattr(self, 'bottom', None) and event.type() in (QEvent.Type.Move, QEvent.Type.Resize):
            self._schedule_rebuild()
        elif watched is self.status_chip and event.type() == QEvent.Type.MouseButtonRelease                 and event.button() == Qt.MouseButton.LeftButton:
            self._open_connection()
            return True
        return super().eventFilter(watched, event)

    @property
    def connection_anchor(self):
        """主窗口在这页打开连接设置时挂在这个胶囊下面。"""
        return self.status_chip

    def _open_connection(self):
        window = getattr(og, 'main_window', None)
        opener = getattr(window, 'open_connection_popover', None)
        if opener is not None:
            opener(self.status_chip)

    def _build_team_block(self):
        block = QWidget(self.bottom)
        block.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        column = QVBoxLayout(block)
        column.setContentsMargins(0, 0, 0, 4)
        column.setSpacing(0)
        caption = _label('当前队伍', 13, 400, 'rgba(255, 255, 255, 0.65)')
        self.team_name = _label('', 30, 800)
        self.team_name.setMinimumWidth(0)
        _shadow(self.team_name)
        column.addWidget(caption)
        column.addSpacing(4)
        column.addWidget(self.team_name)
        column.addSpacing(14)
        avatars = QHBoxLayout()
        avatars.setSpacing(12)
        self.avatars = [Avatar(58) for _ in range(3)]
        for avatar in self.avatars:
            avatars.addWidget(avatar)
        self.team_code_button = _button('队伍代码', 'dashed', 34, radius=17, size=13, weight=500)
        self.team_code_button.setToolTip('去角色代码页改这个队伍的代码')
        self.team_code_button.clicked.connect(lambda: self._open_page('CharacterCodeTab'))
        avatars.addSpacing(4)
        avatars.addWidget(self.team_code_button)
        avatars.addStretch(1)
        column.addLayout(avatars)
        column.addSpacing(12)
        self.team_detail = _label('', 13, 400, 'rgba(255, 255, 255, 0.58)')
        column.addWidget(self.team_detail)
        return block

    def _build_combat_card(self):
        card = GlassFrame(self)
        card.setFixedWidth(CARD_WIDTH)
        column = QVBoxLayout(card)
        column.setContentsMargins(18, 18, 18, 18)
        column.setSpacing(12)
        self.combat_header = _CardHeader('自动战斗', 'bolt', '自动战斗的全部设置')
        self.combat_header.settings_button.clicked.connect(lambda: self._show_task(self._auto_task()))
        column.addWidget(self.combat_header)
        self.combat_hint = _label('', 13, 400, 'rgba(255, 255, 255, 0.75)')
        self.combat_hint.setWordWrap(True)
        self.combat_hint.setMinimumHeight(38)
        self.combat_hint.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        column.addWidget(self.combat_hint)
        self.chip_row = QHBoxLayout()
        self.chip_row.setSpacing(6)
        self.chips = [_chip('') for _ in range(3)]
        for chip in self.chips:
            self.chip_row.addWidget(chip)
        self.chip_row.addStretch(1)
        column.addLayout(self.chip_row)
        column.addStretch(1)
        # 只开关自动战斗这个任务; 整个识别启不启动看右上角的总开关
        self.combat_toggle = ToggleRow(46)
        self.combat_toggle.setToolTip('只开关自动战斗; 整个识别用右上角的「启动」')
        self.combat_toggle.clicked.connect(self._on_combat_toggle)
        column.addWidget(self.combat_toggle)
        return card

    def _build_farm_card(self):
        card = GlassFrame(self)
        card.setFixedWidth(CARD_WIDTH)
        column = QVBoxLayout(card)
        column.setContentsMargins(18, 18, 18, 18)
        column.setSpacing(12)
        self.farm_header = _CardHeader('刷4C声骸', 'layers', '刷4C的全部设置')
        self.farm_header.settings_button.clicked.connect(lambda: self._show_task(self._farm_task()))
        column.addWidget(self.farm_header)

        self.target_button = QPushButton()
        self.target_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.target_button.setFixedHeight(52)
        self.target_button.setToolTip('换 Boss 或传送方式')
        self.target_button.setStyleSheet(
            'QPushButton { background: rgba(255, 255, 255, 0.08); border: 0; border-radius: 10px; }'
            'QPushButton:hover { background: rgba(255, 255, 255, 0.14); }')
        target_row = QHBoxLayout(self.target_button)
        target_row.setContentsMargins(12, 6, 10, 6)
        target_text = QVBoxLayout()
        target_text.setSpacing(1)
        self.target_title = _label('', 14, 700)
        self.target_sub = _label('', 11, 400, 'rgba(255, 255, 255, 0.55)')
        target_text.addWidget(self.target_title)
        target_text.addWidget(self.target_sub)
        target_row.addLayout(target_text, 1)
        chevron = QLabel()
        chevron.setPixmap(kit_icon('chevron-down', WHITE).pixmap(QSize(14, 14)))
        chevron.setStyleSheet('background: transparent;')
        target_row.addWidget(chevron)
        for widget in (self.target_title, self.target_sub, chevron):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.target_button.clicked.connect(self._open_boss_menu)
        column.addWidget(self.target_button)

        stats = QHBoxLayout()
        stats.setSpacing(22)
        self.echo_value, self.echo_caption = self._stat(stats)
        self.rate_value, self.rate_caption = self._stat(stats)
        stats.addStretch(1)
        column.addLayout(stats)
        column.addStretch(1)
        self.farm_button = _button('开始刷', 'glass', 46, 'play')
        self.farm_button.clicked.connect(self._on_farm_action)
        column.addWidget(self.farm_button)
        return card

    @staticmethod
    def _stat(row):
        column = QVBoxLayout()
        column.setSpacing(0)
        value = _label('—', 22, 700)
        caption = _label('', 11, 400, 'rgba(255, 255, 255, 0.55)')
        column.addWidget(value)
        column.addWidget(caption)
        row.addLayout(column)
        return value, caption

    def _apply_layout(self, stacked):
        if stacked == self._stacked:
            return
        self._stacked = stacked
        grid = self.bottom_grid
        grid.removeWidget(self.team_block)
        grid.removeWidget(self.cards)
        if stacked:
            grid.addWidget(self.team_block, 0, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
            grid.addWidget(self.cards, 1, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 0)
        else:
            grid.addWidget(self.team_block, 0, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
            grid.addWidget(self.cards, 0, 1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 0)

    # --- 背景 -----------------------------------------------------------------------

    def _background_path(self):
        value = self.config.get(BG_KEY) or ''
        return Path(value) if value else None

    def _load_background(self):
        path = self._background_path()
        image = None
        if path is not None:
            loaded = QImage(str(path)) if path.exists() else QImage()
            if loaded.isNull():
                logger.warning(f'主页: 背景图读不出来 {path}, 先用纯色')
            else:
                if max(loaded.width(), loaded.height()) > BG_MAX_SIDE:
                    loaded = loaded.scaled(BG_MAX_SIDE, BG_MAX_SIDE, Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation)
                image = loaded.convertToFormat(QImage.Format.Format_RGB32)
        self._image = image
        self._built_for = None
        self._schedule_rebuild()

    def _schedule_rebuild(self):
        self._rebuild_timer.start()

    def _fade_top(self):
        return fade_top(self.height(), self.bottom.geometry().top())

    def _rebuild_backdrop(self):
        if self.width() <= 0 or self.height() <= 0:
            return
        dpr = self.devicePixelRatioF()
        size = QSize(max(1, round(self.width() * dpr)), max(1, round(self.height() * dpr)))
        top = self._fade_top()
        key = (size.width(), size.height(), top, id(self._image))
        if key == self._built_for:
            return
        scene = QImage(size, QImage.Format.Format_RGB32)
        scene.setDevicePixelRatio(dpr)
        painter = QPainter(scene)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        target = QRectF(0, 0, self.width(), self.height())
        if self._image is not None:
            x, y, w, h = cover_source_rect(self._image.width(), self._image.height(), size.width(), size.height())
            painter.drawImage(target, self._image, QRectF(x, y, w, h))
        else:
            painter.fillRect(target, FALLBACK_BG)
            painter.setPen(QColor(255, 255, 255, 80))
            font = painter.font()
            font.setPixelSize(14)
            painter.setFont(font)
            hint_area = QRectF(0, 0, self.width(), max(80, top))
            painter.drawText(hint_area, Qt.AlignmentFlag.AlignCenter, '右上角「换背景」可以放一张自己喜欢的图')
        gradient = QLinearGradient(0, top, 0, self.height())
        for position, alpha in FADE_STOPS:
            gradient.setColorAt(position, QColor(*BASE, int(255 * alpha)))
        painter.fillRect(QRectF(0, top, self.width(), self.height() - top), gradient)
        painter.end()

        small = scene.scaled(max(1, size.width() // BLUR_DOWNSCALE), max(1, size.height() // BLUR_DOWNSCALE),
                             Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        middle = small.scaled(max(1, size.width() // 4), max(1, size.height() // 4),
                              Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        blurred = middle.scaled(size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self._scene = QPixmap.fromImage(scene)
        self._scene.setDevicePixelRatio(dpr)
        self._blurred = QPixmap.fromImage(blurred)
        self._blurred.setDevicePixelRatio(dpr)
        self._built_for = key
        self.update()

    def blurred_backdrop(self):
        return self._blurred

    def paintEvent(self, _event):
        painter = QPainter(self)
        if self._scene is not None:
            painter.drawPixmap(QRectF(self.rect()), self._scene, QRectF(self._scene.rect()))
        else:
            painter.fillRect(self.rect(), FALLBACK_BG)

    def _open_background_menu(self):
        menu = QMenu(self)
        menu.addAction('选择图片…', self._choose_background)
        clear = menu.addAction('恢复纯色', self._clear_background)
        clear.setEnabled(self._background_path() is not None)
        # 按钮贴着右边, 菜单右边对齐按钮往左展开
        button = self.background_button
        menu.exec(button.mapToGlobal(QPoint(button.width() - menu.sizeHint().width(), button.height() + 4)))

    def _choose_background(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择背景图片', '', '图片 (*.png *.jpg *.jpeg *.bmp *.webp)')
        if not path:
            return
        source = Path(path)
        if QImage(str(source)).isNull():
            self._toast('这张图读不出来，换一张试试', 'danger')
            return
        folder = Path(Config.config_folder)
        destination = folder / f'{BG_FILE_STEM}{source.suffix.lower()}'
        try:
            folder.mkdir(parents=True, exist_ok=True)
            data = source.read_bytes()
            self._remove_stored_backgrounds()
            destination.write_bytes(data)
        except OSError as e:
            self._toast(f'背景图保存失败: {e}', 'danger')
            return
        self.config[BG_KEY] = destination.as_posix()
        self._load_background()

    def _clear_background(self):
        self._remove_stored_backgrounds()
        self.config[BG_KEY] = ''
        self._load_background()

    @staticmethod
    def _remove_stored_backgrounds():
        for old in Path(Config.config_folder).glob(f'{BG_FILE_STEM}.*'):
            try:
                old.unlink()
            except OSError as e:
                logger.warning(f'主页: 删不掉旧背景图 {old}: {e}')

    # --- 刷新 -----------------------------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_timer.start()
        self.refresh()
        self._schedule_rebuild()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._refresh_timer.stop()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_layout(self.width() < STACK_BELOW)
        self._schedule_rebuild()

    def _on_event(self, *_args):
        if self.isVisible():
            self.refresh()

    def _executor(self):
        return self.executor or getattr(og, 'executor', None)

    def _task(self, module, name):
        executor = self._executor()
        if executor is None:
            return None
        try:
            cls = getattr(__import__(module, fromlist=[name]), name)
            return executor.get_task_by_class(cls)
        except Exception:
            return None

    def _auto_task(self):
        return self._task('src.task.AutoCombatTask', 'AutoCombatTask')

    def _farm_task(self):
        return self._task('src.task.FarmEchoTask', 'FarmEchoTask')

    @staticmethod
    def _run_bar():
        return getattr(getattr(og, 'main_window', None), 'run_bar', None)

    def _run_button(self):
        run_bar = self._run_bar()
        try:
            return run_bar._compute_state().button if run_bar is not None else 'start'
        except Exception:
            return 'start'

    def _hotkey(self):
        run_bar = self._run_bar()
        try:
            return run_bar.hotkey() if run_bar is not None else ''
        except Exception:
            return ''

    @staticmethod
    def _tr(text):
        app = getattr(og, 'app', None)
        return app.tr(text) if app is not None and text else (text or '')

    def refresh(self):
        try:
            self._refresh_connection()
            self._refresh_run()
            self._refresh_team()
            farm_active = self._refresh_farm()
            self._refresh_combat(farm_active)
        except Exception as e:
            logger.error(f'主页刷新出错: {e}')

    def _refresh_connection(self):
        manager = getattr(og, 'device_manager', None)
        device = manager.get_preferred_device() if manager is not None else None
        game = game_display_name(getattr(og, 'config', None) or {})
        if device is None:
            self.connection_dot.set_tone('idle')
            self.connection_label.setText('还没选游戏')
            self.connection_detail.setText('')
            return
        connected = bool(device.get('connected'))
        self.connection_dot.set_tone('success' if connected else 'idle')
        self.connection_label.setText(f'{game} {"已连接" if connected else "未连接"}')
        details = []
        if connected and device.get('resolution'):
            details.append(str(device.get('resolution')))
        interaction = manager.config.get('interaction') if device.get('device') == 'windows' else None
        if interaction:
            details.append(INTERACTION_NAMES.get(str(interaction), str(interaction)))
        self.connection_detail.setText(' · '.join(details))

    def _current_team(self):
        executor = self._executor()
        if executor is not None:
            try:
                from src.char.CharFactory import char_dict
                tasks = list(getattr(executor, 'trigger_tasks', [])) + list(getattr(executor, 'onetime_tasks', []))
                team = team_from_tasks(tasks, char_dict)
                if team:
                    return team, '当前识别'
            except Exception as e:
                logger.debug(f'主页: 读当前队伍失败 {e}')
        now = time.monotonic()
        if now - self._last_run_read > LAST_RUN_CACHE_SECONDS:
            self._last_run_team = team_from_last_run()
            self._last_run_read = now
        return (self._last_run_team, '上次战斗') if self._last_run_team else (None, '')

    def _refresh_team(self):
        team, source = self._current_team()
        if not team:
            self.team_name.setText('还没认出队伍')
            self.team_detail.setText('进一次战斗后这里会显示当前队伍')
            for avatar in self.avatars:
                avatar.hide()
            return
        labels = [self._tr(name) for name in team]
        self.team_name.setText(' · '.join(labels))
        for avatar, name, label in zip(self.avatars, team, labels):
            avatar.set_char(name, label)
            avatar.show()
        key = tuple(team)
        if self._team_code[0] != key:
            code = ''
            try:
                from src.char.CustomCharLoader import team_runs_custom_code
                code = '队伍代码：自定义' if team_runs_custom_code(team) else '队伍代码：内置'
            except Exception:
                pass
            self._team_code = (key, code)
        self.team_detail.setText(' · '.join(part for part in (source, self._team_code[1]) if part))

    def _refresh_run(self):
        text, icon_name, kind, enabled = run_view(self._run_button(), self._hotkey())
        button = self.run_button
        button.setText(text)
        button.setIcon(kit_icon(icon_name, ON_ACCENT if kind == 'accent' else WHITE))
        button.setEnabled(enabled)
        if button.property('kind') != kind:
            button.setProperty('kind', kind)
            style_button(button, kind, radius=17, size=14)

    def _combat_enabled(self, task):
        if self._combat_wanted is not None:
            wanted, deadline = self._combat_wanted
            if bool(task.enabled) != wanted and time.monotonic() < deadline:
                return wanted
            self._combat_wanted = None
        return bool(task.enabled)

    def _refresh_combat(self, farm_active):
        task = self._auto_task()
        self.combat_card.setVisible(task is not None)
        if task is None:
            return
        executor = self._executor()
        in_combat = executor is not None and getattr(executor, 'current_task', None) is task
        enabled = self._combat_enabled(task)
        run_button = self._run_button()
        status, tone = combat_view(run_button, enabled, in_combat, farm_active)
        self.combat_header.set_status(status, tone)
        self.combat_toggle.set_on(enabled)
        f_start_key = getattr(task, 'F_START_KEY', '按 F 开始战斗')
        f_start = bool(task.config.get(f_start_key))
        self.combat_hint.setText(combat_hint(run_button, enabled, f_start, self._hotkey(), farm_active))
        chips = combat_chips(task.config, f_start_key, getattr(task, 'F_DELAY_KEY', 'F 之后等待(秒)'))
        # 一行放不下就从后面往前藏, 不让标签被截掉半个字
        room = CARD_WIDTH - 36
        for chip, text in zip(self.chips, chips + [''] * 3):
            chip.setText(text)
            width = chip.sizeHint().width() if text else 0
            fits = bool(text) and width <= room
            chip.setVisible(fits)
            if fits:
                room -= width + self.chip_row.spacing()
        hotkey = self._hotkey()
        if in_combat and not farm_active:
            self.live_title.setText('自动战斗 · 战斗中')
            self.live_detail.setText(f'按 {hotkey} 暂停' if hotkey else '')
            self.live_strip.show()
        elif not farm_active:
            self.live_strip.hide()

    def _refresh_farm(self):
        task = self._farm_task()
        self.farm_card.setVisible(task is not None)
        if task is None:
            return False
        from ok.ui.qt.tasks.tasks_page import task_active, task_status
        active = task_active(task)
        status = task_status(task, self._executor()) if self._executor() is not None else None
        text, tone = (status[0], 'accent' if status[1] in ('accent', 'success') else status[1]) if status else ('待命', 'idle')
        self.farm_header.set_status(text, tone)
        title, sub = farm_target(task.config, getattr(task, 'TELEPORT_KEYS', {}), self._tr)
        self.target_title.setText(title)
        self.target_sub.setText(sub)
        info = getattr(task, 'info', None) or {}
        prefix = '' if active else '上次'
        self.echo_value.setText(format_number(info.get('Echo Count')))
        self.echo_caption.setText(f'{prefix}获得声骸')
        self.rate_value.setText(format_number(info.get('Echo per Hour')))
        self.rate_caption.setText('每小时声骸')
        if active:
            self.farm_button.setText('停止')
            self.farm_button.setIcon(kit_icon('stop', WHITE))
            started = getattr(task, 'start_time', 0) or 0
            elapsed = format_elapsed(time.time() - started) if started and task.enabled else ''
            self.live_title.setText(f'刷4C · 已用 {elapsed}' if elapsed else '刷4C · 准备中')
            count = info.get('Echo Count')
            self.live_detail.setText(title + (f' · 获得 {format_number(count)}' if count not in (None, '') else ''))
            self.live_strip.show()
        else:
            self.farm_button.setText('开始刷')
            self.farm_button.setIcon(kit_icon('play', WHITE))
        return active

    # --- 操作 -----------------------------------------------------------------------

    def _on_run_action(self):
        """右上角总开关: 跟按 F9 一样, 交给运行栏去启动/暂停/取消启动。"""
        try:
            run_bar = self._run_bar()
            if run_bar is not None:
                run_bar.refresh()
                run_bar.toggle_executor()
            else:
                og.app.start_controller.start()
        except Exception as e:
            logger.error(f'主页: 启动按钮出错 {e}')
            self._toast(str(e), 'danger')
        QTimer.singleShot(200, self.refresh)

    def _on_combat_toggle(self):
        """自动战斗自己的开关: 只开关这个任务, 不启动也不暂停整个识别。"""
        task = self._auto_task()
        if task is None:
            return
        wanted = not self.combat_toggle.on
        try:
            from ok.ui.qt.tasks.tasks_page import set_trigger_enabled
            set_trigger_enabled(task, wanted)
            self._combat_wanted = (wanted, time.monotonic() + 3)
        except Exception as e:
            logger.error(f'主页: 自动战斗开关出错 {e}')
            self._toast(str(e), 'danger')
        self.refresh()
        QTimer.singleShot(300, self.refresh)

    def _on_farm_action(self):
        task = self._farm_task()
        if task is None:
            return
        from ok.ui.qt.tasks.tasks_page import start_task, stop_task, task_active
        try:
            if task_active(task):
                stop_task(task)
            else:
                start_task(task, self)
        except Exception as e:
            logger.error(f'主页: 刷4C 按钮出错 {e}')
            self._toast(str(e), 'danger')
        QTimer.singleShot(200, self.refresh)

    def _open_boss_menu(self):
        task = self._farm_task()
        if task is None:
            return
        keys = getattr(task, 'TELEPORT_KEYS', {})
        mode = task.config.get('Teleport to Boss', NO_TELEPORT)
        menu = QMenu(self)
        if mode in keys:
            from src.task import BossBook
            name_key = keys[mode][0]
            current = task.config.get(name_key)
            options = BossBook.known_options(BossBook.CATEGORY_OF_MODE[mode])
            for option in options:
                action = menu.addAction(option)
                action.setCheckable(True)
                action.setChecked(option == current)
                action.triggered.connect(lambda _=False, k=name_key, v=option: self._set_farm(task, k, v))
            if not options:
                menu.addAction('跑过一次后这里会列出 Boss').setEnabled(False)
            menu.addSeparator()
        modes = menu.addMenu('传送方式')
        for value in [NO_TELEPORT] + list(keys):
            action = modes.addAction(self._tr(value))
            action.setCheckable(True)
            action.setChecked(value == mode)
            action.triggered.connect(lambda _=False, v=value: self._set_farm(task, 'Teleport to Boss', v))
        menu.exec(self.target_button.mapToGlobal(QPoint(0, self.target_button.height() + 4)))

    def _set_farm(self, task, key, value):
        task.config[key] = value
        self.refresh()

    def _show_task(self, task):
        window = getattr(og, 'main_window', None)
        if task is not None and window is not None:
            window.show_task(task)

    def _open_page(self, class_name_):
        window = getattr(og, 'main_window', None)
        pages = getattr(window, '_pages', {}) if window is not None else {}
        for page in pages.values():
            if type(page).__name__ == class_name_:
                window.switch_to(page)
                return

    def _toast(self, message, tone='neutral'):
        try:
            from ok.ui.qt.kit.feedback import show_toast
            show_toast(self.window(), message, tone=tone)
        except Exception:
            logger.warning(message)
