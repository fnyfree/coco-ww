"""三火队轴的干跑: 假 task + 假读屏, 真跑一遍启动轴和循环轴。

验证的是【调用链和时长】, 不是打得对不对:
  - 有没有 AttributeError / 参数对不上
  - 每一格的按键顺序是不是视频里那个
  - 一圈的墙钟跟视频的 29 秒差多少
"""
import threading
import os
import sys, time, importlib.util
def _okww_working():
    """框架 working 目录(有 src/char/CustomCharLoader.py 的那层): OKWW_WORKING > 本文件往上找 > 旧版 OKWW 的默认位置。
    coco-ww 是平铺结构, 往上找到的就是安装目录本身; 以前写死旧版路径, 在 coco-ww 里跑测的是旧框架和旧版队伍代码。"""
    import os
    cands = [os.environ.get('OKWW_WORKING')]
    d = os.path.dirname(os.path.abspath(__file__))
    while True:
        cands.append(d)
        up = os.path.dirname(d)
        if up == d:
            break
        d = up
    cands.append(r'F:\ok-ww\data\apps\ok-ww\working')
    for c in cands:
        if c and os.path.isfile(os.path.join(c, 'src', 'char', 'CustomCharLoader.py')):
            return c
    raise SystemExit('找不到框架的 working 目录(里面有 src/char/CustomCharLoader.py), 设环境变量 OKWW_WORKING 再跑')


_WORKING = _okww_working()
sys.path.insert(0, _WORKING)
sys.stdout.reconfigure(encoding='utf-8')

from src.char.BaseChar import CharType

LOG = []
T0 = time.time()


def rec(what):
    LOG.append((time.time() - T0, what))


class FakeExecutor:
    # coco-ww 的 MacroKit.macro_wait 走 BaseChar.precise_wait, 它每片都看 exit_event(停任务)和暂停状态
    exit_event = threading.Event()
    current_task = None
    debug_mode = True
    pause_start = None

    def blocked_by_pause(self):
        return False

    trigger_tasks = []
    onetime_tasks = []

    def check_enabled(self, check_pause=False):
        pass


class FakeTask:
    def __init__(self):
        self.executor = FakeExecutor()
        self.chars = []
        self.key_config = {'Echo Key': 'q', 'Liberation Key': 'r', 'Resonance Key': 'e',
                           'Jump Key': 'space', 'Dodge Key': 'lshift'}
        self.skip_combat_check = False
        self.use_liberation = True
        self.in_liberation = False
        self.can_break = False
        self.has_lavitator = False
        self.on_field = 1          # 场上是谁(index)
        self._pending = None       # (index, 生效时刻)
        self._anim_until = 0       # 大招演出到什么时候(HUD 不在)
        self._f_last_press = -1

    # ---- 读屏 ----
    def in_team(self):
        now = time.time()
        if self._pending and now >= self._pending[1]:
            self.on_field = self._pending[0]
            self._pending = None
        if now < self._anim_until:
            return False, -1, 0
        return True, self.on_field, 3

    def next_frame(self):
        time.sleep(0.016)

    # ---- 输入 ----
    def send_key(self, key, down_time=0.02, interval=-1, after_sleep=0):
        k = str(key)
        rec(f'key:{k}')
        if k in ('1', '2', '3'):
            idx = int(k) - 1
            if idx != self.on_field and self._pending is None:
                self._pending = (idx, time.time() + 0.12)
        elif k == 'r':
            # 大招: 演出 3.5 秒, 期间 HUD 不在
            if time.time() >= self._anim_until:
                self._anim_until = time.time() + 3.5
        if after_sleep:
            time.sleep(after_sleep)

    def click(self, *a, **kw):
        rec('A')

    def mouse_down(self, *a, **kw):
        rec('hold_down')

    def mouse_up(self, *a, **kw):
        rec('hold_up')

    def jump(self, after_sleep=0.01):
        rec('jump')

    def f_break(self):
        return False

    def add_freeze_duration(self, *a, **kw):
        pass

    def raise_not_in_combat(self, msg):
        raise RuntimeError(msg)

    def wait_until(self, fn, post_action=None, time_out=1.0, **kw):
        end = time.time() + time_out
        while time.time() < end:
            if fn():
                return True
            if post_action:
                post_action()
            time.sleep(0.02)
        return False

    def box_of_screen_scaled(self, *a, **kw):
        return None

    def find_one(self, *a, **kw):
        return None

    def get_cd(self, *a, **kw):
        return 0

    def is_pick_f(self, *a, **kw):
        return False

    def get_resonance_key(self, *a, **kw):
        return 'e'

    def get_echo_key(self, *a, **kw):
        return 'q'

    def get_liberation_key(self, *a, **kw):
        return 'r'

    def time_elapsed_accounting_for_freeze(self, start, intro_motion_freeze=False):
        return time.time() - start


def load(name):
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), f'{name}.py')   # 跟本文件同目录的队伍代码
    spec = importlib.util.spec_from_file_location(f'dry_{name}', p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return getattr(m, name)


task = FakeTask()
CL = load('Changli')(task, 0, char_name='chang_changli', char_type=CharType.MAIN_DPS)
BR = load('Brant')(task, 2, char_name='char_brant', char_type=CharType.HEALER)
LU = load('Lupa')(task, 1, char_name='char_lupa', char_type=CharType.SUB_DPS)
task.chars = [CL, LU, BR]

for c in (CL, BR, LU):
    c.is_forte_full = lambda: True
    c.is_mouse_forte_full = lambda: True
    c.is_e_forte_full = lambda: True
    c.resonance_available = lambda: True
    c.liberation_available = lambda: True
    c.is_con_full = lambda: True
    c.get_current_con = lambda: 1.0
    c.check_combat = lambda: None
    c.sleep = lambda sec, check_combat=True: time.sleep(max(0.0, sec))
CL.is_current_char = True

print('--- 启动轴 ---')
t = time.time()
try:
    CL.perform_opener(CL)
except Exception as e:
    import traceback
    traceback.print_exc()
open_wall = time.time() - t
mark_after_open = len(LOG)

print(f'\n启动轴+第一圈 墙钟 {open_wall:.2f}s')
print('时间轴(前 40 条):')
for ts, w in LOG[:40]:
    print(f'  {ts:6.2f} {w}')

LOG.clear()
T0 = time.time()
print('\n--- 循环轴单独一圈 ---')
t = time.time()
try:
    CL.perform_loop(CL)
except Exception as e:
    import traceback
    traceback.print_exc()
loop_wall = time.time() - t
print(f'循环轴一圈 墙钟 {loop_wall:.2f}s (视频里净 29.0s + 三段大招演出)')
sw = [(ts, w) for ts, w in LOG if w.startswith('key:') and w[-1] in '123']
print(f'切人键 {len(sw)} 次, 普攻 {sum(1 for _, w in LOG if w == "A")} 下, '
      f'长按 {sum(1 for _, w in LOG if w == "hold_down")} 次')
print('\n宏自己的时间轴:')
print(CL.macro_timeline())
